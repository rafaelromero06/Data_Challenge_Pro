import math

import lightgbm as lgb
import numpy as np
import pandas as pd

CUANTILES = (0.1, 0.5, 0.9)
NIVEL = 0.8


def entrenar(train: pd.DataFrame, val: pd.DataFrame, features: list[str], parametros: dict,
             rondas: int = 3000) -> dict[float, lgb.Booster]:
    modelos = {}
    for q in CUANTILES:
        p = {**parametros, "objective": "quantile", "alpha": q, "metric": "quantile", "verbose": -1}
        modelos[q] = lgb.train(
            p, lgb.Dataset(train[features], train["subidas"]), num_boost_round=rondas,
            valid_sets=[lgb.Dataset(val[features], val["subidas"])],
            callbacks=[lgb.early_stopping(100, verbose=False)])
    return modelos


def reentrenar(datos: pd.DataFrame, features: list[str], parametros: dict, rondas: dict[float, int]) -> dict:
    return {q: lgb.train({**parametros, "objective": "quantile", "alpha": q, "metric": "quantile", "verbose": -1},
                         lgb.Dataset(datos[features], datos["subidas"]), num_boost_round=max(rondas[q], 1))
            for q in CUANTILES}


def predecir(modelos: dict, X: pd.DataFrame) -> np.ndarray:
    columnas = [np.clip(modelos[q].predict(X, num_iteration=modelos[q].best_iteration or None), 0, None)
                for q in CUANTILES]
    return np.sort(np.column_stack(columnas), axis=1)


def calibrar(y, P: np.ndarray, nivel: float = NIVEL) -> float:
    y = np.asarray(y, float)
    puntajes = np.maximum(P[:, 0] - y, y - P[:, -1])
    n = len(puntajes)
    rango = min(math.ceil((n + 1) * nivel) / n, 1.0)
    return float(np.quantile(puntajes, rango))


def intervalo(P: np.ndarray, ajuste: float, puntual=None) -> tuple[np.ndarray, np.ndarray]:
    inferior = np.clip(P[:, 0] - ajuste, 0, None)
    superior = P[:, -1] + ajuste
    if puntual is not None:
        puntual = np.asarray(puntual, float)
        inferior, superior = np.minimum(inferior, puntual), np.maximum(superior, puntual)
    return inferior, superior


def ancho_constante(y_val, puntual_val, nivel: float = NIVEL) -> float:
    return float(np.quantile(np.abs(np.asarray(y_val, float) - np.asarray(puntual_val, float)), nivel))
