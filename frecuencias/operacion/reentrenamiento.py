import lightgbm as lgb
import numpy as np
import pandas as pd

from frecuencias.analitica.features import FEATURES, construir_features
from frecuencias.config import CARPETA_MODELOS
from frecuencias.inferencia import artefactos
from frecuencias.inferencia import cuantiles as cq
from frecuencias.inferencia.entrenamiento import registrar, version_actual
from frecuencias.inferencia.evaluacion import desviacion_poisson, metricas

VENTANA_DIAS = 7
MEJORA_MINIMA_WAPE = 0.0


def inicio_ventana(fin: pd.Timestamp, corte_activo: pd.Timestamp, ventana_dias: int = VENTANA_DIAS) -> pd.Timestamp | None:
    dias = min(ventana_dias, int(((fin - corte_activo) / pd.Timedelta(days=1)) // 2))
    if dias < 1:
        return None
    inicio = (fin - pd.Timedelta(days=dias)).normalize()
    return inicio if inicio > corte_activo else None


def comparar(paquete: artefactos.Paquete, df: pd.DataFrame, inicio_ventana: pd.Timestamp) -> dict:
    parametros = paquete.meta["hiperparametros"]
    entrenamiento = df[df["ts"] < inicio_ventana]
    ventana = df[(df["ts"] >= inicio_ventana) & df["tiene_base"]]
    if ventana.empty or entrenamiento.empty:
        return {"evaluable": False, "motivo": "No hay franjas suficientes en la ventana de comparacion"}
    if len(entrenamiento) <= int(paquete.meta.get("franjas_entrenamiento", 0)):
        return {"evaluable": False, "motivo": "El candidato no tendria datos nuevos respecto del modelo activo"}
    candidato = lgb.train(parametros, lgb.Dataset(entrenamiento[FEATURES], entrenamiento["subidas"]),
                          num_boost_round=int(paquete.meta["n_arboles"]))
    y = ventana["subidas"]
    p_cand = np.clip(candidato.predict(ventana[FEATURES]), 0, None)
    p_act = np.clip(paquete.punto.predict(ventana[FEATURES]), 0, None)
    cand = {**metricas(y, p_cand), "desviacion_poisson": desviacion_poisson(y, p_cand)}
    act = {**metricas(y, p_act), "desviacion_poisson": desviacion_poisson(y, p_act)}
    base = metricas(y, ventana["prom_4_semanas"])
    activo_vio_ventana = pd.Timestamp(paquete.meta["data_cutoff"]) >= inicio_ventana
    mejora = cand["WAPE"] < act["WAPE"] - MEJORA_MINIMA_WAPE or cand["desviacion_poisson"] < act["desviacion_poisson"]
    gana = (mejora and cand["WAPE"] <= act["WAPE"] and cand["desviacion_poisson"] <= act["desviacion_poisson"]
            and cand["WAPE"] < base["WAPE"])
    return {"evaluable": True, "franjas": int(len(ventana)), "franjas_candidato": int(len(entrenamiento)),
            "franjas_activo": int(paquete.meta.get("franjas_entrenamiento", 0)), "candidato": cand, "activo": act,
            "linea_base": base, "activo_vio_ventana": bool(activo_vio_ventana), "activar": bool(gana)}


def campeon_retador(motor, paquetes: dict, serie: pd.DataFrame, calendario, usuario: str = "ciclo",
                    ventana_dias: int = VENTANA_DIAS) -> dict:
    fin = serie.loc[serie["observado"], "ts"].max()
    decisiones = {}
    for h, paquete in paquetes.items():
        corte = pd.Timestamp(paquete.meta["data_cutoff"])
        inicio = inicio_ventana(fin, corte, ventana_dias)
        base = {"modelo_activo": paquete.meta["model_id"], "version_activa": paquete.meta["version"],
                "corte_activo": str(corte)}
        if inicio is None:
            decisiones[str(h)] = base | {"evaluable": False, "resultado": "sin evaluar: se conserva el modelo activo",
                                         "motivo": "Faltan al menos 2 dias de datos posteriores al corte del activo"}
            continue
        df = construir_features(serie, h, calendario)
        decision = base | comparar(paquete, df, inicio) | {"ventana": [str(inicio), str(fin)]}
        if not decision["evaluable"]:
            decisiones[str(h)] = decision | {"resultado": "sin evaluar: se conserva el modelo activo"}
            continue
        if decision.get("activar"):
            meta = {**paquete.meta, "version": version_actual(), "data_cutoff": str(df["ts"].max()),
                    "franjas_entrenamiento": len(df), "metricas_prueba": {"ventana_reentrenamiento": decision},
                    "entrenado_con": "reentrenamiento controlado: todas las franjas hasta la fecha de corte"}
            punto = lgb.train(meta["hiperparametros"], lgb.Dataset(df[FEATURES], df["subidas"]),
                              num_boost_round=int(meta["n_arboles"]))
            rondas_q = {float(q): int(r) for q, r in meta["n_arboles_cuantiles"].items()}
            modelos_q = cq.reentrenar(df, FEATURES, meta["hiperparametros"], rondas_q)
            carpeta, sha = artefactos.guardar(CARPETA_MODELOS / "paquetes" / f"{meta['model_id']}-{meta['version']}",
                                              punto, modelos_q, meta)
            nuevo = {"meta": meta, "carpeta": carpeta, "sha": sha}
            id_modelo = registrar(motor, nuevo, True, usuario) if motor is not None else None
            artefactos.marcar_activo_local(h, carpeta, sha, id_modelo)
            decision |= {"resultado": "activado", "model_registry_id": id_modelo, "version_nueva": meta["version"]}
        else:
            decision["resultado"] = "rechazado: se conserva el modelo activo"
        decisiones[str(h)] = decision
    return decisiones
