import time

import pandas as pd
from sqlalchemy import text

from frecuencias.config import CARPETA_DATOS
from frecuencias.db import motor_fuente
from frecuencias.ingesta.consultas import (
    QUERY_CONTEO,
    QUERY_ESTACIONALIDADES,
    QUERY_NOVEDADES,
    QUERY_RUTAS,
    QUERY_VIAJES,
)

CARPETA_MES = CARPETA_DATOS / "conteo_mensual"


def _consultar_conteo(conn, ids: list) -> pd.DataFrame:
    try:
        return pd.read_sql(text(QUERY_CONTEO), conn, params={"ids": ids})
    except Exception as e:
        if "statement timeout" not in str(e) or len(ids) < 200:
            raise
        conn.rollback()
        mitad = len(ids) // 2
        print(f"    timeout con {len(ids)} viajes, se parte en dos", flush=True)
        return pd.concat([_consultar_conteo(conn, ids[:mitad]), _consultar_conteo(conn, ids[mitad:])])


def main():
    CARPETA_MES.mkdir(parents=True, exist_ok=True)
    engine = motor_fuente()

    print("Viajes (progvehiculos)...", flush=True)
    with engine.connect() as conn:
        viajes = pd.read_sql(text(QUERY_VIAJES), conn)
    viajes.to_parquet(CARPETA_DATOS / "viajes.parquet", index=False)
    print(f"  {len(viajes):,} viajes, {viajes['fechasalida'].min()} a {viajes['fechasalida'].max()}", flush=True)

    with engine.connect() as conn:
        pd.read_sql(text(QUERY_ESTACIONALIDADES), conn).to_parquet(
            CARPETA_DATOS / "estacionalidades.parquet", index=False)
        pd.read_sql(text(QUERY_NOVEDADES), conn).astype({"hora_inicio": str, "hora_fin": str}).to_parquet(
            CARPETA_DATOS / "novedades.parquet", index=False)
        pd.read_sql(text(QUERY_RUTAS), conn).to_parquet(CARPETA_DATOS / "rutas.parquet", index=False)

    meses = viajes["fechasalida"].dt.to_period("M")
    for mes in sorted(meses.unique()):
        archivo = CARPETA_MES / f"{mes}.parquet"
        if archivo.exists():
            continue
        ids = viajes.loc[meses == mes, "idprogramacion"].astype(int).tolist()
        t0 = time.perf_counter()
        with engine.connect() as conn:
            df = _consultar_conteo(conn, ids)
        df.to_parquet(archivo, index=False)
        print(f"  {mes}: {len(ids):,} viajes -> {len(df):,} franjas, "
              f"{df['subidas'].sum():,.0f} subidas ({time.perf_counter() - t0:.0f}s)", flush=True)

    partes = pd.concat([pd.read_parquet(a) for a in sorted(CARPETA_MES.glob("*.parquet"))])
    demanda = partes.groupby(["id_ruta", "ts"], as_index=False).agg(
        subidas=("subidas", "sum"), bajadas=("bajadas", "sum"),
        nro_viajes=("nro_viajes", "sum"), nro_vehiculos=("nro_vehiculos", "sum"),
        lecturas=("lecturas", "sum"), lecturas_negativas=("lecturas_negativas", "sum"),
        max_subida_lectura=("max_subida_lectura", "max"),
    )
    demanda.to_parquet(CARPETA_DATOS / "demanda_15min.parquet", index=False)
    print(f"Listo: {len(demanda):,} franjas, {demanda['ts'].min()} a {demanda['ts'].max()}, "
          f"{demanda['subidas'].sum():,.0f} subidas -> {CARPETA_DATOS}", flush=True)


if __name__ == "__main__":
    main()
