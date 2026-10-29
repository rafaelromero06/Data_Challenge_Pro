from datetime import datetime

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as insert_pg
from sqlalchemy.engine import Connection

from frecuencias.persistencia.consultas import paginar
from frecuencias.persistencia.tablas import alerta, historial_alerta
from frecuencias.tiempo import ahora_utc

ESTADOS_ABIERTOS = ("abierta", "reconocida", "asignada")
TRANSICIONES = {
    "abierta": {"reconocida", "asignada", "cerrada", "descartada"},
    "reconocida": {"asignada", "cerrada", "descartada"},
    "asignada": {"reconocida", "cerrada", "descartada"},
    "cerrada": set(),
    "descartada": set(),
}
ACTUALIZABLES = (
    "severidad", "ventana_inicio", "ventana_fin", "horizonte_min", "valor_observado", "valor_esperado", "umbral",
    "puntuacion_anomalia", "explicacion", "variables_influyentes", "recomendacion", "execution_id", "prediction_id",
    "recommendation_id", "model_registry_id",
)


def registrar(conn: Connection, datos: dict) -> tuple[object, bool]:
    valores = {**datos, "estado": "abierta", "actualizada_en": ahora_utc()}
    consulta = insert_pg(alerta).values(**valores)
    consulta = consulta.on_conflict_do_update(
        index_elements=[alerta.c.clave_deduplicacion],
        index_where=text("estado IN ('abierta', 'reconocida', 'asignada')"),
        set_={**{c: consulta.excluded[c] for c in ACTUALIZABLES if c in datos}, "actualizada_en": ahora_utc()},
    ).returning(alerta.c.id, text("(xmax = 0) AS nueva"))
    fila = conn.execute(consulta).one()
    if fila.nueva:
        conn.execute(insert(historial_alerta).values(
            alert_id=fila.id, estado_anterior=None, estado_nuevo="abierta", usuario="sistema",
            comentario="Alerta generada automaticamente",
        ))
    return fila.id, bool(fila.nueva)


def obtener(conn: Connection, id_alerta) -> dict | None:
    fila = conn.execute(select(alerta).where(alerta.c.id == id_alerta)).mappings().first()
    return dict(fila) if fila else None


def historial(conn: Connection, id_alerta) -> list[dict]:
    filas = conn.execute(
        select(historial_alerta).where(historial_alerta.c.alert_id == id_alerta).order_by(historial_alerta.c.id)
    ).mappings()
    return [dict(f) for f in filas]


def cambiar_estado(conn: Connection, id_alerta, estado_nuevo: str | None, usuario: str,
                   comentario: str | None = None, usuario_asignado: str | None = None) -> dict:
    actual = obtener(conn, id_alerta)
    if actual is None:
        raise LookupError(f"No existe la alerta {id_alerta}.")
    destino = estado_nuevo or actual["estado"]
    if destino != actual["estado"] and destino not in TRANSICIONES[actual["estado"]]:
        raise ValueError(f"No se puede pasar de '{actual['estado']}' a '{destino}'.")
    if destino == "asignada" and not (usuario_asignado or actual["usuario_asignado"]):
        raise ValueError("Para asignar la alerta indique usuario_asignado.")
    if destino == actual["estado"] and not comentario and not usuario_asignado:
        raise ValueError("No hay cambios: indique un estado nuevo, un comentario o un usuario asignado.")
    valores = {"estado": destino, "actualizada_en": ahora_utc()}
    if usuario_asignado:
        valores["usuario_asignado"] = usuario_asignado
    conn.execute(update(alerta).where(alerta.c.id == id_alerta).values(**valores))
    conn.execute(insert(historial_alerta).values(
        alert_id=id_alerta, estado_anterior=actual["estado"], estado_nuevo=destino, usuario=usuario,
        comentario=comentario,
    ))
    return obtener(conn, id_alerta)


def resumen(conn: Connection, desde: datetime | None = None, hasta: datetime | None = None,
            id_ruta: int | None = None) -> dict:
    consulta = select(alerta.c.codigo, alerta.c.severidad, alerta.c.estado, func.count().label("alertas"))
    if desde is not None:
        consulta = consulta.where(alerta.c.detectada_en >= desde)
    if hasta is not None:
        consulta = consulta.where(alerta.c.detectada_en < hasta)
    if id_ruta is not None:
        consulta = consulta.where(alerta.c.id_ruta == id_ruta)
    filas = conn.execute(consulta.group_by(alerta.c.codigo, alerta.c.severidad, alerta.c.estado)).mappings().all()
    por_codigo, por_severidad, por_estado = {}, {}, {}
    for f in filas:
        por_codigo.setdefault(f["codigo"], {"total": 0, "descartadas": 0, "cerradas": 0})
        por_codigo[f["codigo"]]["total"] += f["alertas"]
        if f["estado"] in ("descartada", "cerrada"):
            por_codigo[f["codigo"]][f["estado"] + "s"] += f["alertas"]
        por_severidad[f["severidad"]] = por_severidad.get(f["severidad"], 0) + f["alertas"]
        por_estado[f["estado"]] = por_estado.get(f["estado"], 0) + f["alertas"]
    for valores in por_codigo.values():
        gestionadas = valores["descartadas"] + valores["cerradas"]
        valores["pct_descartadas"] = round(valores["descartadas"] / gestionadas, 4) if gestionadas else None
    total = sum(por_estado.values())
    gestionadas = por_estado.get("descartada", 0) + por_estado.get("cerrada", 0)
    return {"total": total, "por_estado": por_estado, "por_severidad": por_severidad,
            "por_codigo": dict(sorted(por_codigo.items())),
            "pct_descartadas": round(por_estado.get("descartada", 0) / gestionadas, 4) if gestionadas else None}


def consultar(conn: Connection, estado: str | None = None, severidad: str | None = None, codigo: str | None = None,
              id_ruta: int | None = None, desde: datetime | None = None, hasta: datetime | None = None,
              pagina: int = 1, tamano: int = 50, orden: str = "detectada_en",
              descendente: bool = True) -> tuple[list[dict], int]:
    consulta = select(alerta)
    if estado:
        consulta = consulta.where(alerta.c.estado == estado)
    if severidad:
        consulta = consulta.where(alerta.c.severidad == severidad)
    if codigo:
        consulta = consulta.where(alerta.c.codigo == codigo)
    if id_ruta is not None:
        consulta = consulta.where(alerta.c.id_ruta == id_ruta)
    if desde is not None:
        consulta = consulta.where(alerta.c.detectada_en >= desde)
    if hasta is not None:
        consulta = consulta.where(alerta.c.detectada_en < hasta)
    return paginar(conn, consulta, alerta, orden, descendente, pagina, tamano)
