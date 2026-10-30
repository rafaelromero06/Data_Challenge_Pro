import pandas as pd

from frecuencias.optimizacion import oferta
from frecuencias.tiempo import a_local

HORA_DEMOSTRACION = 6
MAXIMO_ALERTAS_RUTA = 3


def _numero(valor) -> float | None:
    return None if valor is None else float(valor)


def _buses(n: int) -> str:
    return f"{n} bus" if n == 1 else f"{n} buses"


def fuera_de_servicio(rec: dict, ventana: tuple[pd.Timestamp, pd.Timestamp] | None) -> bool:
    inicio, fin = a_local(rec["franja_inicio"]), a_local(rec["franja_fin"])
    return oferta.fraccion_en_servicio(ventana, inicio, fin) < oferta.FRACCION_MINIMA_SERVICIO


def accion(rec: dict | None, ventana: tuple[pd.Timestamp, pd.Timestamp] | None = None) -> dict:
    if rec is None:
        return {"tipo": "sin_dato", "texto": "Sin recomendación calculada para esta hora."}
    inicio, fin = a_local(rec["franja_inicio"]), a_local(rec["franja_fin"])
    franja = f"entre {inicio:%H:%M} y {fin:%H:%M}"
    if ventana is not None and fuera_de_servicio(rec, ventana):
        return {"tipo": "fuera_de_servicio",
                "texto": f"Fuera del horario de despachos de la ruta ({ventana[0]:%H:%M} a {ventana[1]:%H:%M}): no se "
                         f"recomiendan salidas {franja}."}
    recomendados = int(rec["buses_por_hora"])
    headway = _numero(rec.get("headway_min"))
    detalle = f"headway de {headway:.0f} min" if headway else f"{recomendados} por hora"
    if not rec["viable"]:
        return {"tipo": "no_viable",
                "texto": f"No alcanza la operación {franja}: {rec.get('motivo_no_viable') or 'restricción operativa'}. "
                         f"Lo máximo posible son {recomendados} buses por hora."}
    actuales = _numero(rec.get("buses_por_hora_actual"))
    if actuales is None:
        return {"tipo": "programar", "texto": f"Programar {recomendados} buses por hora {franja} ({detalle})."}
    diferencia = recomendados - round(actuales)
    if diferencia > 0:
        return {"tipo": "agregar", "texto": f"Agregar {_buses(diferencia)} {franja}: de {round(actuales)} a "
                                            f"{recomendados} por hora ({detalle})."}
    if diferencia < 0:
        ocupacion = _numero(rec.get("ocupacion_esperada"))
        extra = f"; la ocupación esperada queda en {ocupacion:.0%}" if ocupacion is not None else ""
        verbo = "Se puede retirar" if diferencia == -1 else "Se pueden retirar"
        return {"tipo": "reducir", "texto": f"{verbo} {_buses(-diferencia)} {franja}: de {round(actuales)} a "
                                            f"{recomendados} por hora{extra}."}
    return {"tipo": "mantener", "texto": f"Mantener {recomendados} buses por hora {franja}."}


def referencia_por_defecto(dia: pd.Timestamp, ahora: pd.Timestamp) -> pd.Timestamp:
    if dia.normalize() == ahora.normalize():
        return ahora
    return dia.normalize() + pd.Timedelta(hours=HORA_DEMOSTRACION)


def vigentes(recs: list[dict], referencia: pd.Timestamp) -> tuple[dict | None, dict | None]:
    futuras = [r for r in recs if a_local(r["franja_fin"]) > referencia]
    if not futuras:
        return None, None
    primera = futuras[0]
    if a_local(primera["franja_inicio"]) - referencia > pd.Timedelta(hours=1):
        return None, primera
    return primera, futuras[1] if len(futuras) > 1 else None


def hora(rec: dict, observado: pd.Series, ventana: tuple[pd.Timestamp, pd.Timestamp] | None = None) -> dict:
    inicio, fin = a_local(rec["franja_inicio"]), a_local(rec["franja_fin"])
    reales = observado[(observado.index >= inicio) & (observado.index < fin)]
    completo = len(reales) == int((fin - inicio) / pd.Timedelta(minutes=15))
    sugerida = accion(rec, ventana)
    return {"inicio": inicio, "fin": fin, "recomendados": int(rec["buses_por_hora"]),
            "actuales": _numero(rec.get("buses_por_hora_actual")), "headway": _numero(rec.get("headway_min")),
            "viable": bool(rec["viable"]), "demandaEsperada": _numero(rec.get("demanda_esperada")),
            "demandaObservada": float(reales.sum()) if completo else None,
            "ocupacionEsperada": _numero(rec.get("ocupacion_esperada")), "accion": sugerida["tipo"],
            "texto": sugerida["texto"], "recommendationId": str(rec["id"])}


def ruta(id_ruta: int, nombre: str | None, recs: list[dict], observado: pd.Series, referencia: pd.Timestamp,
         abiertas: dict, pendientes: list[dict], ventana: tuple[pd.Timestamp, pd.Timestamp] | None = None) -> dict:
    ordenadas = sorted(recs, key=lambda r: r["franja_inicio"])
    horas = [hora(r, observado, ventana) for r in ordenadas]
    ahora, siguiente = vigentes(ordenadas, referencia)
    conteo = {}
    for h in horas:
        conteo[h["accion"]] = conteo.get(h["accion"], 0) + 1
    return {
        "routeId": id_ruta, "routeName": nombre,
        "ahora": {**accion(ahora, ventana), "inicio": a_local(ahora["franja_inicio"]) if ahora else None},
        "siguiente": {**accion(siguiente, ventana), "inicio": a_local(siguiente["franja_inicio"]) if siguiente else None},
        "horas": horas, "conteo": conteo,
        "servicio": {"apertura": ventana[0], "cierre": ventana[1]} if ventana else None,
        "alertas": {**abiertas, "delDia": len(pendientes),
                    "principales": [{"id": str(a["id"]), "codigo": a["codigo"], "severidad": a["severidad"],
                                     "explicacion": a["explicacion"], "inicio": a_local(a["ventana_inicio"])
                                     if a["ventana_inicio"] is not None else None}
                                    for a in pendientes[:MAXIMO_ALERTAS_RUTA]]},
    }
