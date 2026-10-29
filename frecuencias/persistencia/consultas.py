from sqlalchemy import Select, asc, desc, func, select
from sqlalchemy.engine import Connection
from sqlalchemy.sql.schema import Table

TAMANO_MAXIMO = 500


def paginar(conn: Connection, consulta: Select, tabla: Table, orden: str, descendente: bool,
            pagina: int, tamano: int) -> tuple[list[dict], int]:
    if orden not in tabla.c:
        raise ValueError(f"No se puede ordenar por '{orden}'.")
    pagina = max(pagina, 1)
    tamano = min(max(tamano, 1), TAMANO_MAXIMO)
    total = conn.execute(select(func.count()).select_from(consulta.subquery())).scalar_one()
    columna = tabla.c[orden]
    filas = conn.execute(
        consulta.order_by(desc(columna) if descendente else asc(columna))
        .limit(tamano).offset((pagina - 1) * tamano)
    ).mappings().all()
    return [dict(f) for f in filas], total
