from datetime import datetime

from sqlalchemy import bindparam, func, insert, literal_column, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection

from frecuencias.persistencia.consultas import paginar
from frecuencias.persistencia.tablas import ejecucion


def iniciar(conn: Connection, tipo: str, detalle: dict | None = None, correlation_id=None) -> dict:
    valores = {"tipo": tipo, "estado": "en_curso", "detalle": detalle}
    if correlation_id is not None:
        valores["correlation_id"] = correlation_id
    fila = conn.execute(
        insert(ejecucion).values(**valores).returning(ejecucion.c.id, ejecucion.c.correlation_id)
    ).one()
    return {"id": fila.id, "correlation_id": fila.correlation_id}


def finalizar(conn: Connection, id_ejecucion: int, estado: str, registros_leidos: int | None = None,
              registros_rechazados: int | None = None, marca_agua_desde: datetime | None = None,
              marca_agua_hasta: datetime | None = None, detalle: dict | None = None, error: str | None = None) -> None:
    valores = {"estado": estado, "finalizada_en": func.clock_timestamp(), "registros_leidos": registros_leidos,
               "registros_rechazados": registros_rechazados, "marca_agua_desde": marca_agua_desde,
               "marca_agua_hasta": marca_agua_hasta, "error": error}
    if detalle is not None:
        valores["detalle"] = func.coalesce(ejecucion.c.detalle, literal_column("'{}'::jsonb")).op("||")(
            bindparam("detalle_final", detalle, type_=JSONB))
    conn.execute(update(ejecucion).where(ejecucion.c.id == id_ejecucion).values(**valores))


def obtener(conn: Connection, id_ejecucion: int) -> dict | None:
    fila = conn.execute(select(ejecucion).where(ejecucion.c.id == id_ejecucion)).mappings().first()
    return dict(fila) if fila else None


def ultima_marca_agua(conn: Connection, tipo: str = "ingesta") -> datetime | None:
    return conn.execute(
        select(func.max(ejecucion.c.marca_agua_hasta)).where(ejecucion.c.tipo == tipo, ejecucion.c.estado == "exitosa")
    ).scalar_one()


def listar(conn: Connection, tipo: str | None = None, estado: str | None = None, pagina: int = 1,
           tamano: int = 50) -> tuple[list[dict], int]:
    consulta = select(ejecucion)
    if tipo:
        consulta = consulta.where(ejecucion.c.tipo == tipo)
    if estado:
        consulta = consulta.where(ejecucion.c.estado == estado)
    return paginar(conn, consulta, ejecucion, "iniciada_en", True, pagina, tamano)
