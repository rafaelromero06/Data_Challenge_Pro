import argparse
import io

import pandas as pd
from psycopg2 import sql
from sqlalchemy import Float, func, insert, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import InterfaceError, OperationalError

from frecuencias.analitica.calidad import validar_demanda, validar_viajes
from frecuencias.config import ARCHIVO_DEMANDA, CARPETA_DATOS, NOMBRES_RUTAS, PASO_MIN, ZONA_HORARIA
from frecuencias.ingesta.consultas import QUERY_CONTEO, QUERY_VIAJES_RANGO
from frecuencias.observabilidad import describir_error
from frecuencias.persistencia.tablas import demanda, problema_calidad, viaje
from frecuencias.resiliencia import Interruptor, reintentar

LOTE = 5000
MARGEN_VIAJES = pd.Timedelta(hours=48)
INTENTOS_FUENTE = 3
FUENTE = Interruptor("base_reto", fallos_maximos=3, espera_s=300.0)
ERRORES_TRANSITORIOS = (OperationalError, InterfaceError)
ENTERAS = {"id_ruta", "nro_viajes", "nro_vehiculos", "lecturas", "lecturas_negativas", "execution_id",
           "idprogramacion"}
COLUMNAS_DEMANDA = ["id_ruta", "ts", "subidas", "bajadas", "nro_viajes", "nro_vehiculos", "lecturas",
                    "lecturas_negativas", "max_subida_lectura", "valido", "motivo_invalido"]
COLUMNAS_VIAJES = ["idprogramacion", "id_ruta", "idvehiculo", "fechasalida", "fecharegistro", "fechainicio",
                   "fechafin", "cancelada", "finalizado", "excede_plazo_salida", "numeropasajeros",
                   "numerobajadas", "distancia_recorrida"]
FECHAS_VIAJE = ["fechasalida", "fecharegistro", "fechainicio", "fechafin"]


def _con_zona(serie: pd.Series) -> pd.Series:
    serie = pd.to_datetime(serie)
    return serie.dt.tz_localize(ZONA_HORARIA) if serie.dt.tz is None else serie.dt.tz_convert(ZONA_HORARIA)


def upsert(conn: Connection, tabla, df: pd.DataFrame, llave: list[str]) -> int:
    if df.empty:
        return 0
    df = df.copy()
    for col in ENTERAS & set(df.columns):
        df[col] = df[col].astype("Int64")
    columnas = list(df.columns)
    destino = sql.Identifier(tabla.schema, tabla.name)
    temporal = sql.Identifier(f"carga_{tabla.name}")
    lista = sql.SQL(", ").join(map(sql.Identifier, columnas))
    actualizar = sql.SQL(", ").join(
        sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(c)) for c in columnas if c not in llave)
    buffer = io.StringIO()
    df.to_csv(buffer, index=False, header=False, na_rep="")
    buffer.seek(0)
    with conn.connection.dbapi_connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE TEMP TABLE IF NOT EXISTS {} (LIKE {}) ON COMMIT DROP").format(temporal, destino))
        cursor.execute(sql.SQL("TRUNCATE {}").format(temporal))
        cursor.copy_expert(sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv, NULL '')").format(temporal, lista), buffer)
        cursor.execute(sql.SQL("INSERT INTO {} ({}) SELECT {} FROM {} ON CONFLICT ({}) DO UPDATE SET {}").format(
            destino, lista, lista, temporal, sql.SQL(", ").join(map(sql.Identifier, llave)), actualizar))
    return len(df)


def guardar_demanda(conn: Connection, validado: pd.DataFrame, execution_id: int | None) -> int:
    df = validado[COLUMNAS_DEMANDA].copy()
    df["ts"] = _con_zona(df["ts"])
    df["execution_id"] = execution_id
    df["cargado_en"] = pd.Timestamp.now(tz="UTC")
    return upsert(conn, demanda, df, ["id_ruta", "ts"])


def guardar_viajes(conn: Connection, validado: pd.DataFrame, execution_id: int | None) -> int:
    df = validado.copy()
    for col in ["cancelada", "finalizado"]:
        df[col] = df[col].astype(str).str.upper().eq("S")
    df["excede_plazo_salida"] = df["excedeplazosalida"].astype(str).str.upper().eq("S")
    for col in FECHAS_VIAJE:
        df[col] = _con_zona(df[col])
    df = df[COLUMNAS_VIAJES].assign(execution_id=execution_id, cargado_en=pd.Timestamp.now(tz="UTC"))
    return upsert(conn, viaje, df, ["idprogramacion"])


def guardar_problemas(conn: Connection, problemas: list[dict], execution_id: int) -> int:
    for inicio in range(0, len(problemas), LOTE):
        bloque = [{**p, "execution_id": execution_id} for p in problemas[inicio:inicio + LOTE]]
        conn.execute(insert(problema_calidad).values(bloque))
    return len(problemas)


