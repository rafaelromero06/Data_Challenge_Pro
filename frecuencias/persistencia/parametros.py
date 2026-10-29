from sqlalchemy import func, insert, or_, select, update
from sqlalchemy.engine import Connection

from frecuencias.persistencia.tablas import parametro


def _misma_ruta(id_ruta: int | None):
    return parametro.c.id_ruta.is_(None) if id_ruta is None else parametro.c.id_ruta == id_ruta


def vigentes(conn: Connection, id_ruta: int | None = None) -> dict[str, float | None]:
    filtro_ruta = parametro.c.id_ruta.is_(None)
    if id_ruta is not None:
        filtro_ruta = or_(filtro_ruta, parametro.c.id_ruta == id_ruta)
    filas = conn.execute(
        select(parametro.c.clave, parametro.c.id_ruta, parametro.c.valor).where(parametro.c.vigente, filtro_ruta)
    ).all()
    valores = {}
    for fila in sorted(filas, key=lambda f: f.id_ruta is not None):
        valores[fila.clave] = None if fila.valor is None else float(fila.valor)
    return valores


def listar(conn: Connection, solo_vigentes: bool = True) -> list[dict]:
    consulta = select(parametro)
    if solo_vigentes:
        consulta = consulta.where(parametro.c.vigente)
    filas = conn.execute(consulta.order_by(parametro.c.clave, parametro.c.id_ruta, parametro.c.version)).mappings()
    return [dict(f) for f in filas]


def publicar(conn: Connection, clave: str, valor: float | None, usuario: str, id_ruta: int | None = None,
             unidad: str | None = None, descripcion: str | None = None, validado: bool = False) -> int:
    actual = conn.execute(
        select(parametro).where(parametro.c.clave == clave, parametro.c.vigente, _misma_ruta(id_ruta))
    ).mappings().first()
    if actual is None:
        general = conn.execute(
            select(parametro).where(parametro.c.clave == clave, parametro.c.vigente, parametro.c.id_ruta.is_(None))
        ).mappings().first()
        unidad = unidad or (general["unidad"] if general else None)
        descripcion = descripcion or (general["descripcion"] if general else None)
        if unidad is None or descripcion is None:
            raise ValueError(f"El parametro '{clave}' no existe: indique unidad y descripcion.")
    version = conn.execute(
        select(func.coalesce(func.max(parametro.c.version), 0)).where(parametro.c.clave == clave, _misma_ruta(id_ruta))
    ).scalar_one() + 1
    if actual is not None:
        conn.execute(update(parametro).where(parametro.c.id == actual["id"]).values(vigente=False))
    conn.execute(insert(parametro).values(
        clave=clave, id_ruta=id_ruta, valor=valor, unidad=unidad or actual["unidad"],
        descripcion=descripcion or actual["descripcion"], version=version, vigente=True, validado=validado,
        creado_por=usuario,
    ))
    return version
