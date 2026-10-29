import numpy as np
import pandas as pd
import pytest

from frecuencias.analitica.features import construir_features
from frecuencias.inferencia.entrenamiento import nuevo_modelo, particion_temporal
from frecuencias.inferencia.evaluacion import desviacion_poisson, metricas, metricas_intervalo, pinball, tipo_periodo


def test_metricas_con_valores_conocidos():
    m = metricas([10, 20, 30], [12, 18, 30])
    assert m["MAE"] == pytest.approx(4 / 3)
    assert m["RMSE"] == pytest.approx(np.sqrt(8 / 3))
    assert m["WAPE"] == pytest.approx(4 / 60)
    assert m["sMAPE"] == pytest.approx(np.mean([2 * 2 / 22, 2 * 2 / 38, 0]), rel=1e-6)


def test_metricas_sin_division_por_cero():
    assert metricas([0, 0], [0, 0])["WAPE"] == 0.0


def test_tipo_periodo():
    df = pd.DataFrame({"es_festivo": [1, 0, 0, 0], "carnaval_central": [0, 1, 0, 0], "semana_santa": [0, 0, 1, 0]})
    assert tipo_periodo(df).tolist() == ["especial", "especial", "especial", "normal"]


def test_particion_temporal_es_cronologica(serie_sintetica, calendario_vacio):
    df = construir_features(serie_sintetica, 60, calendario_vacio)
    train, val, test, corte_val, corte_test = particion_temporal(df)

    assert train["ts"].max() < corte_val <= val["ts"].min()
    assert val["ts"].max() < corte_test <= test["ts"].min()
    assert val["tiene_base"].all() and test["tiene_base"].all()
    assert set(train.index).isdisjoint(val.index) and set(val.index).isdisjoint(test.index)


def test_nuevo_modelo_objetivo_de_conteo():
    modelo = nuevo_modelo(n_estimators=10, early_stopping_rounds=None)
    parametros = modelo.get_params()
    assert parametros["objective"] == "count:poisson"
    assert parametros["n_estimators"] == 10


def test_metricas_de_intervalo_y_pinball():
    y = np.array([10.0, 20.0, 30.0, 40.0])
    m = metricas_intervalo(y, np.array([8.0, 21.0, 25.0, 35.0]), np.array([12.0, 25.0, 35.0, 38.0]), 0.8)
    assert m["cobertura"] == pytest.approx(0.5)
    assert m["ancho_medio"] == pytest.approx(np.mean([4, 4, 10, 3]))
    assert pinball(y, y, 0.9) == 0.0
    assert pinball([10.0], [12.0], 0.1) == pytest.approx(0.9 * 2)


def test_desviacion_poisson_es_cero_con_prediccion_perfecta():
    assert desviacion_poisson([0.0, 5.0, 9.0], [0.0, 5.0, 9.0]) == pytest.approx(0.0, abs=1e-5)
    assert desviacion_poisson([5.0], [10.0]) > 0
