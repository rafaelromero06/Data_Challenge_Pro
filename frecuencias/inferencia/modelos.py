from frecuencias.analitica.features import FEATURES
from frecuencias.config import FEATURE_VERSION
from frecuencias.inferencia import artefactos

COMANDO_ENTRENAMIENTO = "python -m frecuencias.inferencia.entrenamiento"


def _validar(paquete: artefactos.Paquete) -> artefactos.Paquete:
    if paquete.meta["feature_version"] != FEATURE_VERSION or paquete.meta["features"] != FEATURES:
        raise RuntimeError(
            f"{paquete.carpeta.name} se entreno con variables '{paquete.meta['feature_version']}' y el "
            f"codigo usa '{FEATURE_VERSION}'. Reentrene: {COMANDO_ENTRENAMIENTO}")
    return paquete


def desde_registro(activos: dict[int, dict]) -> dict[int, artefactos.Paquete]:
    return {h: _validar(artefactos.cargar(m["artefacto"], m["artefacto_sha256"], m["id"])) for h, m in activos.items()}


def desde_archivo() -> dict[int, artefactos.Paquete]:
    activos = artefactos.leer_activos_locales()
    return {h: _validar(artefactos.cargar(v["artefacto"], v["sha256"], v.get("model_registry_id")))
            for h, v in activos.items()}


def cargar_modelos(motor=None) -> dict[int, artefactos.Paquete]:
    paquetes = {}
    if motor is not None:
        from frecuencias.persistencia import registro_modelos
        with motor.connect() as conn:
            paquetes = desde_registro(registro_modelos.activos(conn))
    if not paquetes:
        paquetes = desde_archivo()
    if not paquetes:
        raise RuntimeError(f"No hay modelos activos. Entrene primero: {COMANDO_ENTRENAMIENTO}")
    return paquetes