def cargar(conn: Connection, demanda_df: pd.DataFrame, viajes_df: pd.DataFrame | None, execution_id: int,
           rutas_validas: set[int] | None) -> tuple[dict, list[dict]]:
    validado, problemas = validar_demanda(demanda_df, rutas_validas)
    resumen = {"franjas_leidas": len(demanda_df), "franjas_guardadas": guardar_demanda(conn, validado, execution_id),
               "franjas_invalidas": int((~validado["valido"]).sum())}
    if viajes_df is not None and not viajes_df.empty:
        viajes_ok, problemas_viajes = validar_viajes(viajes_df, rutas_validas)
        problemas += problemas_viajes
        resumen |= {"viajes_leidos": len(viajes_df), "viajes_guardados": guardar_viajes(conn, viajes_ok, execution_id)}
    resumen["problemas_calidad"] = guardar_problemas(conn, problemas, execution_id)
    resumen["rechazados"] = sum(1 for p in problemas if p["tratamiento"] == "cuarentena")
    return resumen, problemas


def _a_local(serie: pd.Series) -> pd.Series:
    return pd.to_datetime(serie, utc=True).dt.tz_convert(ZONA_HORARIA).dt.tz_localize(None)


def leer_demanda(conn: Connection, desde=None, hasta=None, solo_validas: bool = True) -> pd.DataFrame:
    columnas = [demanda.c.id_ruta, demanda.c.ts] + [
        demanda.c[c].cast(Float()).label(c) for c in COLUMNAS_DEMANDA[2:9]]
    consulta = select(*columnas, demanda.c.valido)
    if solo_validas:
        consulta = consulta.where(demanda.c.valido)
    if desde is not None:
        consulta = consulta.where(demanda.c.ts >= pd.Timestamp(desde).tz_localize(ZONA_HORARIA))
    if hasta is not None:
        consulta = consulta.where(demanda.c.ts < pd.Timestamp(hasta).tz_localize(ZONA_HORARIA))
    df = pd.read_sql(consulta.order_by(demanda.c.id_ruta, demanda.c.ts), conn)
    df["ts"] = _a_local(df["ts"])
    for col in ["nro_viajes", "nro_vehiculos", "lecturas", "lecturas_negativas"]:
        df[col] = df[col].fillna(0).astype("int64")
    df["max_subida_lectura"] = df["max_subida_lectura"].fillna(0).astype("int64")
    return df.drop(columns="valido") if solo_validas else df


def leer_viajes(conn: Connection, desde=None, hasta=None) -> pd.DataFrame:
    consulta = select(viaje)
    if desde is not None:
        consulta = consulta.where(viaje.c.fechasalida >= pd.Timestamp(desde).tz_localize(ZONA_HORARIA))
    if hasta is not None:
        consulta = consulta.where(viaje.c.fechasalida < pd.Timestamp(hasta).tz_localize(ZONA_HORARIA))
    df = pd.read_sql(consulta.order_by(viaje.c.fechasalida), conn)
    for col in FECHAS_VIAJE:
        df[col] = _a_local(df[col])
    for col in ["numeropasajeros", "numerobajadas", "distancia_recorrida"]:
        df[col] = df[col].astype(float)
    return df


def ultima_franja(conn: Connection) -> pd.Timestamp | None:
    valor = conn.execute(select(func.max(demanda.c.ts))).scalar_one()
    return None if valor is None else pd.Timestamp(valor).tz_convert(ZONA_HORARIA).tz_localize(None)


def contar(conn: Connection) -> dict:
    return {"franjas": conn.execute(select(func.count()).select_from(demanda)).scalar_one(),
            "viajes": conn.execute(select(func.count()).select_from(viaje)).scalar_one()}


def _leer_fuente(motor: Engine, desde: pd.Timestamp, hasta: pd.Timestamp) -> tuple[pd.DataFrame, list]:
    with motor.connect() as conn:
        viajes = pd.read_sql(text(QUERY_VIAJES_RANGO), conn,
                             params={"desde": (desde - MARGEN_VIAJES).to_pydatetime(), "hasta": hasta.to_pydatetime()})
        ids = viajes["idprogramacion"].astype(int).tolist()
        partes = [pd.read_sql(text(QUERY_CONTEO), conn, params={"ids": ids[i:i + LOTE]})
                  for i in range(0, len(ids), LOTE)]
    return viajes, partes


