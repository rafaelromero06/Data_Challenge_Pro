from datetime import datetime

import pandas as pd
from sqlalchemy import Date, cast, func, insert, select
from sqlalchemy.engine import Connection

from frecuencias.config import ZONA_HORARIA
from frecuencias.persistencia.consultas import paginar
from frecuencias.persistencia.tablas import alerta, metrica_monitoreo, prediccion
from frecuencias.tiempo import a_zona

COLUMNAS = {c.name for c in metrica_monitoreo.columns} - {"id", "calculado_en"}


def guardar(conn: Connection, filas: list[dict], execution_id: int | None) -> int:
    if not filas:
        return 0
    registros = []
    for f in filas:
        r = {k: f.get(k) for k in COLUMNAS}
        r["execution_id"] = execution_id
        r["ventana_desde"] = a_zona(r["ventana_desde"])
        r["ventana_hasta"] = a_zona(r["ventana_hasta"])
        registros.append(r)
    conn.execute(insert(metrica_monitoreo), registros)
    return len(registros)


def ultimos(conn: Connection) -> list[dict]:
    consulta = (select(metrica_monitoreo)
                .distinct(metrica_monitoreo.c.metrica, metrica_monitoreo.c.alcance)
                .order_by(metrica_monitoreo.c.metrica, metrica_monitoreo.c.alcance,
                          metrica_monitoreo.c.calculado_en.desc(), metrica_monitoreo.c.id.desc()))
    return [dict(f) for f in conn.execute(consulta).mappings()]


def historial(conn: Connection, metrica: str | None = None, alcance: str | None = None,
              desde: datetime | None = None, hasta: datetime | None = None, pagina: int = 1,
              tamano: int = 50) -> tuple[list[dict], int]:
    consulta = select(metrica_monitoreo)
    if metrica:
        consulta = consulta.where(metrica_monitoreo.c.metrica == metrica)
    if alcance:
        consulta = consulta.where(metrica_monitoreo.c.alcance == alcance)
    if desde is not None:
        consulta = consulta.where(metrica_monitoreo.c.calculado_en >= desde)
    if hasta is not None:
        consulta = consulta.where(metrica_monitoreo.c.calculado_en < hasta)
    return paginar(conn, consulta, metrica_monitoreo, "calculado_en", True, pagina, tamano)


def predicciones_por_dia(conn: Connection, desde: pd.Timestamp, hasta: pd.Timestamp) -> pd.Series:
    dia = cast(func.timezone(ZONA_HORARIA, prediccion.c.franja_objetivo), Date).label("dia")
    filas = conn.execute(
        select(dia, func.count().label("n"))
        .where(prediccion.c.franja_objetivo >= a_zona(desde), prediccion.c.franja_objetivo < a_zona(hasta))
        .group_by(dia)
    ).all()
    return pd.Series({pd.Timestamp(f.dia): int(f.n) for f in filas}, dtype=float)


def alertas_gestionadas(conn: Connection, desde: pd.Timestamp, hasta: pd.Timestamp) -> pd.DataFrame:
    filas = conn.execute(
        select(alerta.c.codigo, alerta.c.estado)
        .where(alerta.c.estado.in_(("cerrada", "descartada")), alerta.c.actualizada_en >= a_zona(desde),
               alerta.c.actualizada_en < a_zona(hasta))
    ).all()
    return pd.DataFrame(filas, columns=["codigo", "estado"])
