import json
from unittest import mock

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

from frecuencias.analitica.features import FEATURES, construir_features
from frecuencias.analitica.serie import preparar_serie
from frecuencias.config import FEATURE_VERSION
from frecuencias.inferencia import artefactos
from frecuencias.inferencia import cuantiles as cq
from frecuencias.inferencia import modelos as carga_modelos
from frecuencias.inferencia.prediccion import Predictor

PARAMETROS = {"objective": "poisson", "num_leaves": 7, "min_data_in_leaf": 20, "learning_rate": 0.1,
              "deterministic": True, "force_row_wise": True, "seed": 0, "verbose": -1}


def _guardar(carpeta, df, feature_version=FEATURE_VERSION):
    punto = lgb.train(PARAMETROS, lgb.Dataset(df[FEATURES], df["subidas"]), num_boost_round=20)
    modelos_q = cq.reentrenar(df, FEATURES, PARAMETROS, {q: 20 for q in cq.CUANTILES})
    meta = {"model_id": "demanda-prueba-h60", "version": "t", "horizonte_min": 60, "features": FEATURES,
            "feature_version": feature_version, "intervalo": {"nivel": cq.NIVEL, "ajuste_cqr": 1.5},
            "data_cutoff": str(df["ts"].max())}
    return artefactos.guardar(carpeta, punto, modelos_q, meta)


@pytest.fixture(scope="module")
def datos():
    rng = np.random.default_rng(7)
    ts = pd.date_range("2024-01-01", "2024-03-31 23:45", freq="15min")
    ts = ts[(ts.hour >= 5) & (ts.hour < 22)]
    base = 20 + 15 * np.sin(2 * np.pi * (ts.hour * 60 + ts.minute) / 1440)
    serie = preparar_serie(pd.DataFrame({
        "id_ruta": 2, "ts": ts, "subidas": rng.poisson(base).astype(float), "bajadas": rng.poisson(base).astype(float),
        "nro_viajes": 3, "lecturas": 400, "max_subida_lectura": 5}))
    calendario = (pd.DataFrame(columns=["estacionalidad", "id_estacionalidad", "fecha_inicio", "fecha_fin"]),
                  pd.DataFrame(columns=["fecha", "hora_inicio", "hora_fin", "porcentaje_impacto"]))
    return serie, calendario, construir_features(serie, 60, calendario)


@pytest.fixture
def paquete(tmp_path, datos):
    return _guardar(tmp_path / "paquete", datos[2])


def test_paquete_ida_y_vuelta_con_intervalo_y_contribuciones(paquete, datos):
    carpeta, sha = paquete
    cargado = artefactos.cargar(carpeta, sha, 11)
    X = datos[2].head(50)
    p = cargado.predecir(X)
    assert cargado.model_registry_id == 11 and cargado.horizonte == 60
    assert np.all(p["inferior"] <= p["puntual"]) and np.all(p["puntual"] <= p["superior"])
    assert p["contribuciones"].shape == (50, len(FEATURES) + 1)
    assert np.allclose(np.exp(p["contribuciones"].sum(axis=1)), p["puntual"], rtol=1e-6)


def test_detecta_archivo_modificado(paquete):
    carpeta, sha = paquete
    with open(carpeta / "q90.txt", "a", encoding="utf-8") as archivo:
        archivo.write("\n")
    with pytest.raises(artefactos.ArtefactoAlterado, match="q90.txt"):
        artefactos.cargar(carpeta, sha)


def test_detecta_huella_distinta_a_la_registrada(paquete):
    with pytest.raises(artefactos.ArtefactoAlterado, match="huella"):
        artefactos.cargar(paquete[0], "0" * 64)


def test_carga_de_modelos_rechaza_otra_version_de_variables(tmp_path, datos):
    carpeta, sha = _guardar(tmp_path / "otra", datos[2], feature_version="otra")
    activos = tmp_path / "activos.json"
    activos.write_text(json.dumps({"60": {"artefacto": str(carpeta), "sha256": sha}}), encoding="utf-8")
    with mock.patch.object(artefactos, "ARCHIVO_ACTIVOS", activos):
        with pytest.raises(RuntimeError, match="Reentrene"):
            carga_modelos.cargar_modelos()


def test_carga_de_modelos_sin_activos(tmp_path):
    with mock.patch.object(artefactos, "ARCHIVO_ACTIVOS", tmp_path / "no_existe.json"):
        with pytest.raises(RuntimeError, match="Entrene primero"):
            carga_modelos.cargar_modelos()


def test_predictor_usa_el_menor_horizonte_que_cubre_la_antelacion(paquete, datos):
    serie, calendario, _ = datos
    predictor = Predictor({60: artefactos.cargar(paquete[0], paquete[1])}, calendario)
    referencia = pd.Timestamp("2024-03-20 08:00")
    perfil = predictor.perfil(serie[serie["id_ruta"] == 2], referencia, 60)
    assert [r["minutos_antelacion"] for r in perfil] == [15, 30, 45, 60]
    assert {r["horizonte_modelo"] for r in perfil} == {60}
    assert perfil[0]["franja_objetivo"] == referencia + pd.Timedelta(minutes=15)
    assert len(perfil[0]["explicacion"]["variables"]) == 5
    with pytest.raises(ValueError):
        predictor.modelo_para(75)


def test_fuera_del_servicio_habitual_el_intervalo_no_extrapola(paquete, datos):
    serie, calendario, _ = datos
    predictor = Predictor({60: artefactos.cargar(paquete[0], paquete[1])}, calendario)
    perfil = predictor.perfil(serie[serie["id_ruta"] == 2], pd.Timestamp("2024-03-20 21:30"), 60)
    fuera = [r for r in perfil if not r["servicio_habitual"]]
    assert fuera and all(not r["intervalo_calibrado"] for r in fuera)
    for r in fuera:
        assert r["limite_superior"] - r["pasajeros_predichos"] == pytest.approx(1.5, abs=0.011)
    assert all(r["intervalo_calibrado"] for r in perfil if r["servicio_habitual"])


def test_calibracion_cqr_alcanza_la_cobertura_nominal():
    rng = np.random.default_rng(0)
    y = rng.poisson(30, 2000).astype(float)
    P = np.column_stack([np.full(2000, 28.0), np.full(2000, 30.0), np.full(2000, 32.0)])
    ajuste = cq.calibrar(y, P, 0.8)
    inferior, superior = cq.intervalo(P, ajuste)
    assert ajuste > 0
    assert np.mean((y >= inferior) & (y <= superior)) >= 0.8


def test_intervalo_siempre_contiene_la_prediccion_puntual():
    P = np.array([[10.0, 12.0, 14.0], [0.0, 1.0, 2.0]])
    inferior, superior = cq.intervalo(P, 0.0, puntual=[20.0, 0.5])
    assert superior[0] == 20.0 and inferior[1] == 0.0 and inferior.min() >= 0


def test_paquetes_activos_usan_la_version_de_variables_del_codigo():
    activos = artefactos.leer_activos_locales()
    if not activos:
        pytest.skip("No hay paquetes activos en modelos/activos.json")
    for v in activos.values():
        meta = json.loads((artefactos.CARPETA_MODELOS / v["artefacto"] / artefactos.ARCHIVO_META).read_text(encoding="utf-8"))
        assert meta["feature_version"] == FEATURE_VERSION and meta["features"] == FEATURES
