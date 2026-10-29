import argparse
import json
import time
from datetime import datetime, timezone

import lightgbm as lgb
import numpy as np
import pandas as pd

from frecuencias import db
from frecuencias.analitica.carga import cargar_calendario, cargar_datos
from frecuencias.analitica.features import FEATURES, construir_features
from frecuencias.analitica.serie import preparar_serie
from frecuencias.config import ARCHIVO_DEMANDA, CARPETA_MODELOS, FEATURE_VERSION, HORIZONTES_MIN, RANDOM_SEED
from frecuencias.inferencia import ajuste, artefactos
from frecuencias.inferencia import cuantiles as cq
from frecuencias.inferencia import diagnostico
from frecuencias.inferencia.evaluacion import desglose, metricas_completas, metricas_intervalo, pinball

ARCHIVO_REPORTE = CARPETA_MODELOS / "reporte_entrenamiento.json"
FAMILIA = "lightgbm"


def nuevo_modelo(n_estimators=3000, early_stopping_rounds=50):
    import xgboost as xgb

    return xgb.XGBRegressor(
        objective="count:poisson",
        n_estimators=n_estimators,
        max_depth=6,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        early_stopping_rounds=early_stopping_rounds,
        random_state=RANDOM_SEED,
        n_jobs=-1,
    )


def particion_temporal(df: pd.DataFrame, frac_val=0.1, frac_test=0.2):
    tiempos = np.sort(df.loc[df["tiene_base"], "ts"].unique())
    corte_val = tiempos[int(len(tiempos) * (1 - frac_val - frac_test))]
    corte_test = tiempos[int(len(tiempos) * (1 - frac_test))]
    train = df[df["ts"] < corte_val]
    val = df[(df["ts"] >= corte_val) & (df["ts"] < corte_test) & df["tiene_base"]]
    test = df[(df["ts"] >= corte_test) & df["tiene_base"]]
    return train, val, test, pd.Timestamp(corte_val), pd.Timestamp(corte_test)


def entrenar_punto(parametros: dict, train: pd.DataFrame, val: pd.DataFrame, features: list[str]) -> lgb.Booster:
    return lgb.train(parametros, lgb.Dataset(train[features], train["subidas"]),
                     num_boost_round=ajuste.RONDAS_MAXIMAS,
                     valid_sets=[lgb.Dataset(val[features], val["subidas"])],
                     callbacks=[lgb.early_stopping(100, verbose=False)])


def evaluar_candidato(df: pd.DataFrame, features: list[str], parametros: dict, comparar: bool = True) -> dict:
    train, val, test, c_val, c_test = particion_temporal(df)
    modelo = entrenar_punto(parametros, train, val, features)
    pred = np.clip(modelo.predict(test[features], num_iteration=modelo.best_iteration), 0, None)
    y = test["subidas"]
    resultados = {
        "Base: sem. pasada": metricas_completas(y, test["lag_7d"]),
        "Base: prom 4 sem": metricas_completas(y, test["prom_4_semanas"]),
        "LightGBM": metricas_completas(y, pred),
    }
    if comparar:
        inicio = time.perf_counter()
        xgb_modelo = nuevo_modelo()
        xgb_modelo.fit(train[features], train["subidas"], eval_set=[(val[features], val["subidas"])], verbose=False)
        resultados["XGBoost"] = metricas_completas(y, xgb_modelo.predict(test[features]))
        resultados["XGBoost"]["segundos"] = round(time.perf_counter() - inicio, 1)

    modelos_q = cq.entrenar(train, val, features, parametros)
    P_val, P_test = cq.predecir(modelos_q, val[features]), cq.predecir(modelos_q, test[features])
    pred_val = np.clip(modelo.predict(val[features], num_iteration=modelo.best_iteration), 0, None)
    ajuste_cqr = cq.calibrar(val["subidas"], P_val)
    inf_cqr, sup_cqr = cq.intervalo(P_test, ajuste_cqr, pred)
    inf_q, sup_q = cq.intervalo(P_test, 0.0)
    c = cq.ancho_constante(val["subidas"], pred_val)
    evaluado = test.assign(prediccion=pred, inferior=inf_cqr, superior=sup_cqr)
    por_hora = evaluado.groupby(evaluado["ts"].dt.hour).apply(
        lambda g: float(((g["subidas"] >= g["inferior"]) & (g["subidas"] <= g["superior"])).mean()))
    intervalos = {
        "cuantiles": metricas_intervalo(y, inf_q, sup_q, cq.NIVEL),
        "cuantiles_cqr": metricas_intervalo(y, inf_cqr, sup_cqr, cq.NIVEL),
        "ancho_constante": metricas_intervalo(y, np.clip(pred - c, 0, None), pred + c, cq.NIVEL),
        "pinball": {str(q): pinball(y, P_test[:, i], q) for i, q in enumerate(cq.CUANTILES)},
        "cobertura_por_hora": {int(h): round(v, 4) for h, v in por_hora.items()},
        "cobertura_por_ruta": {str(int(r)): round(float(((g["subidas"] >= g["inferior"]) & (g["subidas"] <= g["superior"])).mean()), 4)
                               for r, g in evaluado.groupby("id_ruta")},
        "ajuste_cqr": ajuste_cqr, "nivel": cq.NIVEL,
    }
    pred_train = np.clip(modelo.predict(train[features], num_iteration=modelo.best_iteration), 0, None)
    diagnosticos = {
        "importancia": diagnostico.importancia(modelo, test[features]),
        "residuos": diagnostico.residuos(evaluado, "subidas", "prediccion"),
        "sobreajuste": diagnostico.sobreajuste(train["subidas"], pred_train, val["subidas"], pred_val),
    }
    return {
        "modelo": modelo, "cuantiles": modelos_q, "rondas": modelo.best_iteration,
        "rondas_cuantiles": {q: m.best_iteration for q, m in modelos_q.items()},
        "corte_validacion": c_val, "corte_prueba": c_test, "filas": {"train": len(train), "val": len(val), "test": len(test)},
        "metricas_prueba": resultados, "desglose": desglose(evaluado.assign(base=test["prom_4_semanas"]), "subidas",
                                                           "prediccion", "base"),
        "intervalos": intervalos, "diagnostico": diagnosticos,
    }


