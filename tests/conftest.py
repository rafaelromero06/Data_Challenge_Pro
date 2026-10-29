import numpy as np
import pandas as pd
import pytest

from frecuencias.analitica.serie import preparar_serie


@pytest.fixture
def calendario_vacio():
    return (
        pd.DataFrame(columns=["estacionalidad", "id_estacionalidad", "fecha_inicio", "fecha_fin"]),
        pd.DataFrame(columns=["fecha", "hora_inicio", "hora_fin", "porcentaje_impacto"]),
    )


@pytest.fixture
def demanda_sintetica():
    rng = np.random.default_rng(7)
    ts = pd.date_range("2024-01-01", "2024-03-31 23:45", freq="15min")
    ts = ts[(ts.hour >= 5) & (ts.hour < 22)]
    filas = []
    for id_ruta, escala in [(2, 1.0), (16, 2.0)]:
        base = escala * (20 + 15 * np.sin(2 * np.pi * (ts.hour * 60 + ts.minute) / 1440))
        filas.append(pd.DataFrame({
            "id_ruta": id_ruta, "ts": ts, "subidas": rng.poisson(base).astype(float),
            "bajadas": rng.poisson(base).astype(float), "nro_viajes": 3, "lecturas": 400,
            "max_subida_lectura": 5,
        }))
    return pd.concat(filas, ignore_index=True)


@pytest.fixture
def serie_sintetica(demanda_sintetica):
    return preparar_serie(demanda_sintetica)
