import pandas as pd

from frecuencias.alertas import reglas
from frecuencias.config import PASO_MIN
from frecuencias.optimizacion import oferta
from frecuencias.optimizacion.frecuencia import recomendar
from frecuencias.optimizacion.parametros import parametros_hora
from frecuencias.tiempo import a_zona

FRANJAS_POR_HORA = 60 // PASO_MIN


def bloques(predicciones: list[dict]) -> list[list[dict]]:
    ordenadas = sorted(predicciones, key=lambda r: r["franja_objetivo"])
    return [ordenadas[i:i + FRANJAS_POR_HORA] for i in range(0, len(ordenadas), FRANJAS_POR_HORA)
            if len(ordenadas[i:i + FRANJAS_POR_HORA]) == FRANJAS_POR_HORA]


def _oferta_del_bloque(viajes: pd.DataFrame | None, id_ruta: int, inicio: pd.Timestamp, fin: pd.Timestamp) -> dict:
    if viajes is None:
        return {"ventana": None, "fraccion": 1.0, "actual": None, "origen": "sin_datos"}
    ventana = oferta.ventana_servicio(viajes, id_ruta, inicio)
    fraccion = oferta.fraccion_en_servicio(ventana, inicio, fin)
    actual, origen = oferta.buses_actuales(viajes, id_ruta, inicio)
    if actual is not None and 0 < fraccion < 1:
        actual = round(actual / fraccion, 2)
    return {"ventana": ventana, "fraccion": fraccion, "actual": actual, "origen": origen}


def _alertas_observadas(bloque: list[dict], rec: dict, observado: pd.Series | None, demanda: float,
                        error_pronostico: float) -> list[dict]:
    if observado is None:
        return []
    inicio, fin = rec["franja_inicio"], rec["franja_fin"]
    reales = observado.reindex(pd.date_range(inicio, fin, freq=f"{PASO_MIN}min", inclusive="left"))
    if not reales.notna().all():
        return []
    return reglas.por_observacion({
        "id_ruta": rec["id_ruta"], "inicio": inicio, "fin": fin, "observado": float(reales.sum()),
        "predicho": demanda, "limite_inferior": rec["demanda_inferior"], "limite_superior": rec["demanda_superior"],
        "prediction_id": rec["prediction_id"], "model_registry_id": bloque[0].get("model_registry_id"),
        "horizonte_min": rec["horizonte_min"],
    }, error_pronostico)


def recomendar_bloques(predicciones: list[dict], id_ruta: int, valores: dict, viajes: pd.DataFrame | None,
                       observado: pd.Series | None = None) -> list[dict]:
    resultado = []
    for bloque in bloques(predicciones):
        if not any(r.get("servicio_habitual", True) for r in bloque):
            continue
        inicio = pd.Timestamp(bloque[0]["franja_objetivo"])
        fin = inicio + pd.Timedelta(hours=1)
        actual = _oferta_del_bloque(viajes, id_ruta, inicio, fin)
        if actual["fraccion"] < oferta.FRACCION_MINIMA_SERVICIO:
            continue
        p = parametros_hora(valores, inicio.hour, int(bloque[0]["variables"].get("tipo_dia") or 0))
        demanda = sum(r["pasajeros_predichos"] for r in bloque)
        demanda_alta = sum(r["limite_superior"] for r in bloque)
        en_ruta = None if viajes is None else oferta.despachos_en_ruta(viajes, id_ruta, inicio, p.tiempo_ciclo_min)
        rec = recomendar(demanda, demanda_alta, p, actual["actual"], en_ruta)
        rec.update({
            "id_ruta": id_ruta, "franja_inicio": inicio, "franja_fin": fin,
            "prediction_id": bloque[0].get("prediction_id"), "horizonte_min": bloque[-1]["minutos_antelacion"],
            "cuantil_demanda": 0.9, "origen_frecuencia_actual": actual["origen"], "parametros_hora": p.como_dict(),
            "demanda_inferior": round(sum(r["limite_inferior"] for r in bloque), 2),
            "demanda_superior": round(demanda_alta, 2), "fraccion_servicio": round(actual["fraccion"], 3),
            "ventana_servicio": [str(v) for v in actual["ventana"]] if actual["ventana"] else None,
        })
        rec["alertas"] = reglas.por_recomendacion(rec, p, bloque[0]["explicacion"]["variables"])
        rec["alertas"] += _alertas_observadas(bloque, rec, observado, demanda,
                                              valores.get("error_pronostico", 15.0) / 100)
        resultado.append(rec)
    return resultado


def fila_recomendacion(rec: dict, execution_id: int | None) -> dict:
    columnas = ("prediction_id", "id_ruta", "demanda_esperada", "capacidad_efectiva", "buses_por_hora", "headway_min",
                "vehiculos_requeridos", "headway_actual_min", "viable", "motivo_no_viable", "cuantil_demanda",
                "carga_maxima_esperada", "ocupacion_esperada", "buses_por_hora_actual", "ocupacion_actual",
                "flota_disponible", "tiempo_ciclo_min")
    fila = {c: rec.get(c) for c in columnas}
    fila.update({
        "execution_id": execution_id, "franja_inicio": a_zona(rec["franja_inicio"]),
        "franja_fin": a_zona(rec["franja_fin"]),
        "parametros": {"hora": rec["parametros_hora"], "sensibilidad": rec["sensibilidad"],
                       "limitado_por": rec["limitado_por"], "metodo_vehiculos": rec["metodo_vehiculos"],
                       "origen_frecuencia_actual": rec["origen_frecuencia_actual"],
                       "demanda_inferior": rec["demanda_inferior"], "demanda_superior": rec["demanda_superior"],
                       "objetivo": rec["objetivo"], "linea_base": rec["linea_base"], "recaudo": rec["recaudo"],
                       "fraccion_servicio": rec.get("fraccion_servicio"), "ventana_servicio": rec.get("ventana_servicio")},
    })
    return fila
