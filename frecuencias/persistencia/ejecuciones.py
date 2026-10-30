from datetime import datetime

from sqlalchemy import bindparam, func, insert, literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection

from frecuencias.persistencia.consultas import paginar
from frecuencias.persistencia.tablas import ejecucion

ESTADOS_REINTENTABLES = ("fallida", "interrumpida")


def iniciar(conn: Connection, tipo: str, detalle: dict | None = None, correlation_id=None,
            propietario: str | None = None, reintento_de: int | None = None, intento: int = 1) -> dict:
    valores = {"tipo": tipo, "estado": "en_curso", "propietario": propietario, "latido_en": func.clock_timestamp(),
               "reintento_de": reintento_de, "intento": intento, "avance": 0}
    if detalle is not None:
        valores["detalle"] = detalle
    if correlation_id is not None:
        valores["correlation_id"] = correlation_id
    fila = conn.execute(
        insert(ejecucion).values(**valores).returning(ejecucion.c.id, ejecucion.c.correlation_id)
    ).one()
    return {"id": fila.id, "correlation_id": fila.correlation_id, "intento": intento}


def finalizar(conn: Connection, id_ejecucion: int, estado: str, registros_leidos: int | None = None,
              registros_rechazados: int | None = None, marca_agua_desde: datetime | None = None,
              marca_agua_hasta: datetime | None = None, detalle: dict | None = None, error: str | None = None) -> None:
    valores = {"estado": estado, "finalizada_en": func.clock_timestamp(), "registros_leidos": registros_leidos,
               "registros_rechazados": registros_rechazados, "marca_agua_desde": marca_agua_desde,
               "marca_agua_hasta": marca_agua_hasta, "error": error}
    if estado == "exitosa":
        valores |= {"avance": 100, "etapa": "terminada"}
    if detalle is not None:
        valores["detalle"] = func.coalesce(ejecucion.c.detalle, literal_column("'{}'::jsonb")).op("||")(
            bindparam("detalle_final", detalle, type_=JSONB))
    conn.execute(update(ejecucion).where(ejecucion.c.id == id_ejecucion).values(**valores))


def avanzar(conn: Connection, id_ejecucion: int, etapa: str, avance: float) -> None:
    conn.execute(
        update(ejecucion).where(ejecucion.c.id == id_ejecucion, ejecucion.c.estado == "en_curso")
        .values(etapa=etapa, avance=round(min(max(avance, 0.0), 100.0), 1), latido_en=func.clock_timestamp())
    )


def latir(conn: Connection, propietario: str) -> int:
    return conn.execute(
        update(ejecucion).where(ejecucion.c.estado == "en_curso", ejecucion.c.propietario == propietario)
        .values(latido_en=func.clock_timestamp())
    ).rowcount


def recuperar(conn: Connection, segundos_sin_latido: float, prefijo_propio: str | None = None,
              propietario_actual: str | None = None) -> list[dict]:
    limite = func.clock_timestamp() - func.make_interval(0, 0, 0, 0, 0, 0, bindparam("segundos", segundos_sin_latido))
    abandonada = func.coalesce(ejecucion.c.latido_en, ejecucion.c.iniciada_en) < limite
    condiciones = [abandonada]
    if prefijo_propio:
        condiciones.append(ejecucion.c.propietario.startswith(prefijo_propio, autoescape=True)
                           & (ejecucion.c.propietario != (propietario_actual or "")))
    filas = conn.execute(
        update(ejecucion).where(ejecucion.c.estado == "en_curso", or_(*condiciones))
        .values(estado="interrumpida", finalizada_en=func.clock_timestamp(),
                error="El proceso que la ejecutaba se detuvo antes de terminar (reinicio o caida).")
        .returning(ejecucion.c.id, ejecucion.c.tipo, ejecucion.c.detalle, ejecucion.c.intento,
                   ejecucion.c.etapa, ejecucion.c.propietario)
    ).mappings().all()
    return [dict(f) for f in filas]


def obtener(conn: Connection, id_ejecucion: int) -> dict | None:
    fila = conn.execute(select(ejecucion).where(ejecucion.c.id == id_ejecucion)).mappings().first()
    return dict(fila) if fila else None


def ultima_marca_agua(conn: Connection, tipo: str = "ingesta") -> datetime | None:
    return conn.execute(
        select(func.max(ejecucion.c.marca_agua_hasta)).where(ejecucion.c.tipo == tipo, ejecucion.c.estado == "exitosa")
    ).scalar_one()


def ultimo_reentrenamiento(conn: Connection) -> datetime | None:
    return conn.execute(
        select(func.max(ejecucion.c.iniciada_en)).where(
            ejecucion.c.tipo == "ciclo", ejecucion.c.estado == "exitosa",
            ejecucion.c.detalle.has_key("reentrenamiento"))
    ).scalar_one()


def listar(conn: Connection, tipo: str | None = None, estado: str | None = None, pagina: int = 1,
           tamano: int = 50) -> tuple[list[dict], int]:
    consulta = select(ejecucion)
    if tipo:
        consulta = consulta.where(ejecucion.c.tipo == tipo)
    if estado:
        consulta = consulta.where(ejecucion.c.estado == estado)
    return paginar(conn, consulta, ejecucion, "iniciada_en", True, pagina, tamano)