def reentrenar_final(df: pd.DataFrame, features: list[str], parametros: dict, rondas: int,
                     rondas_cuantiles: dict) -> tuple[lgb.Booster, dict]:
    punto = lgb.train(parametros, lgb.Dataset(df[features], df["subidas"]), num_boost_round=max(rondas, 1))
    return punto, cq.reentrenar(df, features, parametros, rondas_cuantiles)


def version_actual() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def construir_paquete(df: pd.DataFrame, horizonte: int, parametros: dict, evaluacion: dict, ajuste_info: dict | None,
                      fuente: str, version: str) -> dict:
    punto, modelos_q = reentrenar_final(df, FEATURES, parametros, evaluacion["rondas"], evaluacion["rondas_cuantiles"])
    model_id = f"demanda-lgbm-h{horizonte}"
    meta = {
        "model_id": model_id, "version": version, "familia": FAMILIA, "feature_version": FEATURE_VERSION,
        "features": FEATURES, "horizonte_min": horizonte, "data_cutoff": str(df["ts"].max()),
        "corte_validacion": str(evaluacion["corte_validacion"]), "corte_prueba": str(evaluacion["corte_prueba"]),
        "entrenado_con": "todas las franjas con servicio (train + val + test)", "franjas_entrenamiento": len(df),
        "n_arboles": evaluacion["rondas"], "n_arboles_cuantiles": {str(q): r for q, r in evaluacion["rondas_cuantiles"].items()},
        "generated_at": datetime.now(timezone.utc).isoformat(), "semilla": RANDOM_SEED, "fuente": fuente,
        "hiperparametros": parametros, "metricas_prueba": evaluacion["metricas_prueba"],
        "intervalo": {"nivel": cq.NIVEL, "cuantiles": list(cq.CUANTILES), "ajuste_cqr": evaluacion["intervalos"]["ajuste_cqr"],
                      "metodo": "LightGBM cuantil + calibracion conforme (CQR)"},
        "ajuste": None if ajuste_info is None else {k: v for k, v in ajuste_info.items() if k != "historial"},
    }
    carpeta, sha = artefactos.guardar(CARPETA_MODELOS / "paquetes" / f"{model_id}-{version}", punto, modelos_q, meta)
    return {"meta": meta, "carpeta": carpeta, "sha": sha}


