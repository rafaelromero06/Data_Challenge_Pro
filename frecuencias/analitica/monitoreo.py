import numpy as np
import pandas as pd

from frecuencias.inferencia.evaluacion import metricas

TRAMOS_PSI = 10
PISO_PROPORCION = 1e-4
MUESTRAS_MINIMAS_REFERENCIA = 200
MUESTRAS_MINIMAS_RECIENTES = 50
SEMANAS_REFERENCIA = 8
DIAS_RECIENTES = 7
HORAS_SERVICIO = (5, 20)
NIVEL_NOMINAL = 0.8
DIAS_VOLUMEN = 7
ALERTAS_GESTIONADAS_MINIMAS = 5
ESTADOS_GESTIONADOS = ("cerrada", "descartada")


def psi(referencia, reciente, tramos: int = TRAMOS_PSI) -> float | None:
    referencia = np.asarray(referencia, dtype=float)
    reciente = np.asarray(reciente, dtype=float)
    if len(referencia) < MUESTRAS_MINIMAS_REFERENCIA or len(reciente) < MUESTRAS_MINIMAS_RECIENTES:
        return None
    cortes = np.unique(np.quantile(referencia, np.linspace(0, 1, tramos + 1)))
    if len(cortes) < 3:
        return None
    cortes[0], cortes[-1] = -np.inf, np.inf
    esperado = np.clip(np.histogram(referencia, cortes)[0] / len(referencia), PISO_PROPORCION, None)
    actual = np.clip(np.histogram(reciente, cortes)[0] / len(reciente), PISO_PROPORCION, None)
    return round(float(np.sum((actual - esperado) * np.log(actual / esperado))), 4)


def estado_de(valor, en_alerta: bool, evaluable: bool = True) -> str:
    if valor is None or not evaluable:
        return "sin_datos"
    return "alerta" if en_alerta else "ok"


def _en_servicio(serie: pd.DataFrame) -> pd.DataFrame:
    return serie[serie["observado"] & serie["ts"].dt.hour.between(*HORAS_SERVICIO)]


def deriva(serie: pd.DataFrame, corte: pd.Timestamp, hasta: pd.Timestamp, umbral: float) -> list[dict]:
    obs = _en_servicio(serie)
    referencia = obs[(obs["ts"] < corte) & (obs["ts"] >= corte - pd.Timedelta(weeks=SEMANAS_REFERENCIA))]
    desde = hasta - pd.Timedelta(days=DIAS_RECIENTES)
    recientes = obs[(obs["ts"] >= desde) & (obs["ts"] < hasta)]
    resultado = []
    for ruta in sorted(obs["id_ruta"].unique()):
        ref = referencia.loc[referencia["id_ruta"] == ruta, "subidas"]
        rec = recientes.loc[recientes["id_ruta"] == ruta, "subidas"]
        valor = psi(ref, rec)
        resultado.append({
            "metrica": "deriva_psi", "alcance": f"ruta:{int(ruta)}", "id_ruta": int(ruta), "valor": valor,
            "umbral": umbral, "muestras": int(len(rec)), "ventana_desde": desde, "ventana_hasta": hasta,
            "estado": estado_de(valor, valor is not None and valor > umbral),
            "detalle": {"referencia": [str(corte - pd.Timedelta(weeks=SEMANAS_REFERENCIA)), str(corte)],
                        "muestras_referencia": int(len(ref)),
                        "media_referencia": round(float(ref.mean()), 2) if len(ref) else None,
                        "media_reciente": round(float(rec.mean()), 2) if len(rec) else None},
        })
    return resultado


def errores_por_horizonte(unido: pd.DataFrame, tolerancia_wape: float, cobertura_minima: float,
                          dias_minimos: int, desde: pd.Timestamp, hasta: pd.Timestamp) -> list[dict]:
    resultado = []
    for (h, id_modelo), g in unido.groupby(["horizonte_min", "model_registry_id"]):
        dias = int(g["ts"].dt.normalize().nunique())
        evaluable = dias >= dias_minimos
        wape = metricas(g["subidas"], g["pasajeros_predichos"])["WAPE"]
        con_intervalo = g.dropna(subset=["limite_inferior", "limite_superior"])
        dentro = ((con_intervalo["subidas"] >= con_intervalo["limite_inferior"])
                  & (con_intervalo["subidas"] <= con_intervalo["limite_superior"]))
        cobertura = round(float(dentro.mean()), 4) if len(con_intervalo) else None
        comunes = {"alcance": f"h{int(h)}", "horizonte_min": int(h), "model_registry_id": int(id_modelo),
                   "muestras": int(len(g)), "ventana_desde": desde, "ventana_hasta": hasta,
                   "detalle": {"dias": dias, "evaluable": evaluable}}
        resultado.append(comunes | {
            "metrica": "error_movil_wape", "valor": round(wape, 4), "umbral": tolerancia_wape,
            "estado": estado_de(wape, wape > tolerancia_wape, evaluable)})
        resultado.append(comunes | {
            "metrica": "cobertura_intervalo", "valor": cobertura, "referencia": NIVEL_NOMINAL,
            "umbral": cobertura_minima,
            "estado": estado_de(cobertura, cobertura is not None and cobertura < cobertura_minima, evaluable)})
    return resultado


def volumen(conteos: pd.Series, hasta: pd.Timestamp, minimo: float) -> dict:
    dia = (hasta - pd.Timedelta(days=1)).normalize()
    conteos = pd.Series(conteos.to_numpy(dtype=float), index=pd.DatetimeIndex(conteos.index))
    previos = conteos[(conteos.index < dia) & (conteos.index >= dia - pd.Timedelta(days=DIAS_VOLUMEN))]
    previos = previos[previos > 0]
    valor = float(conteos.get(dia, 0))
    referencia = float(previos.median()) if len(previos) else None
    proporcion = None if not referencia else valor / referencia
    return {"metrica": "volumen_predicciones", "alcance": "global", "valor": valor, "referencia": referencia,
            "umbral": round(minimo * referencia, 2) if referencia else None, "muestras": int(len(previos)),
            "ventana_desde": dia, "ventana_hasta": dia + pd.Timedelta(days=1),
            "estado": estado_de(proporcion, proporcion is not None and proporcion < minimo),
            "detalle": {"proporcion_del_habitual": None if proporcion is None else round(proporcion, 4),
                        "dias_referencia": [str(d.date()) for d in previos.index]}}


def alertas_descartadas(gestionadas: pd.DataFrame, maximo: float, desde, hasta) -> list[dict]:
    resultado = []
    grupos = [("global", gestionadas)] + [(codigo, g) for codigo, g in gestionadas.groupby("codigo")]
    for alcance, g in grupos:
        total = int(len(g))
        descartadas = int((g["estado"] == "descartada").sum()) if total else 0
        valor = round(descartadas / total, 4) if total else None
        resultado.append({
            "metrica": "alertas_descartadas", "alcance": alcance, "valor": valor, "umbral": maximo, "muestras": total,
            "ventana_desde": desde, "ventana_hasta": hasta,
            "estado": estado_de(valor, valor is not None and valor > maximo, total >= ALERTAS_GESTIONADAS_MINIMAS),
            "detalle": {"descartadas": descartadas, "gestionadas": total},
        })
    return resultado
