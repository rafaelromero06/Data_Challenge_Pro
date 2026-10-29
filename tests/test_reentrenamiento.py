from types import SimpleNamespace

import pandas as pd

from frecuencias.operacion.reentrenamiento import comparar, inicio_ventana

CORTE = pd.Timestamp("2025-11-29 23:45")


def test_ventana_deja_dias_nuevos_al_candidato():
    assert inicio_ventana(pd.Timestamp("2025-12-12 23:15"), CORTE) == pd.Timestamp("2025-12-06")
    assert inicio_ventana(pd.Timestamp("2025-12-30 23:00"), CORTE, 7) == pd.Timestamp("2025-12-23")


def test_sin_dias_nuevos_suficientes_no_hay_ventana():
    assert inicio_ventana(pd.Timestamp("2025-12-01 10:00"), CORTE) is None
    assert inicio_ventana(CORTE, CORTE) is None


def test_candidato_sin_datos_nuevos_no_se_evalua():
    paquete = SimpleNamespace(meta={"hiperparametros": {}, "franjas_entrenamiento": 100})
    df = pd.DataFrame({"ts": pd.date_range("2025-12-01", periods=200, freq="15min"), "tiene_base": True})
    resultado = comparar(paquete, df, df["ts"].iloc[100])
    assert not resultado["evaluable"] and "datos nuevos" in resultado["motivo"]
