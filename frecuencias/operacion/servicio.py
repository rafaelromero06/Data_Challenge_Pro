import pandas as pd
from sqlalchemy.engine import Connection

from frecuencias.alertas import notificaciones
from frecuencias.inferencia.prediccion import Predictor
from frecuencias.observabilidad import ALERTAS, RECOMENDACIONES
from frecuencias.operacion import pronostico, recomendacion
from frecuencias.persistencia import alertas, parametros, recomendaciones
from frecuencias.tiempo import a_zona

COLUMNAS_ALERTA = {"codigo", "severidad", "clave_deduplicacion", "id_ruta", "ventana_inicio", "ventana_fin",
                   "horizonte_min", "valor_observado", "valor_esperado", "umbral", "puntuacion_anomalia",
                   "explicacion", "variables_influyentes", "recomendacion", "prediction_id", "recommendation_id",
                   "model_registry_id", "execution_id"}


def registrar_alertas(conn: Connection, lista: list[dict], execution_id: int | None) -> dict:
    nuevas, actualizadas = 0, 0
    for a in lista:
        datos = {k: v for k, v in {**a, "execution_id": execution_id}.items() if k in COLUMNAS_ALERTA and v is not None}
        for campo in ("ventana_inicio", "ventana_fin"):
            if campo in datos:
                datos[campo] = a_zona(datos[campo])
        id_alerta, es_nueva = alertas.registrar(conn, datos)
        ALERTAS.labels(a["codigo"], a["severidad"]).inc()
        if es_nueva:
            nuevas += 1
            notificaciones.notificar(conn, id_alerta, a)
        else:
            actualizadas += 1
    return {"nuevas": nuevas, "actualizadas": actualizadas}


def ejecutar(conn: Connection, predictor: Predictor, serie: pd.DataFrame, viajes: pd.DataFrame | None, id_ruta: int,
             referencia: pd.Timestamp, execution_id: int | None, bloques_max: int | None = None,
             observado: pd.Series | None = None) -> dict:
    predichas = pronostico.pronosticar(predictor, serie, id_ruta, referencia)
    pronostico.guardar(conn, predichas, id_ruta, referencia, execution_id)
    valores = parametros.vigentes(conn, id_ruta)
    recs = recomendacion.recomendar_bloques(predichas, id_ruta, valores, viajes, observado)[:bloques_max]
    ids = recomendaciones.guardar(conn, [recomendacion.fila_recomendacion(r, execution_id) for r in recs])
    todas = []
    for rec, id_rec in zip(recs, ids):
        rec["recommendation_id"] = id_rec
        RECOMENDACIONES.labels(str(rec["viable"]).lower()).inc()
        for a in rec["alertas"]:
            a["recommendation_id"] = id_rec
        todas += rec["alertas"]
    resumen_alertas = registrar_alertas(conn, todas, execution_id)
    return {"predicciones": predichas, "recomendaciones": recs, "alertas": todas, "resumen_alertas": resumen_alertas}
