import numpy as np
import pandas as pd

HORAS_PICO = {6, 7, 8, 16, 17, 18}


def metricas(y, yhat) -> dict:
    y, yhat = np.asarray(y, float), np.asarray(yhat, float)
    return {
        "MAE": float(np.mean(np.abs(y - yhat))),
        "RMSE": float(np.sqrt(np.mean((y - yhat) ** 2))),
        "WAPE": float(np.sum(np.abs(y - yhat)) / max(np.sum(np.abs(y)), 1e-9)),
        "sMAPE": float(np.mean(2 * np.abs(yhat - y) / (np.abs(y) + np.abs(yhat) + 1e-9))),
    }


def desviacion_poisson(y, yhat) -> float:
    y, yhat = np.asarray(y, float), np.clip(np.asarray(yhat, float), 1e-6, None)
    termino = np.where(y > 0, y * np.log(np.where(y > 0, y, 1.0) / yhat), 0.0)
    return float(np.mean(2 * (termino - (y - yhat))))


def rmsle(y, yhat) -> float:
    y, yhat = np.clip(np.asarray(y, float), 0, None), np.clip(np.asarray(yhat, float), 0, None)
    return float(np.sqrt(np.mean((np.log1p(yhat) - np.log1p(y)) ** 2)))


def metricas_completas(y, yhat) -> dict:
    return {**metricas(y, yhat), "desviacion_poisson": desviacion_poisson(y, yhat), "RMSLE": rmsle(y, yhat),
            "sesgo": float(np.mean(np.asarray(yhat, float) - np.asarray(y, float)))}


def pinball(y, cuantil, q: float) -> float:
    d = np.asarray(y, float) - np.asarray(cuantil, float)
    return float(np.mean(np.maximum(q * d, (q - 1) * d)))


def metricas_intervalo(y, inferior, superior, nivel: float) -> dict:
    y, lo, hi = np.asarray(y, float), np.asarray(inferior, float), np.asarray(superior, float)
    alfa = 1 - nivel
    puntaje = (hi - lo) + (2 / alfa) * (lo - y) * (y < lo) + (2 / alfa) * (y - hi) * (y > hi)
    return {"nivel": nivel, "cobertura": float(np.mean((y >= lo) & (y <= hi))), "bajo_el_limite": float(np.mean(y < lo)),
            "sobre_el_limite": float(np.mean(y > hi)), "ancho_medio": float(np.mean(hi - lo)),
            "interval_score": float(np.mean(puntaje))}


def fila(nombre, m):
    return (
        f"  {nombre:16s} | MAE {m['MAE']:6.2f} | RMSE {m['RMSE']:6.2f} "
        f"| WAPE {m['WAPE']:6.1%} | sMAPE {m['sMAPE']:6.1%}"
    )


def tipo_periodo(df: pd.DataFrame) -> np.ndarray:
    especial = df["es_festivo"].eq(1) | df["carnaval_central"].eq(1) | df["semana_santa"].eq(1)
    return np.where(especial, "especial", "normal")


def tipo_hora(df: pd.DataFrame) -> np.ndarray:
    return np.where(df["ts"].dt.hour.isin(HORAS_PICO), "pico", "valle")


def desglose(df: pd.DataFrame, y_col: str, pred_col: str, base_col: str | None = None) -> dict:
    grupos = {"ruta": df["id_ruta"].astype(int), "tipo_dia": tipo_periodo(df), "hora": tipo_hora(df)}
    resultado = {}
    for nombre, clave in grupos.items():
        resultado[nombre] = {}
        for valor, g in df.groupby(clave):
            fila_m = {"modelo": metricas(g[y_col], g[pred_col]), "franjas": int(len(g)),
                      "sesgo": float((g[pred_col] - g[y_col]).mean())}
            if base_col:
                fila_m["linea_base"] = metricas(g[y_col], g[base_col])
            resultado[nombre][str(valor)] = fila_m
    return resultado
