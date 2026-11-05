import argparse
import io
import time

import numpy as np
import pandas as pd
from psycopg2 import sql
from sqlalchemy import delete, select, text
from sqlalchemy.engine import Connection, Engine

from frecuencias.analitica.calidad import DISTANCIA_MAXIMA_RUTA_M, FUENTE_DEMANDA, problema
from frecuencias.analitica.carga import cargar_calendario
from frecuencias.analitica.features import agregar_calendario
from frecuencias.ingesta import historico
from frecuencias.ingesta.consultas import QUERY_LECTURAS_SEGMENTO, QUERY_SEGMENTOS, QUERY_TIMBRADAS_SEGMENTOS
from frecuencias.observabilidad import describir_error, log
from frecuencias.optimizacion.parametros import tipo_dia_de
from frecuencias.persistencia import ejecuciones
from frecuencias.persistencia.tablas import observacion_segmento, perfil_segmento, segmento, viaje
from frecuencias.resiliencia import reintentar
from frecuencias.tiempo import a_zona

RUTAS = (2, 16, 21)
VIAJES_POR_LOTE = 500
TIEMPO_MAXIMO_LOTE = "600s"
TOLERANCIA_SIMPLIFICACION = 0.000005
DIAS_PERFIL = 28
COLUMNAS_LECTURA = ["id", "idprogramacion", "id_ruta", "fecha", "latitud", "longitud", "subidas", "bajadas",
                    "acumulada_subida", "acumulada_bajada"]
ENTERAS_LECTURA = ["id", "idprogramacion", "id_ruta", "subidas", "bajadas", "acumulada_subida", "acumulada_bajada"]

ASIGNAR = """
CREATE TEMP TABLE asignacion_segmento ON COMMIT DROP AS
WITH lecturas AS (
    SELECT l.*, ST_SetSRID(ST_MakePoint(l.longitud, l.latitud), 4326) AS punto,
           SUM(l.subidas) OVER (PARTITION BY l.idprogramacion) AS subidas_viaje,
           SUM(l.bajadas) OVER (PARTITION BY l.idprogramacion) AS bajadas_viaje,
           SUM(l.subidas) OVER orden AS subidas_acumuladas,
           SUM(l.bajadas) OVER orden AS bajadas_acumuladas
    FROM lectura_segmento l
    WINDOW orden AS (PARTITION BY l.idprogramacion ORDER BY l.fecha, l.id ROWS UNBOUNDED PRECEDING)
)
SELECT l.id, l.idprogramacion, l.id_ruta, l.fecha, l.subidas, l.bajadas, l.acumulada_subida, l.acumulada_bajada,
       s.id_segmento, ST_Distance(s.geom::geography, l.punto::geography) AS distancia_m,
       CASE WHEN l.bajadas_viaje > 0
            THEN GREATEST(l.subidas_acumuladas - l.bajadas_acumuladas::numeric * l.subidas_viaje / l.bajadas_viaje, 0)
       END AS carga
FROM lecturas l
CROSS JOIN LATERAL (
    SELECT rs.id_segmento, rs.geom
    FROM ai_frecuencias.route_segment rs
    WHERE rs.id_ruta = l.id_ruta
    ORDER BY rs.geom <-> l.punto
    LIMIT 1
) s
"""

AGREGAR = """
SELECT idprogramacion, id_segmento,
       (date_trunc('hour', fecha) + floor(date_part('minute', fecha) / 15) * interval '15 minutes')
           AT TIME ZONE 'America/Bogota' AS franja,
       id_ruta, SUM(subidas)::int AS subidas, SUM(bajadas)::int AS bajadas,
       GREATEST((array_agg(acumulada_subida ORDER BY fecha DESC, id DESC))[1]
                - (array_agg(acumulada_bajada ORDER BY fecha DESC, id DESC))[1], 0)::bigint AS pasajeros,
       ROUND(AVG(carga)::numeric, 2) AS carga_media, ROUND(MAX(carga)::numeric, 2) AS carga_maxima,
       COUNT(*)::int AS lecturas, ROUND(MAX(distancia_m)::numeric, 1) AS distancia_max_m
FROM asignacion_segmento
WHERE distancia_m <= :distancia
GROUP BY idprogramacion, id_segmento, 3, id_ruta
"""

