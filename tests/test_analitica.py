import numpy as np
import pandas as pd
import pytest

from frecuencias.analitica.calidad import limpiar_demanda
from frecuencias.analitica.features import FEATURES, agregar_calendario, calcular_features, dias_en_rangos
from frecuencias.analitica.serie import preparar_serie
from frecuencias.config import HORIZONTES_MIN, MAX_SUBIDAS_LECTURA, PASO_MIN


def test_limpieza_descarta_dias_sin_lecturas_y_lecturas_imposibles(demanda_sintetica):
    df = demanda_sintetica.copy()
    dia_vacio = df["ts"].dt.normalize() == pd.Timestamp("2024-01-31")
    df.loc[dia_vacio & (df["id_ruta"] == 2), "lecturas"] = 1
    salto = df.index[(df["id_ruta"] == 16)][100]
    df.loc[salto, "max_subida_lectura"] = MAX_SUBIDAS_LECTURA + 1

    limpio = limpiar_demanda(df, verbose=False)

    assert not ((limpio["id_ruta"] == 2) & (limpio["ts"].dt.normalize() == pd.Timestamp("2024-01-31"))).any()
    assert ((limpio["id_ruta"] == 16) & (limpio["ts"].dt.normalize() == pd.Timestamp("2024-01-31"))).any()
    assert df.loc[salto, "ts"] not in set(limpio.loc[limpio["id_ruta"] == 16, "ts"])
    assert len(limpio) == len(df) - dia_vacio[df["id_ruta"] == 2].sum() - 1


def test_serie_regular_con_faltantes_y_sin_subidas_negativas(demanda_sintetica):
    df = demanda_sintetica.copy()
    df.loc[0, "subidas"] = -5

    serie = preparar_serie(df)

    for _, g in serie.groupby("id_ruta"):
        pasos = g["ts"].diff().dropna().unique()
        assert list(pasos) == [pd.Timedelta(minutes=PASO_MIN)]
        assert len(g) % (24 * 60 // PASO_MIN) == 0
    nocturno = serie[serie["ts"].dt.hour == 2]
    assert not nocturno["observado"].any() and nocturno["subidas"].isna().all()
    assert not serie.loc[serie["ts"] == df.loc[0, "ts"]].query("id_ruta == 2")["observado"].iloc[0]


def test_dias_en_rangos_incluye_extremos():
    rangos = pd.DataFrame({"fecha_inicio": ["2024-02-10"], "fecha_fin": ["2024-02-13"]})
    assert dias_en_rangos(rangos) == set(pd.date_range("2024-02-10", "2024-02-13"))


def test_tipo_de_dia_festivo_y_carnaval():
    ts = pd.to_datetime(["2024-02-10 08:00", "2024-02-12 08:00", "2024-02-14 08:00", "2024-03-25 08:00"])
    df = pd.DataFrame({"ts": ts})
    df["dia_semana"] = df["ts"].dt.dayofweek
    estac = pd.DataFrame({
        "estacionalidad": ["CARNAVAL", "FESTIVO_NACIONAL"],
        "id_estacionalidad": [8, 1],
        "fecha_inicio": ["2024-02-12", "2024-03-25"],
        "fecha_fin": ["2024-02-13", "2024-03-25"],
    })
    novedades = pd.DataFrame(columns=["fecha", "hora_inicio", "hora_fin", "porcentaje_impacto"])

    resultado = agregar_calendario(df, (estac, novedades))

    assert resultado["tipo_dia"].tolist() == [1, 2, 0, 2]
    assert resultado["carnaval_central"].tolist() == [0, 1, 0, 0]
    assert resultado["es_festivo"].tolist() == [0, 0, 0, 1]


@pytest.mark.parametrize("horizonte", HORIZONTES_MIN)
def test_ninguna_variable_usa_el_futuro(serie_sintetica, calendario_vacio, horizonte):
    rng = np.random.default_rng(horizonte)
    corte = pd.Timestamp("2024-03-20 12:00")
    alterada = serie_sintetica.copy()
    futuro = alterada["ts"] > corte
    for col in ["subidas", "bajadas", "nro_viajes"]:
        alterada.loc[futuro, col] = rng.integers(0, 5000, int(futuro.sum()))

    a = calcular_features(serie_sintetica, horizonte, calendario_vacio)
    b = calcular_features(alterada, horizonte, calendario_vacio)
    objetivo = (a["ts"] > corte) & (a["ts"] <= corte + pd.Timedelta(minutes=horizonte))

    assert objetivo.sum() > 0
    for variable in FEATURES:
        pd.testing.assert_series_equal(a.loc[objetivo, variable], b.loc[objetivo, variable], check_names=False)


def test_control_con_fuga_es_detectado(serie_sintetica, calendario_vacio):
    corte = pd.Timestamp("2024-03-20 12:00")
    alterada = serie_sintetica.copy()
    alterada.loc[alterada["ts"] > corte, "nro_viajes"] = 999

    a = calcular_features(serie_sintetica, 60, calendario_vacio)
    b = calcular_features(alterada, 60, calendario_vacio)
    objetivo = (a["ts"] > corte) & (a["ts"] <= corte + pd.Timedelta(minutes=60))

    assert not a.loc[objetivo, "nro_viajes"].equals(b.loc[objetivo, "nro_viajes"])
