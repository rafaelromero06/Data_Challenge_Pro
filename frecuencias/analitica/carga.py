import os

import numpy as np
import pandas as pd

from frecuencias.analitica.calidad import limpiar_demanda
from frecuencias.config import ARCHIVO_DEMANDA, CARPETA_DATOS, PASO_MIN, RANDOM_SEED
from frecuencias.db import motor_fuente
from frecuencias.ingesta.consultas import QUERY_ESTACIONALIDADES, QUERY_NOVEDADES


def cargar_datos() -> pd.DataFrame:
    if ARCHIVO_DEMANDA.exists():
        return limpiar_demanda(pd.read_parquet(ARCHIVO_DEMANDA))
    print(f"(no existe {ARCHIVO_DEMANDA.name} -> datos sinteticos; para los reales corra "
          "python -m frecuencias.ingesta.extraccion)")
    return _generar_datos_sinteticos()


def cargar_calendario():
    if (CARPETA_DATOS / "estacionalidades.parquet").exists():
        estac = pd.read_parquet(CARPETA_DATOS / "estacionalidades.parquet")
        novedades = pd.read_parquet(CARPETA_DATOS / "novedades.parquet")
        for col in ["hora_inicio", "hora_fin"]:
            novedades[col] = pd.to_datetime(novedades[col], format="%H:%M:%S").dt.time
        return estac, novedades
    if os.environ.get("SOBUSA_DB_HOST"):
        engine = motor_fuente()
        with engine.connect() as conn:
            return pd.read_sql(QUERY_ESTACIONALIDADES, conn), pd.read_sql(QUERY_NOVEDADES, conn)
    return (
        pd.DataFrame(columns=["estacionalidad", "id_estacionalidad", "fecha_inicio", "fecha_fin"]),
        pd.DataFrame(columns=["fecha", "hora_inicio", "hora_fin", "porcentaje_impacto"]),
    )


def _generar_datos_sinteticos() -> pd.DataFrame:
    rng = np.random.default_rng(RANDOM_SEED)
    dias = pd.date_range("2023-01-01", "2025-06-30", freq="D")
    franjas = pd.timedelta_range("04:30:00", "21:45:00", freq=f"{PASO_MIN}min")
    minutos = franjas.total_seconds().values / 60

    perfil = (
        4
        + 30 * np.exp(-((minutos - 420) ** 2) / (2 * 60 ** 2))
        + 24 * np.exp(-((minutos - 1050) ** 2) / (2 * 75 ** 2))
    )

    partes = []
    for id_ruta, escala in [(2, 1.0), (10, 1.4), (11, 0.8)]:
        nivel = np.exp(np.cumsum(rng.normal(0, 0.03, len(dias))))
        nivel = nivel / nivel.mean()
        for i, dia in enumerate(dias):
            factor = escala * nivel[i] * (0.6 if dia.dayofweek >= 5 else 1.0)
            partes.append(
                pd.DataFrame(
                    {
                        "id_ruta": id_ruta,
                        "ts": dia + franjas,
                        "subidas": rng.poisson(perfil * factor),
                        "bajadas": rng.poisson(perfil * factor * 0.95),
                        "nro_viajes": 3,
                    }
                )
            )
    return pd.concat(partes, ignore_index=True)