LEJANAS = """
SELECT idprogramacion, COUNT(*) AS lecturas, ROUND(MAX(distancia_m)::numeric, 0) AS distancia_max
FROM asignacion_segmento WHERE distancia_m > :distancia GROUP BY idprogramacion
"""

SIN_BAJADAS = "SELECT DISTINCT idprogramacion FROM asignacion_segmento WHERE carga IS NULL"


def cargar_catalogo(conn: Connection, fuente: Engine) -> int:
    with fuente.connect() as origen:
        segmentos = pd.read_sql(text(QUERY_SEGMENTOS), origen, params={"rutas": list(RUTAS)})
    segmentos["longitud_m"] = segmentos["longitud_m"].astype(float)
    segmentos["inicio_m"] = segmentos.groupby("id_ruta")["longitud_m"].cumsum() - segmentos["longitud_m"]
    filas = segmentos.assign(tolerancia=TOLERANCIA_SIMPLIFICACION).to_dict("records")
    conn.execute(text("""
        INSERT INTO ai_frecuencias.route_segment (id_segmento, id_config, id_ruta, nro_segmento, longitud_m, inicio_m, geom)
        VALUES (:id_segmento, :id_config, :id_ruta, :nro_segmento, :longitud_m, :inicio_m,
                ST_Simplify(ST_GeomFromText(:wkt, 4326), :tolerancia))
        ON CONFLICT (id_segmento) DO UPDATE SET id_config = EXCLUDED.id_config, id_ruta = EXCLUDED.id_ruta,
            nro_segmento = EXCLUDED.nro_segmento, longitud_m = EXCLUDED.longitud_m, inicio_m = EXCLUDED.inicio_m,
            geom = EXCLUDED.geom, cargado_en = now()
    """), filas)
    conn.execute(text("ANALYZE ai_frecuencias.route_segment"))
    return len(filas)


def _copiar_lecturas(conn: Connection, lecturas: pd.DataFrame) -> None:
    datos = lecturas[COLUMNAS_LECTURA].copy()
    for col in ENTERAS_LECTURA:
        datos[col] = pd.to_numeric(datos[col]).astype("Int64")
    buffer = io.StringIO()
    datos.to_csv(buffer, index=False, header=False)
    buffer.seek(0)
    columnas = sql.SQL(", ").join(map(sql.Identifier, COLUMNAS_LECTURA))
    with conn.connection.dbapi_connection.cursor() as cursor:
        cursor.execute("""
            CREATE TEMP TABLE lectura_segmento (
                id bigint, idprogramacion bigint, id_ruta integer, fecha timestamp, latitud double precision,
                longitud double precision, subidas integer, bajadas integer, acumulada_subida bigint,
                acumulada_bajada bigint) ON COMMIT DROP""")
        cursor.copy_expert(sql.SQL("COPY lectura_segmento ({}) FROM STDIN WITH (FORMAT csv)").format(columnas), buffer)


def procesar_lote(conn: Connection, lecturas: pd.DataFrame,
                  distancia_maxima: float = DISTANCIA_MAXIMA_RUTA_M) -> tuple[pd.DataFrame, list[dict]]:
    conn.execute(text(f"SET LOCAL statement_timeout = '{TIEMPO_MAXIMO_LOTE}'"))
    conn.execute(text("SET LOCAL jit = off"))
    _copiar_lecturas(conn, lecturas)
    conn.execute(text("ANALYZE lectura_segmento"))
    conn.execute(text(ASIGNAR))
    observaciones = pd.read_sql(text(AGREGAR), conn, params={"distancia": distancia_maxima})
    problemas = [problema("CAL-201", FUENTE_DEMANDA, {"idprogramacion": int(f.idprogramacion)}, int(f.lecturas),
                          f"{int(f.lecturas)} lecturas a mas de {distancia_maxima:.0f} m de la ruta "
                          f"(maximo {int(f.distancia_max)} m)")
                 for f in conn.execute(text(LEJANAS), {"distancia": distancia_maxima})]
    problemas += [problema("CAL-202", FUENTE_DEMANDA, {"idprogramacion": int(i)})
                  for (i,) in conn.execute(text(SIN_BAJADAS))]
    conn.execute(text("DROP TABLE asignacion_segmento"))
    conn.execute(text("DROP TABLE lectura_segmento"))
    return observaciones, problemas


