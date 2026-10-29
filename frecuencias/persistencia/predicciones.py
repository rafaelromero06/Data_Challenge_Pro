from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection

from frecuencias.persistencia.consultas import paginar
from frecuencias.persistencia.tablas import prediccion

CLAVE = [
    prediccion.c.model_registry_id,
    prediccion.c.id_ruta,
    text("COALESCE(sentido, -1)"),
    text("COALESCE(id_segmento, -1)"),
    prediccion.c.franja_objetivo,
    prediccion.c.horizonte_min,
]
ACTUALIZABLES = (
    "execution_id", "referencia", "pasajeros_predichos", "limite_inferior", "limite_superior", "nivel_confianza",
    "feature_version", "data_cutoff", "explicacion", "generada_en", "latencia_ms",
)


def guardar(conn: Connection, filas: list[dict]) -> list:
    if not filas:
        return []
    consulta = insert(prediccion).values(filas)
    consulta = consulta.on_conflict_do_update(
        index_elements=CLAVE,
        set_={c: consulta.excluded[c] for c in ACTUALIZABLES if c in filas[0]},
    ).returning(prediccion.c.id)
    return list(conn.execute(consulta).scalars())


def consultar(conn: Connection, id_ruta: int | None = None, horizonte_min: int | None = None,
              desde: datetime | None = None, hasta: datetime | None = None, model_registry_id: int | None = None,
              execution_id: int | None = None, pagina: int = 1, tamano: int = 50, orden: str = "franja_objetivo",
              descendente: bool = True) -> tuple[list[dict], int]:
    consulta = select(prediccion)
    if id_ruta is not None:
        consulta = consulta.where(prediccion.c.id_ruta == id_ruta)
    if horizonte_min is not None:
        consulta = consulta.where(prediccion.c.horizonte_min == horizonte_min)
    if desde is not None:
        consulta = consulta.where(prediccion.c.franja_objetivo >= desde)
    if hasta is not None:
        consulta = consulta.where(prediccion.c.franja_objetivo < hasta)
    if model_registry_id is not None:
        consulta = consulta.where(prediccion.c.model_registry_id == model_registry_id)
    if execution_id is not None:
        consulta = consulta.where(prediccion.c.execution_id == execution_id)
    return paginar(conn, consulta, prediccion, orden, descendente, pagina, tamano)
