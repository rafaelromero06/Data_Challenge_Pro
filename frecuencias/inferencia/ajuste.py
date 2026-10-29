import lightgbm as lgb
import numpy as np
import optuna
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from frecuencias.config import RANDOM_SEED
from frecuencias.inferencia.evaluacion import desviacion_poisson

PARAMETROS_BASE = {
    "objective": "poisson", "metric": "poisson", "learning_rate": 0.05, "num_leaves": 63, "min_data_in_leaf": 50,
    "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 0.0, "seed": RANDOM_SEED,
    "deterministic": True, "force_row_wise": True, "verbose": -1,
}
RONDAS_MAXIMAS = 3000
PACIENCIA = 50


def pliegues(datos: pd.DataFrame, n_splits: int = 4) -> list[tuple[np.ndarray, np.ndarray]]:
    tiempos = np.sort(datos["ts"].unique())
    resultado = []
    for idx_train, idx_val in TimeSeriesSplit(n_splits=n_splits).split(tiempos):
        en_train = datos["ts"].isin(tiempos[idx_train]).to_numpy()
        en_val = datos["ts"].isin(tiempos[idx_val]).to_numpy() & datos["tiene_base"].to_numpy()
        resultado.append((np.flatnonzero(en_train), np.flatnonzero(en_val)))
    return resultado


def evaluar(parametros: dict, datos: pd.DataFrame, features: list[str], cortes) -> tuple[float, list[int]]:
    desviaciones, rondas = [], []
    for idx_train, idx_val in cortes:
        tr, va = datos.iloc[idx_train], datos.iloc[idx_val]
        modelo = lgb.train(parametros, lgb.Dataset(tr[features], tr["subidas"]), num_boost_round=RONDAS_MAXIMAS,
                           valid_sets=[lgb.Dataset(va[features], va["subidas"])],
                           callbacks=[lgb.early_stopping(PACIENCIA, verbose=False)])
        desviaciones.append(desviacion_poisson(va["subidas"], modelo.predict(va[features],
                                                                             num_iteration=modelo.best_iteration)))
        rondas.append(modelo.best_iteration)
    return float(np.mean(desviaciones)), rondas


def ajustar(datos: pd.DataFrame, features: list[str], n_trials: int, n_splits: int = 4) -> dict:
    cortes = pliegues(datos, n_splits)
    base, rondas_base = evaluar(PARAMETROS_BASE, datos, features, cortes)

    def objetivo(trial: optuna.Trial) -> float:
        parametros = {**PARAMETROS_BASE,
                      "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
                      "num_leaves": trial.suggest_int("num_leaves", 15, 255),
                      "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 10, 200),
                      "feature_fraction": trial.suggest_float("feature_fraction", 0.6, 1.0),
                      "bagging_fraction": trial.suggest_float("bagging_fraction", 0.6, 1.0),
                      "lambda_l2": trial.suggest_float("lambda_l2", 1e-3, 10.0, log=True)}
        valor, rondas = evaluar(parametros, datos, features, cortes)
        trial.set_user_attr("rondas", rondas)
        return valor

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    estudio = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=RANDOM_SEED))
    estudio.enqueue_trial({k: PARAMETROS_BASE[k] for k in ("learning_rate", "num_leaves", "min_data_in_leaf",
                                                           "feature_fraction", "bagging_fraction")} | {"lambda_l2": 1e-3})
    estudio.optimize(objetivo, n_trials=n_trials)
    mejores = {**PARAMETROS_BASE, **estudio.best_params}
    return {"parametros": mejores, "desviacion_cv": estudio.best_value, "desviacion_cv_base": base,
            "rondas_cv_base": rondas_base, "trials": len(estudio.trials), "pliegues": n_splits,
            "historial": [{"numero": t.number, "valor": t.value, **t.params} for t in estudio.trials]}