def guardar_observaciones(conn: Connection, observaciones: pd.DataFrame, execution_id: int | None) -> int:
    if observaciones.empty:
        return 0
    df = observaciones.assign(execution_id=execution_id, cargado_en=pd.Timestamp.now(tz="UTC"))
    df["franja"] = pd.to_datetime(df["franja"], utc=True)
    return historico.upsert(conn, observacion_segmento, df, ["idprogramacion", "id_segmento", "franja"])


def viajes_del_rango(conn: Connection, desde: pd.Timestamp, hasta: pd.Timestamp) -> pd.DataFrame:
    consulta = (select(viaje.c.idprogramacion, viaje.c.id_ruta)
                .where(viaje.c.fechasalida >= a_zona(desde), viaje.c.fechasalida < a_zona(hasta),
                       ~viaje.c.cancelada, viaje.c.id_ruta.in_(RUTAS))
                .order_by(viaje.c.idprogramacion))
    return pd.read_sql(consulta, conn)


def leer_lecturas(fuente: Engine, viajes: pd.DataFrame) -> pd.DataFrame:
    def _leer():
        with fuente.connect() as origen:
            return pd.read_sql(text(QUERY_LECTURAS_SEGMENTO), origen,
                               params={"ids": viajes["idprogramacion"].astype(int).tolist()})
    lecturas, _ = reintentar(lambda: historico.FUENTE.llamar(_leer), intentos=historico.INTENTOS_FUENTE,
                             espera_inicial_s=5.0, excepciones=historico.ERRORES_TRANSITORIOS)
    return lecturas.merge(viajes, on="idprogramacion")


def actualizar(destino: Engine, fuente: Engine, desde: pd.Timestamp, hasta: pd.Timestamp,
               id_padre: int | None = None) -> dict:
    inicio = time.perf_counter()
    with destino.begin() as conn:
        e = ejecuciones.iniciar(conn, "ingesta", {"modo": "segmentos", "desde": str(desde), "hasta": str(hasta),
                                                  "ciclo": id_padre})
    try:
        with destino.begin() as conn:
            catalogo = cargar_catalogo(conn, fuente)
            viajes = viajes_del_rango(conn, desde, hasta)
        resumen = {"segmentos": catalogo, "viajes": len(viajes), "lecturas": 0, "observaciones": 0, "problemas": 0}
        for i in range(0, len(viajes), VIAJES_POR_LOTE):
            lote = viajes.iloc[i:i + VIAJES_POR_LOTE]
            lecturas = leer_lecturas(fuente, lote)
            with destino.begin() as conn:
                observaciones, problemas = procesar_lote(conn, lecturas)
                resumen["observaciones"] += guardar_observaciones(conn, observaciones, e["id"])
                resumen["problemas"] += historico.guardar_problemas(conn, problemas, e["id"])
            resumen["lecturas"] += len(lecturas)
        with destino.begin() as conn:
            resumen["perfil"] = recalcular_perfil(conn, hasta, e["id"])
            resumen["segundos"] = round(time.perf_counter() - inicio, 1)
            ejecuciones.finalizar(conn, e["id"], "exitosa", registros_leidos=resumen["lecturas"],
                                  marca_agua_desde=a_zona(desde), marca_agua_hasta=a_zona(hasta), detalle=resumen)
        log("segmentos").info("segmentos_actualizados", execution_id=e["id"], **{k: v for k, v in resumen.items()
                                                                                 if k != "perfil"})
        return {"execution_id": e["id"], **resumen}
    except Exception as error:
        with destino.begin() as conn:
            ejecuciones.finalizar(conn, e["id"], "fallida", error=describir_error(error, 2000))
        raise


