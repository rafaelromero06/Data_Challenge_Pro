import argparse
import json
import os
import sys
import time
import tracemalloc
from pathlib import Path

import pandas as pd
import psutil
from sqlalchemy import func, select, text

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from alembic import command
from alembic.config import Config

from frecuencias import db
from frecuencias.config import ARCHIVO_DEMANDA, CARPETA_DATOS
from frecuencias.ingesta import historico
from frecuencias.persistencia import ejecuciones
from frecuencias.persistencia.tablas import demanda, viaje

MINIMO_REGISTROS = 100_000


def _administrar(sentencia: str) -> None:
    with db.motor_destino().connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(sentencia))


def _cargar(motor, demanda_df: pd.DataFrame, viajes_df: pd.DataFrame, rutas: set[int]) -> dict:
    proceso = psutil.Process()
    tracemalloc.start()
    inicio = time.perf_counter()
    with motor.begin() as conn:
        e = ejecuciones.iniciar(conn, "ingesta", {"modo": "prueba_volumen"})
        resumen, _ = historico.cargar(conn, demanda_df, viajes_df, e["id"], rutas)
        ejecuciones.finalizar(conn, e["id"], "exitosa", registros_leidos=len(demanda_df) + len(viajes_df),
                              detalle=resumen)
    segundos = time.perf_counter() - inicio
    _, pico = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    with motor.connect() as conn:
        filas = {"demand_history": conn.execute(select(func.count()).select_from(demanda)).scalar_one(),
                 "trip_history": conn.execute(select(func.count()).select_from(viaje)).scalar_one()}
    registros = len(demanda_df) + len(viajes_df)
    memoria = proceso.memory_info()
    return {"registros": registros, "segundos": round(segundos, 1), "registros_por_segundo": round(registros / segundos),
            "pico_python_mb": round(pico / 2**20, 1), "rss_mb": round(memoria.rss / 2**20, 1),
            "pico_rss_mb": round(getattr(memoria, "peak_wset", memoria.rss) / 2**20, 1), "filas_en_base": filas,
            "rechazados": resumen["rechazados"], "problemas_calidad": resumen["problemas_calidad"]}


def main():
    parser = argparse.ArgumentParser(description="Prueba de volumen de la ingesta (RNF-003) en una base temporal")
    parser.add_argument("--base", default="frecuencias_volumen")
    parser.add_argument("--conservar", action="store_true", help="No elimina la base temporal al terminar")
    parser.add_argument("--salida", default="reportes/volumen_ingesta.json")
    args = parser.parse_args()
    demanda_df = pd.read_parquet(ARCHIVO_DEMANDA)
    viajes_df = pd.read_parquet(CARPETA_DATOS / "viajes.parquet")
    rutas = set(pd.read_parquet(CARPETA_DATOS / "rutas.parquet")["id_ruta"].astype(int))
    _administrar(f'DROP DATABASE IF EXISTS "{args.base}" WITH (FORCE)')
    _administrar(f'CREATE DATABASE "{args.base}"')
    original = os.environ.get("AI_DB_NAME")
    os.environ["AI_DB_NAME"] = args.base
    try:
        inicio = time.perf_counter()
        command.upgrade(Config(str(RAIZ / "alembic.ini")), "head")
        migraciones_s = round(time.perf_counter() - inicio, 1)
        motor = db.motor_destino()
        primera = _cargar(motor, demanda_df, viajes_df, rutas)
        repetida = _cargar(motor, demanda_df, viajes_df, rutas)
        motor.dispose()
    finally:
        if original is None:
            os.environ.pop("AI_DB_NAME", None)
        else:
            os.environ["AI_DB_NAME"] = original
        if not args.conservar:
            _administrar(f'DROP DATABASE IF EXISTS "{args.base}" WITH (FORCE)')
    informe = {"migraciones_s": migraciones_s, "primera_carga": primera, "carga_repetida": repetida,
               "carga_repetida_no_duplica": primera["filas_en_base"] == repetida["filas_en_base"],
               "cumple_rnf_003": primera["registros"] >= MINIMO_REGISTROS}
    Path(args.salida).parent.mkdir(parents=True, exist_ok=True)
    Path(args.salida).write_text(json.dumps(informe, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(informe, ensure_ascii=False))


if __name__ == "__main__":
    main()
