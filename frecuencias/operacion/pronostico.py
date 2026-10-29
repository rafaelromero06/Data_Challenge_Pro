import pandas as pd
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection

from frecuencias.inferencia.prediccion import Predictor
from frecuencias.observabilidad import PREDICCIONES
from frecuencias.persistencia import predicciones
from frecuencias.persistencia.tablas import instantanea
from frecuencias.tiempo import a_zona


def pronosticar(predictor: Predictor, serie: pd.DataFrame, id_ruta: int, referencia: pd.Timestamp,
                hasta_min: int | None = None) -> list[dict]:
    ruta = serie[serie["id_ruta"] == id_ruta]
    if ruta.empty:
        raise LookupError(f"La ruta {id_ruta} no tiene datos.")
    resultados = predictor.perfil(ruta, referencia, hasta_min)
    for r in resultados:
        PREDICCIONES.labels(str(r["horizonte_modelo"])).inc()
    return resultados


def guardar(conn: Connection, resultados: list[dict], id_ruta: int, referencia: pd.Timestamp,
            execution_id: int | None) -> list:
    filas = [{
        "execution_id": execution_id, "model_registry_id": r["model_registry_id"], "id_ruta": id_ruta,
        "referencia": a_zona(referencia), "franja_objetivo": a_zona(r["franja_objetivo"]),
        "horizonte_min": r["minutos_antelacion"], "pasajeros_predichos": r["pasajeros_predichos"],
        "limite_inferior": r["limite_inferior"], "limite_superior": r["limite_superior"],
        "nivel_confianza": r["nivel_confianza"], "feature_version": r["feature_version"],
        "data_cutoff": a_zona(r["data_cutoff"]) if r.get("data_cutoff") else None,
        "explicacion": {**r["explicacion"], "horizonte_modelo": r["horizonte_modelo"], "model_id": r["model_id"],
                        "version": r["version"]},
        "latencia_ms": r["latencia_ms"],
    } for r in resultados]
    ids = predicciones.guardar(conn, filas)
    snapshots = [{"prediction_id": i, "execution_id": execution_id, "feature_version": r["feature_version"],
                  "data_cutoff": a_zona(referencia), "variables": r["variables"]} for i, r in zip(ids, resultados)]
    if snapshots:
        consulta = insert(instantanea).values(snapshots)
        conn.execute(consulta.on_conflict_do_update(
            index_elements=[instantanea.c.prediction_id],
            set_={"variables": consulta.excluded.variables, "execution_id": consulta.excluded.execution_id,
                  "data_cutoff": consulta.excluded.data_cutoff}))
    for i, r in zip(ids, resultados):
        r["prediction_id"] = i
    return ids