def validar_contra_operador(destino: Engine, fuente: Engine, fecha: pd.Timestamp) -> dict:
    with destino.begin() as conn:
        cargar_catalogo(conn, fuente)
        viajes = viajes_del_rango(conn, fecha, fecha + pd.Timedelta(days=1))
    lecturas = leer_lecturas(fuente, viajes)
    with destino.begin() as conn:
        observaciones, _ = procesar_lote(conn, lecturas, distancia_maxima=float("inf"))
    with fuente.connect() as origen:
        operador = pd.read_sql(text(QUERY_TIMBRADAS_SEGMENTOS), origen,
                               params={"ids": viajes["idprogramacion"].astype(int).tolist()})
    propio = observaciones.groupby(["idprogramacion", "id_segmento"], as_index=False)[["subidas", "bajadas"]].sum()
    unido = propio.merge(operador, on=["idprogramacion", "id_segmento"], how="outer",
                         suffixes=("_propio", "_operador")).fillna(0)
    comunes = unido[unido["idprogramacion"].isin(operador["idprogramacion"])]
    return {
        "fecha": str(fecha.date()), "viajes": len(viajes), "viajes_en_timbradas_segmentos": operador["idprogramacion"].nunique(),
        "pares_viaje_segmento": len(comunes),
        "subidas_iguales": round(float((comunes["subidas_propio"] == comunes["subidas_operador"]).mean()), 4),
        "bajadas_iguales": round(float((comunes["bajadas_propio"] == comunes["bajadas_operador"]).mean()), 4),
        "subidas_propio": int(comunes["subidas_propio"].sum()), "subidas_operador": int(comunes["subidas_operador"].sum()),
        "correlacion_subidas": round(float(np.corrcoef(comunes["subidas_propio"], comunes["subidas_operador"])[0, 1]), 4),
    }


def tipos_dia(fechas: pd.Series, calendario) -> pd.Series:
    dias = pd.DataFrame({"ts": pd.to_datetime(fechas.unique())})
    dias["dia_semana"] = dias["ts"].dt.dayofweek
    dias = agregar_calendario(dias, calendario)
    return fechas.map(dict(zip(dias["ts"], dias["tipo_dia"].map(tipo_dia_de))))


def construir_perfil(observaciones: pd.DataFrame, calendario) -> pd.DataFrame:
    if observaciones.empty:
        return pd.DataFrame()
    obs = observaciones.copy()
    obs["local"] = pd.to_datetime(obs["franja"], utc=True).dt.tz_convert("America/Bogota").dt.tz_localize(None)
    por_paso = obs.sort_values("local").groupby(["idprogramacion", "id_segmento", "id_ruta"], as_index=False).agg(
        local=("local", "first"), subidas=("subidas", "sum"), bajadas=("bajadas", "sum"),
        pasajeros=("pasajeros", "max"), carga_media=("carga_media", "mean"), carga_maxima=("carga_maxima", "max"))
    por_paso["hora"] = por_paso["local"].dt.hour
    por_paso["tipo_dia"] = tipos_dia(por_paso["local"].dt.normalize(), calendario)
    claves = ["id_ruta", "tipo_dia", "hora"]
    viajes_hora = por_paso.groupby(claves)["idprogramacion"].nunique().rename("viajes_hora")
    grupo = por_paso.groupby(claves + ["id_segmento"])
    perfil = grupo.agg(con_lectura=("idprogramacion", "nunique"), subidas_suma=("subidas", "sum"),
                       subidas_max=("subidas", "max"), subidas_min_obs=("subidas", "min"),
                       bajadas_suma=("bajadas", "sum"), bajadas_max=("bajadas", "max"),
                       bajadas_min_obs=("bajadas", "min"), pasajeros_prom=("pasajeros", "mean"),
                       pasajeros_min=("pasajeros", "min"), pasajeros_max=("pasajeros", "max"),
                       carga_prom=("carga_media", "mean"), carga_max=("carga_maxima", "max"))
    perfil["carga_p90"] = grupo["carga_maxima"].quantile(0.9)
    perfil = perfil.reset_index().merge(viajes_hora.reset_index(), on=claves)
    completo = perfil["con_lectura"] >= perfil["viajes_hora"]
    perfil["nro_viajes"] = perfil["viajes_hora"]
    perfil["subidas_prom"] = perfil["subidas_suma"] / perfil["viajes_hora"]
    perfil["bajadas_prom"] = perfil["bajadas_suma"] / perfil["viajes_hora"]
    perfil["subidas_min"] = np.where(completo, perfil["subidas_min_obs"], 0)
    perfil["bajadas_min"] = np.where(completo, perfil["bajadas_min_obs"], 0)
    total = perfil.groupby(claves)["subidas_suma"].transform("sum")
    perfil["cuota_subidas"] = np.where(total > 0, perfil["subidas_suma"] / total.where(total > 0, 1), 0)
    columnas = [c.name for c in perfil_segmento.columns
                if c.name not in ("periodo_desde", "periodo_hasta", "execution_id", "calculado_en")]
    return perfil[columnas].round(6)


