import numpy as np
import pandas as pd
import pytest

from frecuencias.analitica import monitoreo

HASTA = pd.Timestamp("2024-03-31")


def test_psi_estable_desplazado_y_con_pocas_muestras():
    rng = np.random.default_rng(3)
    referencia = rng.poisson(20, 2000)
    assert monitoreo.psi(referencia, rng.poisson(20, 500)) < 0.05
    assert monitoreo.psi(referencia, rng.poisson(35, 500)) > 0.25
    assert monitoreo.psi(referencia[:50], rng.poisson(20, 500)) is None
    assert monitoreo.psi(np.zeros(500), np.zeros(100)) is None


def test_deriva_por_ruta_contra_las_semanas_previas_al_corte(serie_sintetica):
    serie = serie_sintetica.copy()
    recientes = (serie["ts"] >= HASTA - pd.Timedelta(days=7)) & (serie["id_ruta"] == 16)
    serie.loc[recientes, "subidas"] *= 3
    medidas = {m["alcance"]: m for m in monitoreo.deriva(serie, pd.Timestamp("2024-03-01"), HASTA, 0.25)}
    assert medidas["ruta:2"]["estado"] == "ok"
    assert medidas["ruta:16"]["estado"] == "alerta"
    assert medidas["ruta:16"]["detalle"]["media_reciente"] > medidas["ruta:16"]["detalle"]["media_referencia"]


def _unido(dias: int, dentro: int) -> pd.DataFrame:
    filas = []
    for d in range(dias):
        for k in range(10):
            ts = HASTA - pd.Timedelta(days=d + 1) + pd.Timedelta(hours=k + 6)
            filas.append({"horizonte_min": 30, "model_registry_id": 38, "ts": ts, "subidas": 100.0, "pasajeros_predichos": 110.0,
                          "limite_inferior": 90.0 if k < dentro else 120.0, "limite_superior": 130.0})
    return pd.DataFrame(filas)


def test_error_movil_y_cobertura_del_intervalo():
    medidas = {m["metrica"]: m for m in monitoreo.errores_por_horizonte(
        _unido(3, 6), 0.15, 0.65, 3, HASTA - pd.Timedelta(days=7), HASTA)}
    assert medidas["error_movil_wape"]["valor"] == pytest.approx(0.1)
    assert medidas["error_movil_wape"]["estado"] == "ok"
    assert medidas["cobertura_intervalo"]["valor"] == pytest.approx(0.6)
    assert medidas["cobertura_intervalo"]["estado"] == "alerta"
    assert medidas["cobertura_intervalo"]["referencia"] == 0.8


def test_metricas_de_error_sin_dias_suficientes_no_alertan():
    medidas = monitoreo.errores_por_horizonte(_unido(2, 0), 0.05, 0.65, 3, HASTA - pd.Timedelta(days=7), HASTA)
    assert {m["estado"] for m in medidas} == {"sin_datos"}


def test_volumen_de_predicciones_frente_al_habitual():
    dias = pd.date_range(HASTA - pd.Timedelta(days=8), periods=8, freq="D")
    conteos = pd.Series([100.0] * 7 + [30.0], index=dias)
    medida = monitoreo.volumen(conteos, HASTA, 0.5)
    assert medida["valor"] == 30.0
    assert medida["referencia"] == 100.0
    assert medida["estado"] == "alerta"
    assert monitoreo.volumen(pd.Series(dtype=float), HASTA, 0.5)["estado"] == "sin_datos"
    assert monitoreo.volumen(pd.Series([100.0] * 8, index=dias), HASTA, 0.5)["estado"] == "ok"


def test_porcentaje_de_alertas_descartadas_global_y_por_codigo():
    gestionadas = pd.DataFrame({"codigo": ["ALT-003"] * 5 + ["ALT-001"] * 2,
                                "estado": ["descartada"] * 3 + ["cerrada"] * 4})
    medidas = {m["alcance"]: m for m in monitoreo.alertas_descartadas(gestionadas, 0.3, HASTA, HASTA)}
    assert medidas["ALT-003"]["valor"] == 0.6
    assert medidas["ALT-003"]["estado"] == "alerta"
    assert medidas["ALT-001"]["estado"] == "sin_datos"
    assert medidas["global"]["valor"] == pytest.approx(3 / 7, abs=1e-4)
    vacias = monitoreo.alertas_descartadas(pd.DataFrame(columns=["codigo", "estado"]), 0.3, HASTA, HASTA)
    assert [m["estado"] for m in vacias] == ["sin_datos"]
