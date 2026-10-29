import lightgbm as lgb
import numpy as np
import pandas as pd
from statsmodels.stats.diagnostic import acorr_ljungbox

REZAGOS_RESIDUOS = (4, 96)


def importancia(modelo: lgb.Booster, X: pd.DataFrame, muestra: int = 3000, semilla: int = 42) -> dict:
    ganancia = pd.Series(modelo.feature_importance(importance_type="gain"), index=modelo.feature_name())
    sub = X.sample(min(muestra, len(X)), random_state=semilla)
    contribuciones = modelo.predict(sub, pred_contrib=True, num_iteration=modelo.best_iteration or None)
    shap = pd.Series(np.abs(contribuciones[:, :-1]).mean(axis=0), index=modelo.feature_name())
    return {"ganancia": (ganancia / ganancia.sum()).sort_values(ascending=False).round(4).to_dict(),
            "shap_medio_abs": shap.sort_values(ascending=False).round(4).to_dict()}


def residuos(df: pd.DataFrame, y_col: str, pred_col: str) -> dict:
    resultado = {}
    for id_ruta, g in df.sort_values("ts").groupby("id_ruta"):
        r = (g[y_col] - g[pred_col]).to_numpy()
        if len(r) < 2 * max(REZAGOS_RESIDUOS):
            continue
        media = acorr_ljungbox(r, lags=list(REZAGOS_RESIDUOS))
        varianza = acorr_ljungbox((r - r.mean()) ** 2, lags=list(REZAGOS_RESIDUOS))
        resultado[str(int(id_ruta))] = {
            "ljung_box_p": {str(k): float(v) for k, v in zip(REZAGOS_RESIDUOS, media["lb_pvalue"])},
            "mcleod_li_p": {str(k): float(v) for k, v in zip(REZAGOS_RESIDUOS, varianza["lb_pvalue"])},
            "acf_1": float(pd.Series(r).autocorr(1)),
        }
    return resultado


def sobreajuste(y_train, p_train, y_val, p_val) -> dict:
    e_tr = np.abs(np.asarray(y_train, float) - np.asarray(p_train, float))
    e_va = np.abs(np.asarray(y_val, float) - np.asarray(p_val, float))
    sd = np.sqrt(((len(e_tr) - 1) * e_tr.var(ddof=1) + (len(e_va) - 1) * e_va.var(ddof=1)) / (len(e_tr) + len(e_va) - 2))
    return {"mae_train": float(e_tr.mean()), "mae_validacion": float(e_va.mean()),
            "brecha_normalizada": float(e_tr.mean() / np.std(y_train) - e_va.mean() / np.std(y_val)),
            "d_cohen": float((e_va.mean() - e_tr.mean()) / sd) if sd > 0 else 0.0}