def extraer_rango(motor: Engine, desde: pd.Timestamp, hasta: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    (viajes, partes), _ = reintentar(lambda: FUENTE.llamar(_leer_fuente, motor, desde, hasta),
                                     intentos=INTENTOS_FUENTE, espera_inicial_s=5.0,
                                     excepciones=ERRORES_TRANSITORIOS)
    columnas = ["id_ruta", "ts", "subidas", "bajadas", "nro_viajes", "nro_vehiculos", "lecturas",
                "lecturas_negativas", "max_subida_lectura"]
    conteo = pd.concat(partes) if partes else pd.DataFrame(columns=columnas)
    franjas = conteo.groupby(["id_ruta", "ts"], as_index=False).agg(
        subidas=("subidas", "sum"), bajadas=("bajadas", "sum"), nro_viajes=("nro_viajes", "sum"),
        nro_vehiculos=("nro_vehiculos", "sum"), lecturas=("lecturas", "sum"),
        lecturas_negativas=("lecturas_negativas", "sum"), max_subida_lectura=("max_subida_lectura", "max"))
    franjas["ts"] = pd.to_datetime(franjas["ts"])
    franjas = franjas[(franjas["ts"] >= desde) & (franjas["ts"] < hasta)].reset_index(drop=True)
    viajes = viajes[(viajes["fechasalida"] >= desde) & (viajes["fechasalida"] < hasta)].reset_index(drop=True)
    return franjas, viajes


def leer_archivos(desde: pd.Timestamp | None, hasta: pd.Timestamp | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    demanda_df = pd.read_parquet(ARCHIVO_DEMANDA)
    viajes_df = pd.read_parquet(CARPETA_DATOS / "viajes.parquet")
    if desde is not None:
        demanda_df = demanda_df[demanda_df["ts"] >= desde]
        viajes_df = viajes_df[viajes_df["fechasalida"] >= desde]
    if hasta is not None:
        demanda_df = demanda_df[demanda_df["ts"] < hasta]
        viajes_df = viajes_df[viajes_df["fechasalida"] < hasta]
    return demanda_df.reset_index(drop=True), viajes_df.reset_index(drop=True)


def _guardar_carga(motor_destino: Engine, demanda_df: pd.DataFrame, viajes_df: pd.DataFrame, detalle: dict) -> dict:
    from frecuencias.persistencia import ejecuciones

    with motor_destino.begin() as conn:
        e = ejecuciones.iniciar(conn, "ingesta", detalle)
    try:
        with motor_destino.begin() as conn:
            resumen, _ = cargar(conn, demanda_df, viajes_df, e["id"], set(NOMBRES_RUTAS))
            marca = None
            if not demanda_df.empty:
                ultima = pd.Timestamp(demanda_df["ts"].max())
                marca = (ultima + pd.Timedelta(minutes=PASO_MIN)).tz_localize(ZONA_HORARIA)
            ejecuciones.finalizar(conn, e["id"], "exitosa", registros_leidos=resumen["franjas_leidas"],
                                  registros_rechazados=resumen["rechazados"], marca_agua_hasta=marca, detalle=resumen)
    except Exception as error:
        with motor_destino.begin() as conn:
            ejecuciones.finalizar(conn, e["id"], "fallida", error=describir_error(error, 2000))
        raise
    return {"execution_id": e["id"], **resumen}


def carga_inicial(motor_destino: Engine, hasta: pd.Timestamp | None = None) -> dict:
    demanda_df, viajes_df = leer_archivos(None, hasta)
    return _guardar_carga(motor_destino, demanda_df, viajes_df, {"modo": "carga_inicial", "origen": "datos/"})


def recargar_rango(motor_destino: Engine, desde: pd.Timestamp, hasta: pd.Timestamp, origen: str = "archivos",
                   motor_fuente: Engine | None = None) -> dict:
    if hasta <= desde:
        raise ValueError("La fecha --hasta debe ser posterior a --desde.")
    demanda_df, viajes_df = extraer_rango(motor_fuente, desde, hasta) if origen == "base" else leer_archivos(desde, hasta)
    detalle = {"modo": "recarga_rango", "origen": origen, "desde": str(desde), "hasta": str(hasta)}
    return _guardar_carga(motor_destino, demanda_df, viajes_df, detalle)


def main():
    from frecuencias import db

    parser = argparse.ArgumentParser(description="Carga o recarga el historico analitico en ai_frecuencias")
    parser.add_argument("--desde", help="Fecha local (AAAA-MM-DD); con --hasta, recarga solo ese rango")
    parser.add_argument("--hasta", help="Fecha local (AAAA-MM-DD) hasta la que se carga; por defecto todo")
    parser.add_argument("--origen", choices=["archivos", "base"], default="archivos",
                        help="En una recarga por rango: datos/ o la base del operador")
    argumentos = parser.parse_args()
    hasta = pd.Timestamp(argumentos.hasta) if argumentos.hasta else None
    if argumentos.desde:
        if hasta is None:
            parser.error("Indique --hasta junto con --desde")
        fuente = db.motor_fuente() if argumentos.origen == "base" else None
        print(recargar_rango(db.motor_destino(), pd.Timestamp(argumentos.desde), hasta, argumentos.origen, fuente))
        return
    if argumentos.origen == "base":
        parser.error("Con --origen base indique --desde y --hasta")
    print(carga_inicial(db.motor_destino(), hasta))


if __name__ == "__main__":
    main()
