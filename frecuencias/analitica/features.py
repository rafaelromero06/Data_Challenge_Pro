import numpy as np
import pandas as pd

from frecuencias.config import (
    ID_CARNAVAL_CENTRAL,
    ID_PRECARNAVAL,
    ID_SEMANA_SANTA,
    PASO_MIN,
    PASOS_ANIO,
    PASOS_DIA,
    PASOS_SEMANA,
)

FEATURES = [
    "id_ruta", "minuto_dia", "dia_semana", "mes", "dia_del_anio",
    "tipo_dia", "es_festivo", "precarnaval", "carnaval_central", "semana_santa", "impacto_evento",
    "lag_h", "lag_h_mas_1", "media_1h_previa", "ratio_hoy", "nivel_28d",
    "lag_1d", "lag_7d", "prom_4_semanas", "lag_364d",
    "viajes_1h_previa", "viajes_habituales",
]


def dias_en_rangos(rangos: pd.DataFrame) -> set:
    dias = set()
    for ini, fin in zip(rangos["fecha_inicio"], rangos["fecha_fin"]):
        dias.update(pd.date_range(ini, fin, freq="D"))
    return dias


def agregar_calendario(df: pd.DataFrame, calendario) -> pd.DataFrame:
    estac, novedades = calendario
    dia = df["ts"].dt.normalize()
    d0, d1 = dia.min(), dia.max()
    estac = estac[(pd.to_datetime(estac["fecha_fin"]) >= d0) & (pd.to_datetime(estac["fecha_inicio"]) <= d1)]
    novedades = novedades[pd.to_datetime(novedades["fecha"]).between(d0, d1)]

    festivos = dias_en_rangos(estac[estac["estacionalidad"] == "FESTIVO_NACIONAL"])
    precarnaval = dias_en_rangos(estac[estac["id_estacionalidad"] == ID_PRECARNAVAL])
    carnaval = dias_en_rangos(estac[estac["id_estacionalidad"] == ID_CARNAVAL_CENTRAL])
    semana_santa = dias_en_rangos(estac[estac["id_estacionalidad"] == ID_SEMANA_SANTA])
    df["es_festivo"] = dia.isin(festivos).astype(int)
    df["precarnaval"] = dia.isin(precarnaval).astype(int)
    df["carnaval_central"] = dia.isin(carnaval).astype(int)
    df["semana_santa"] = dia.isin(semana_santa).astype(int)

    tipo = np.select([df["dia_semana"] == 5, df["dia_semana"] == 6], [1, 2], 0)
    tipo = np.where(df["es_festivo"].eq(1), 2, tipo)
    tipo = np.where(df["carnaval_central"].eq(1) & (df["dia_semana"] < 5), 2, tipo)
    df["tipo_dia"] = tipo

    df["impacto_evento"] = 0.0
    hora = df["ts"].dt.time
    for _, n in novedades.iterrows():
        activo = (dia == pd.Timestamp(n["fecha"])) & (hora >= n["hora_inicio"]) & (hora <= n["hora_fin"])
        df.loc[activo, "impacto_evento"] = np.maximum(
            df.loc[activo, "impacto_evento"], float(n["porcentaje_impacto"] or 0)
        )
    return df


def calcular_features(serie: pd.DataFrame, horizonte_min: int, calendario) -> pd.DataFrame:
    h = horizonte_min // PASO_MIN
    df = serie.sort_values(["id_ruta", "ts"]).copy()

    df["minuto_dia"] = df["ts"].dt.hour * 60 + df["ts"].dt.minute
    df["dia_semana"] = df["ts"].dt.dayofweek
    df["mes"] = df["ts"].dt.month
    df["dia_del_anio"] = df["ts"].dt.dayofyear
    df = agregar_calendario(df, calendario)

    g = df.groupby("id_ruta")["subidas"]
    df["lag_h"] = g.shift(h)
    df["lag_h_mas_1"] = g.shift(h + 1)
    df["media_1h_previa"] = g.transform(lambda s: s.shift(h).rolling(4, min_periods=1).mean())
    df["lag_1d"] = g.shift(PASOS_DIA)
    df["lag_7d"] = g.shift(PASOS_SEMANA)
    semanas = [g.shift(PASOS_SEMANA * k) for k in range(1, 5)]
    df["prom_4_semanas"] = pd.concat(semanas, axis=1).mean(axis=1)
    df["lag_364d"] = g.shift(PASOS_ANIO)

    df["nivel_28d"] = g.transform(
        lambda s: s.shift(h).rolling(28 * PASOS_DIA, min_periods=PASOS_DIA).mean()
    )

    acum = df.assign(_s=df["subidas"].fillna(0)).groupby(["id_ruta", df["ts"].dt.normalize()])["_s"].cumsum()
    ga = acum.groupby(df["id_ruta"])
    acum_ref = ga.shift(h)
    acum_habitual = pd.concat([ga.shift(h + PASOS_SEMANA * k) for k in range(1, 5)], axis=1).mean(axis=1)
    df["ratio_hoy"] = (acum_ref / acum_habitual).where(acum_habitual >= 50)

    gv = df.groupby("id_ruta")["nro_viajes"]
    df["viajes_1h_previa"] = gv.transform(lambda s: s.shift(h).rolling(4, min_periods=1).mean())
    df["viajes_habituales"] = pd.concat([gv.shift(PASOS_SEMANA * k) for k in range(1, 5)], axis=1).mean(axis=1)

    df["tiene_base"] = df["lag_7d"].notna() & df["prom_4_semanas"].notna()
    return df


def construir_features(serie: pd.DataFrame, horizonte_min: int, calendario) -> pd.DataFrame:
    df = calcular_features(serie, horizonte_min, calendario)
    return df[df["observado"]].reset_index(drop=True)