def registrar(motor, paquete: dict, activar: bool, usuario: str) -> int:
    from frecuencias.persistencia import registro_modelos

    meta = paquete["meta"]
    with motor.begin() as conn:
        id_modelo = registro_modelos.registrar(
            conn, model_id=meta["model_id"], version=meta["version"], horizonte_min=meta["horizonte_min"],
            feature_version=meta["feature_version"], features=meta["features"], hiperparametros=meta["hiperparametros"],
            semilla=meta["semilla"], familia=meta["familia"],
            datos_entrenamiento={"franjas": meta["franjas_entrenamiento"], "fuente": meta["fuente"],
                                 "data_cutoff": meta["data_cutoff"]},
            data_cutoff=pd.Timestamp(meta["data_cutoff"]).tz_localize("America/Bogota"),
            corte_validacion=pd.Timestamp(meta["corte_validacion"]).tz_localize("America/Bogota"),
            corte_prueba=pd.Timestamp(meta["corte_prueba"]).tz_localize("America/Bogota"),
            metricas={"prueba": meta["metricas_prueba"], "intervalo": meta["intervalo"]},
            artefacto=artefactos.ruta_relativa(paquete["carpeta"]), artefacto_sha256=paquete["sha"],
            entrenado_en=datetime.now(timezone.utc))
        if activar:
            registro_modelos.activar(conn, id_modelo, usuario)
    return id_modelo


def main():
    parser = argparse.ArgumentParser(description="Entrena, evalua y registra los modelos de demanda")
    parser.add_argument("--trials", type=int, default=20, help="Trials de Optuna por horizonte (0 = sin ajuste)")
    parser.add_argument("--hasta", help="Usa solo datos anteriores a esta fecha local (AAAA-MM-DD)")
    parser.add_argument("--sin-comparar", action="store_true", help="No entrena XGBoost para comparar")
    parser.add_argument("--no-activar", action="store_true", help="Registra como candidato sin activar")
    parser.add_argument("--horizontes", default=",".join(map(str, HORIZONTES_MIN)))
    args = parser.parse_args()

    serie = preparar_serie(cargar_datos())
    if args.hasta:
        serie = serie[serie["ts"] < pd.Timestamp(args.hasta)]
    calendario = cargar_calendario()
    fuente = "sensores" if ARCHIVO_DEMANDA.exists() else "sintetica"
    motor = db.motor_destino() if db.url_destino() else None
    version = version_actual()
    reporte = {"generado": datetime.now(timezone.utc).isoformat(), "version": version, "familia": FAMILIA,
               "feature_version": FEATURE_VERSION, "horizontes": {}}
    for horizonte in [int(h) for h in args.horizontes.split(",")]:
        inicio = time.perf_counter()
        df = construir_features(serie, horizonte, calendario)
        train, val, test, _, corte_test = particion_temporal(df)
        ajuste_info = None
        parametros = dict(ajuste.PARAMETROS_BASE)
        if args.trials > 0:
            ajuste_info = ajuste.ajustar(df[df["ts"] < corte_test], FEATURES, args.trials)
            parametros = ajuste_info["parametros"]
            print(f"h{horizonte}: Optuna {ajuste_info['trials']} trials, desviacion CV {ajuste_info['desviacion_cv']:.4f} "
                  f"(base {ajuste_info['desviacion_cv_base']:.4f})", flush=True)
        evaluacion = evaluar_candidato(df, FEATURES, parametros, comparar=not args.sin_comparar)
        paquete = construir_paquete(df, horizonte, parametros, evaluacion, ajuste_info, fuente, version)
        id_modelo = registrar(motor, paquete, not args.no_activar, "entrenamiento") if motor else None
        if not args.no_activar:
            artefactos.marcar_activo_local(horizonte, paquete["carpeta"], paquete["sha"], id_modelo)
        m = evaluacion["metricas_prueba"]
        print(f"h{horizonte}: WAPE LightGBM {m['LightGBM']['WAPE']:.2%} | base 4 sem {m['Base: prom 4 sem']['WAPE']:.2%}"
              + (f" | XGBoost {m['XGBoost']['WAPE']:.2%}" if "XGBoost" in m else "")
              + f" | cobertura CQR {evaluacion['intervalos']['cuantiles_cqr']['cobertura']:.1%}"
              + f" | {time.perf_counter() - inicio:.0f}s", flush=True)
        reporte["horizontes"][str(horizonte)] = {
            "model_registry_id": id_modelo, "artefacto": artefactos.ruta_relativa(paquete["carpeta"]),
            "sha256": paquete["sha"], "parametros": parametros, "ajuste": ajuste_info, "filas": evaluacion["filas"],
            "rondas": evaluacion["rondas"], "metricas_prueba": m, "desglose": evaluacion["desglose"],
            "intervalos": evaluacion["intervalos"], "diagnostico": evaluacion["diagnostico"],
        }
        ARCHIVO_REPORTE.write_text(json.dumps(reporte, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"Modelos guardados en {CARPETA_MODELOS / 'paquetes'}; reporte en {ARCHIVO_REPORTE}", flush=True)


if __name__ == "__main__":
    main()
