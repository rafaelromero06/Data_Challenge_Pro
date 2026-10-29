from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection

from frecuencias.persistencia.tablas import modelo
from frecuencias.tiempo import ahora_utc


def registrar(conn: Connection, **datos) -> int:
    datos.setdefault("estado", "candidato")
    return conn.execute(insert(modelo).values(**datos).returning(modelo.c.id)).scalar_one()


def obtener(conn: Connection, id_modelo: int) -> dict | None:
    fila = conn.execute(select(modelo).where(modelo.c.id == id_modelo)).mappings().first()
    return dict(fila) if fila else None


def activo(conn: Connection, horizonte_min: int) -> dict | None:
    fila = conn.execute(
        select(modelo).where(modelo.c.horizonte_min == horizonte_min, modelo.c.estado == "activo")
    ).mappings().first()
    return dict(fila) if fila else None


def activos(conn: Connection) -> dict[int, dict]:
    filas = conn.execute(select(modelo).where(modelo.c.estado == "activo")).mappings().all()
    return {f["horizonte_min"]: dict(f) for f in filas}


def listar(conn: Connection, horizonte_min: int | None = None, estado: str | None = None) -> list[dict]:
    consulta = select(modelo)
    if horizonte_min is not None:
        consulta = consulta.where(modelo.c.horizonte_min == horizonte_min)
    if estado:
        consulta = consulta.where(modelo.c.estado == estado)
    filas = conn.execute(consulta.order_by(modelo.c.horizonte_min, modelo.c.registrado_en.desc())).mappings()
    return [dict(f) for f in filas]


def activar(conn: Connection, id_modelo: int, usuario: str) -> dict:
    candidato = obtener(conn, id_modelo)
    if candidato is None:
        raise LookupError(f"No existe el modelo {id_modelo}.")
    if candidato["estado"] == "activo":
        return candidato
    conn.execute(
        update(modelo)
        .where(modelo.c.horizonte_min == candidato["horizonte_min"], modelo.c.estado == "activo")
        .values(estado="retirado")
    )
    conn.execute(
        update(modelo).where(modelo.c.id == id_modelo)
        .values(estado="activo", activado_en=ahora_utc(), aprobado_por=usuario)
    )
    return obtener(conn, id_modelo)


def rechazar(conn: Connection, id_modelo: int, usuario: str) -> dict:
    actual = obtener(conn, id_modelo)
    if actual is None:
        raise LookupError(f"No existe el modelo {id_modelo}.")
    if actual["estado"] == "activo":
        raise ValueError("No se puede rechazar el modelo activo: active otra version primero.")
    conn.execute(update(modelo).where(modelo.c.id == id_modelo).values(estado="rechazado", aprobado_por=usuario))
    return obtener(conn, id_modelo)
