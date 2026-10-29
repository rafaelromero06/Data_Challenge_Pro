import argparse
import shutil

from sqlalchemy import select, update
from sqlalchemy.engine import Connection

from frecuencias.config import CARPETA_MODELOS
from frecuencias.inferencia import artefactos
from frecuencias.observabilidad import log
from frecuencias.persistencia.tablas import modelo

CARPETA_ARCHIVO = CARPETA_MODELOS / "archivo"
PREFIJO_ARCHIVO = "archivo/"
RETIRADOS_A_CONSERVAR = 2
PATRON_FASE1 = "demanda_h*.json"


def planificar(filas: list[dict], retirados_a_conservar: int = RETIRADOS_A_CONSERVAR) -> list[dict]:
    por_horizonte = {}
    for f in filas:
        por_horizonte.setdefault(f["horizonte_min"], []).append(f)
    plan = []
    for grupo in por_horizonte.values():
        retirados = sorted((f for f in grupo if f["estado"] == "retirado"),
                           key=lambda f: f["activado_en"] or f["registrado_en"], reverse=True)
        archivables = [f for f in grupo if f["estado"] == "rechazado"] + retirados[retirados_a_conservar:]
        plan += [f for f in archivables if not f["artefacto"].startswith(PREFIJO_ARCHIVO)]
    ids = {f["id"] for f in plan}
    protegidos = {f["artefacto"] for f in filas if f["id"] not in ids}
    protegidos |= {v["artefacto"] for v in artefactos.leer_activos_locales().values()}
    return [f | {"protegido": f["artefacto"] in protegidos} for f in sorted(plan, key=lambda f: f["id"])]


def archivar(conn: Connection, retirados_a_conservar: int = RETIRADOS_A_CONSERVAR, simular: bool = False) -> list[dict]:
    filas = [dict(f) for f in conn.execute(select(
        modelo.c.id, modelo.c.horizonte_min, modelo.c.estado, modelo.c.artefacto, modelo.c.activado_en,
        modelo.c.registrado_en)).mappings()]
    resultado = []
    for f in planificar(filas, retirados_a_conservar):
        origen = CARPETA_MODELOS / f["artefacto"]
        base = {"model_registry_id": f["id"], "horizonte_min": f["horizonte_min"], "estado": f["estado"],
                "artefacto": f["artefacto"]}
        if f["protegido"]:
            resultado.append(base | {"accion": "conservado: otro registro o activos.json usa el mismo paquete"})
            continue
        if not origen.is_dir():
            resultado.append(base | {"accion": "sin archivos en disco"})
            continue
        if simular:
            resultado.append(base | {"accion": "se archivaria"})
            continue
        nuevo = f"{PREFIJO_ARCHIVO}{origen.name}"
        conn.execute(update(modelo).where(modelo.c.artefacto == f["artefacto"]).values(artefacto=nuevo))
        CARPETA_ARCHIVO.mkdir(parents=True, exist_ok=True)
        shutil.move(str(origen), str(CARPETA_ARCHIVO / origen.name))
        resultado.append(base | {"accion": "archivado", "artefacto_nuevo": nuevo})
    if resultado:
        log("retencion").info("retencion_aplicada", archivados=sum(r["accion"] == "archivado" for r in resultado),
                              revisados=len(resultado))
    return resultado


def archivar_fase1(simular: bool = False) -> list[str]:
    destino = CARPETA_ARCHIVO / "xgboost-fase1"
    archivos = sorted(CARPETA_MODELOS.glob(PATRON_FASE1))
    if not simular and archivos:
        destino.mkdir(parents=True, exist_ok=True)
        for archivo in archivos:
            shutil.move(str(archivo), str(destino / archivo.name))
    return [a.name for a in archivos]


def main():
    from frecuencias import db

    parser = argparse.ArgumentParser(description="Archiva paquetes de modelos rechazados o retirados antiguos")
    parser.add_argument("--conservar", type=int, default=RETIRADOS_A_CONSERVAR,
                        help="Retirados mas recientes que se conservan por horizonte para poder revertir")
    parser.add_argument("--simular", action="store_true", help="Solo muestra lo que se archivaria")
    parser.add_argument("--incluir-fase1", action="store_true", help="Archiva tambien los XGBoost de la fase 1")
    args = parser.parse_args()
    with db.motor_destino().begin() as conn:
        for fila in archivar(conn, args.conservar, args.simular):
            print(fila)
    if args.incluir_fase1:
        print({"fase1": archivar_fase1(args.simular), "simulado": args.simular})


if __name__ == "__main__":
    main()
