from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from frecuencias.persistencia.consultas import paginar
from frecuencias.persistencia.tablas import notificacion, problema_calidad


def consultar(conn: Connection, execution_id: int | None = None, regla: str | None = None,
              dimension: str | None = None, pagina: int = 1, tamano: int = 50) -> tuple[list[dict], int]:
    consulta = select(problema_calidad)
    if execution_id is not None:
        consulta = consulta.where(problema_calidad.c.execution_id == execution_id)
    if regla:
        consulta = consulta.where(problema_calidad.c.regla == regla)
    if dimension:
        consulta = consulta.where(problema_calidad.c.dimension == dimension)
    return paginar(conn, consulta, problema_calidad, "id", False, pagina, tamano)


def resumen(conn: Connection, execution_id: int) -> list[dict]:
    filas = conn.execute(
        select(problema_calidad.c.regla, problema_calidad.c.dimension, problema_calidad.c.tratamiento,
               func.count().label("filas"))
        .where(problema_calidad.c.execution_id == execution_id)
        .group_by(problema_calidad.c.regla, problema_calidad.c.dimension, problema_calidad.c.tratamiento)
        .order_by(problema_calidad.c.regla)
    ).mappings()
    return [dict(f) for f in filas]


def notificaciones(conn: Connection, alert_id=None, pagina: int = 1, tamano: int = 50) -> tuple[list[dict], int]:
    consulta = select(notificacion)
    if alert_id is not None:
        consulta = consulta.where(notificacion.c.alert_id == alert_id)
    return paginar(conn, consulta, notificacion, "creada_en", True, pagina, tamano)