def recalcular_perfil(conn: Connection, hasta: pd.Timestamp, execution_id: int | None,
                      dias: int = DIAS_PERFIL) -> dict:
    desde = hasta - pd.Timedelta(days=dias)
    observaciones = pd.read_sql(select(observacion_segmento).where(
        observacion_segmento.c.franja >= a_zona(desde), observacion_segmento.c.franja < a_zona(hasta)), conn)
    perfil = construir_perfil(observaciones, cargar_calendario())
    if perfil.empty:
        return {"filas": 0}
    conn.execute(delete(perfil_segmento).where(perfil_segmento.c.id_ruta.in_(perfil["id_ruta"].unique().tolist())))
    perfil = perfil.assign(periodo_desde=desde.date(), periodo_hasta=(hasta - pd.Timedelta(days=1)).date(),
                           execution_id=execution_id)
    for col in ("subidas_min", "subidas_max", "bajadas_min", "bajadas_max", "pasajeros_min", "pasajeros_max",
                "nro_viajes", "hora"):
        perfil[col] = perfil[col].astype(int)
    filas = perfil.replace({np.nan: None}).to_dict("records")
    conn.execute(perfil_segmento.insert(), filas)
    return {"filas": len(filas), "desde": str(desde.date()), "hasta": str(hasta.date()),
            "observaciones": len(observaciones)}


def refrescar_perfil(destino: Engine, hasta: pd.Timestamp) -> dict:
    with destino.begin() as conn:
        e = ejecuciones.iniciar(conn, "ingesta", {"modo": "perfil_segmentos", "hasta": str(hasta)})
    try:
        with destino.begin() as conn:
            resumen = recalcular_perfil(conn, hasta, e["id"])
            ejecuciones.finalizar(conn, e["id"], "exitosa", registros_leidos=resumen.get("observaciones", 0),
                                  detalle=resumen)
        return {"execution_id": e["id"], **resumen}
    except Exception as error:
        with destino.begin() as conn:
            ejecuciones.finalizar(conn, e["id"], "fallida", error=describir_error(error, 2000))
        raise


def main():
    from frecuencias import db

    parser = argparse.ArgumentParser(description="Asigna las lecturas del sensor a los segmentos de cada ruta")
    parser.add_argument("--desde", help="Fecha local AAAA-MM-DD")
    parser.add_argument("--hasta", help="Fecha local AAAA-MM-DD (excluida)")
    parser.add_argument("--validar", help="Compara un dia (AAAA-MM-DD) con timbradas_segmentos del operador")
    parser.add_argument("--solo-perfil", action="store_true",
                        help="Recalcula el perfil con las observaciones guardadas, sin leer la base del operador")
    args = parser.parse_args()
    if args.validar:
        print(validar_contra_operador(db.motor_destino(), db.motor_fuente(), pd.Timestamp(args.validar)))
        return
    if args.solo_perfil:
        if not args.hasta:
            parser.error("Indique --hasta con --solo-perfil")
        print(refrescar_perfil(db.motor_destino(), pd.Timestamp(args.hasta)))
        return
    if not (args.desde and args.hasta):
        parser.error("Indique --desde y --hasta, o --validar")
    resultado = actualizar(db.motor_destino(), db.motor_fuente(), pd.Timestamp(args.desde), pd.Timestamp(args.hasta))
    print(resultado)


if __name__ == "__main__":
    main()
