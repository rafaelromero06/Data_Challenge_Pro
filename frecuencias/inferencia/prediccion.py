import math
import time

import numpy as np
import pandas as pd

from frecuencias.analitica.features import calcular_features
from frecuencias.config import PASO_MIN

DIAS_HISTORIA = 371
VARIABLES_EXPLICACION = 5
DESCRIPCIONES = {
    "id_ruta": "ruta", "minuto_dia": "hora del dia", "dia_semana": "dia de la semana", "mes": "mes",
    "dia_del_anio": "dia del anio", "tipo_dia": "tipo de dia", "es_festivo": "festivo", "precarnaval": "precarnaval",
    "carnaval_central": "carnaval", "semana_santa": "semana santa", "impacto_evento": "evento programado",
    "lag_h": "ultimo dato conocido", "lag_h_mas_1": "dato anterior al ultimo", "media_1h_previa": "ultima hora conocida",
    "ratio_hoy": "marcha del dia frente a lo habitual", "nivel_28d": "nivel de los ultimos 28 dias",
    "lag_1d": "misma franja ayer", "lag_7d": "misma franja hace una semana",
    "prom_4_semanas": "promedio de las ultimas 4 semanas", "lag_364d": "misma franja hace un anio",
    "viajes_1h_previa": "viajes de la ultima hora", "viajes_habituales": "viajes habituales en la franja",
    "rollstd_4": "variabilidad de la ultima hora", "rollmean_96": "promedio de las ultimas 24 h",
    "rollstd_96": "variabilidad de las ultimas 24 h", "is_peak": "hora pico",
    "otras_rutas_lag_h": "demanda reciente de las otras rutas",
}


def serie_conocida(ruta: pd.DataFrame, referencia: pd.Timestamp, hasta: pd.Timestamp) -> pd.DataFrame:
    inicio = max(referencia.normalize() - pd.Timedelta(days=DIAS_HISTORIA), ruta["ts"].min())
    grilla = pd.date_range(inicio, hasta, freq=f"{PASO_MIN}min")
    g = ruta.set_index("ts").reindex(grilla).rename_axis("ts").reset_index()
    g["id_ruta"] = ruta["id_ruta"].iloc[0]
    g.loc[g["ts"] > referencia, ["subidas", "bajadas", "nro_viajes"]] = np.nan
    g["observado"] = g["subidas"].notna()
    return g


def _valor(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return round(float(v), 4)


def explicacion(contribuciones: np.ndarray, fila: pd.Series, features: list[str]) -> dict:
    aportes = pd.Series(contribuciones[:-1], index=features)
    principales = aportes.abs().sort_values(ascending=False).head(VARIABLES_EXPLICACION).index
    return {
        "base": round(float(np.exp(contribuciones[-1])), 2),
        "variables": [{"variable": v, "descripcion": DESCRIPCIONES.get(v, v), "valor": _valor(fila[v]),
                       "efecto_pct": round(100 * (math.exp(aportes[v]) - 1), 1)} for v in principales],
    }


class Predictor:
    def __init__(self, paquetes: dict, calendario):
        self.paquetes = dict(sorted(paquetes.items()))
        self.calendario = calendario

    @property
    def horizontes(self) -> list[int]:
        return list(self.paquetes)

    def modelo_para(self, minutos: int) -> int:
        for h in self.horizontes:
            if h >= minutos:
                return h
        raise ValueError(f"Ningun modelo cubre {minutos} minutos de antelacion.")

    def perfil(self, ruta: pd.DataFrame, referencia: pd.Timestamp, hasta_min: int | None = None) -> list[dict]:
        inicio = time.perf_counter()
        limite = hasta_min or max(self.horizontes)
        hist = serie_conocida(ruta, referencia, referencia + pd.Timedelta(minutes=limite))
        necesarios = sorted({self.modelo_para(k * PASO_MIN) for k in range(1, limite // PASO_MIN + 1)})
        variables = {h: calcular_features(hist, h, self.calendario).set_index("ts") for h in necesarios}
        resultados = []
        for k in range(1, limite // PASO_MIN + 1):
            minutos = k * PASO_MIN
            h = self.modelo_para(minutos)
            paquete = self.paquetes[h]
            objetivo = referencia + pd.Timedelta(minutes=minutos)
            X = variables[h].loc[[objetivo]]
            p = paquete.predecir(X)
            fila = X.iloc[0]
            habitual = bool(pd.notna(fila.get("prom_4_semanas")))
            puntual = float(p["puntual"][0])
            inferior, superior = float(p["inferior"][0]), float(p["superior"][0])
            if not habitual:
                ajuste = float(paquete.meta["intervalo"]["ajuste_cqr"])
                inferior, superior = max(puntual - ajuste, 0.0), puntual + ajuste
            resultados.append({
                "franja_objetivo": objetivo, "minutos_antelacion": minutos, "horizonte_modelo": h,
                "pasajeros_predichos": round(puntual, 2),
                "limite_inferior": round(inferior, 2),
                "limite_superior": round(superior, 2),
                "intervalo_calibrado": habitual,
                "nivel_confianza": paquete.meta["intervalo"]["nivel"],
                "explicacion": explicacion(p["contribuciones"][0], fila, paquete.features),
                "variables": {v: _valor(fila[v]) for v in paquete.features},
                "linea_base_prom_4_semanas": _valor(fila.get("prom_4_semanas")),
                "servicio_habitual": habitual,
                "model_id": paquete.meta["model_id"], "version": paquete.meta["version"],
                "model_registry_id": paquete.model_registry_id, "feature_version": paquete.meta["feature_version"],
                "data_cutoff": paquete.meta.get("data_cutoff"),
            })
        latencia = (time.perf_counter() - inicio) * 1000 / max(len(resultados), 1)
        for r in resultados:
            r["latencia_ms"] = round(latencia, 2)
        return resultados
