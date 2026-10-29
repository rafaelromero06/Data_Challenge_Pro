from datetime import datetime

from sqlalchemy import insert, select
from sqlalchemy.engine import Connection

from frecuencias.persistencia.consultas import paginar
from frecuencias.persistencia.tablas import recomendacion


def guardar(conn: Connection, filas: list[dict]) -> list:
    if not filas:
        return []
    return list(conn.execute(insert(recomendacion).values(filas).returning(recomendacion.c.id)).scalars())


def consultar(conn: Connection, id_ruta: int | None = None, desde: datetime | None = None,
              hasta: datetime | None = None, viable: bool | None = None, execution_id: int | None = None,
              pagina: int = 1, tamano: int = 50, orden: str = "franja_inicio",
              descendente: bool = True) -> tuple[list[dict], int]:
    consulta = select(recomendacion)
    if id_ruta is not None:
        consulta = consulta.where(recomendacion.c.id_ruta == id_ruta)
    if desde is not None:
        consulta = consulta.where(recomendacion.c.franja_inicio >= desde)
    if hasta is not None:
        consulta = consulta.where(recomendacion.c.franja_inicio < hasta)
    if viable is not None:
        consulta = consulta.where(recomendacion.c.viable == viable)
    if execution_id is not None:
        consulta = consulta.where(recomendacion.c.execution_id == execution_id)
    return paginar(conn, consulta, recomendacion, orden, descendente, pagina, tamano)
