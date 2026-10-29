import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from frecuencias.config import CARPETA_MODELOS
from frecuencias.inferencia import cuantiles as cq

ARCHIVO_PUNTO = "punto.txt"
ARCHIVOS_CUANTIL = {0.1: "q10.txt", 0.5: "q50.txt", 0.9: "q90.txt"}
ARCHIVO_META = "meta.json"
ARCHIVO_MANIFIESTO = "manifiesto.json"
ARCHIVO_ACTIVOS = CARPETA_MODELOS / "activos.json"


class ArtefactoAlterado(RuntimeError):
    pass


def sha256(ruta: Path) -> str:
    return hashlib.sha256(Path(ruta).read_bytes()).hexdigest()


@dataclass
class Paquete:
    carpeta: Path
    sha: str
    meta: dict
    punto: lgb.Booster
    cuantiles: dict
    model_registry_id: int | None = None
    features: list = field(init=False)

    def __post_init__(self):
        self.features = self.meta["features"]

    @property
    def horizonte(self) -> int:
        return int(self.meta["horizonte_min"])

    def predecir(self, X: pd.DataFrame) -> dict:
        datos = X[self.features]
        puntual = np.clip(self.punto.predict(datos), 0, None)
        P = cq.predecir(self.cuantiles, datos)
        inferior, superior = cq.intervalo(P, self.meta["intervalo"]["ajuste_cqr"], puntual)
        contribuciones = self.punto.predict(datos, pred_contrib=True)
        return {"puntual": puntual, "inferior": inferior, "superior": superior, "contribuciones": contribuciones}


def ruta_relativa(carpeta: Path) -> str:
    return Path(carpeta).resolve().relative_to(CARPETA_MODELOS.resolve()).as_posix()


def guardar(carpeta: Path, punto: lgb.Booster, cuantiles: dict, meta: dict) -> tuple[Path, str]:
    carpeta.mkdir(parents=True, exist_ok=True)
    punto.save_model(str(carpeta / ARCHIVO_PUNTO))
    for q, modelo in cuantiles.items():
        modelo.save_model(str(carpeta / ARCHIVOS_CUANTIL[q]))
    (carpeta / ARCHIVO_META).write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    nombres = [ARCHIVO_PUNTO, *ARCHIVOS_CUANTIL.values(), ARCHIVO_META]
    manifiesto = {n: sha256(carpeta / n) for n in nombres}
    (carpeta / ARCHIVO_MANIFIESTO).write_text(json.dumps(manifiesto, sort_keys=True, indent=1), encoding="utf-8")
    return carpeta, sha256(carpeta / ARCHIVO_MANIFIESTO)


def cargar(carpeta, sha_esperado: str | None = None, model_registry_id: int | None = None) -> Paquete:
    carpeta = Path(carpeta)
    if not carpeta.is_absolute():
        carpeta = CARPETA_MODELOS / carpeta
    sha = sha256(carpeta / ARCHIVO_MANIFIESTO)
    if sha_esperado and sha != sha_esperado:
        raise ArtefactoAlterado(f"La huella de {carpeta.name} no coincide con la registrada.")
    manifiesto = json.loads((carpeta / ARCHIVO_MANIFIESTO).read_text(encoding="utf-8"))
    for nombre, huella in manifiesto.items():
        if sha256(carpeta / nombre) != huella:
            raise ArtefactoAlterado(f"El archivo {nombre} de {carpeta.name} fue modificado.")
    meta = json.loads((carpeta / ARCHIVO_META).read_text(encoding="utf-8"))
    punto = lgb.Booster(model_file=str(carpeta / ARCHIVO_PUNTO))
    modelos_q = {q: lgb.Booster(model_file=str(carpeta / n)) for q, n in ARCHIVOS_CUANTIL.items()}
    return Paquete(carpeta=carpeta, sha=sha, meta=meta, punto=punto, cuantiles=modelos_q,
                   model_registry_id=model_registry_id)


def leer_activos_locales() -> dict[int, dict]:
    if not ARCHIVO_ACTIVOS.exists():
        return {}
    return {int(h): v for h, v in json.loads(ARCHIVO_ACTIVOS.read_text(encoding="utf-8")).items()}


def marcar_activo_local(horizonte: int, carpeta: Path, sha: str, model_registry_id: int | None) -> None:
    activos = {str(h): v for h, v in leer_activos_locales().items()}
    activos[str(horizonte)] = {"artefacto": ruta_relativa(carpeta), "sha256": sha, "model_registry_id": model_registry_id}
    ARCHIVO_ACTIVOS.write_text(json.dumps(activos, indent=2, sort_keys=True), encoding="utf-8")
