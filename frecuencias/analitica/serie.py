import pandas as pd

from frecuencias.config import PASO_MIN


def preparar_serie(crudo: pd.DataFrame) -> pd.DataFrame:
    df = crudo.copy()
    df["ts"] = pd.to_datetime(df["ts"]).dt.floor(f"{PASO_MIN}min")

    negativos = int((df["subidas"] < 0).sum())
    if negativos:
        print(f"AVISO: {negativos} filas con subidas negativas, se excluyen (regla de validez 6.3).")
        df = df[df["subidas"] >= 0]

    df = df.groupby(["id_ruta", "ts"], as_index=False)[
        ["subidas", "bajadas", "nro_viajes"]
    ].sum()

    partes = []
    for id_ruta, g in df.groupby("id_ruta"):
        inicio = g["ts"].min().normalize()
        fin = g["ts"].max().normalize() + pd.Timedelta(days=1) - pd.Timedelta(minutes=PASO_MIN)
        grilla = pd.date_range(inicio, fin, freq=f"{PASO_MIN}min")
        g = g.set_index("ts").reindex(grilla).rename_axis("ts").reset_index()
        g["id_ruta"] = id_ruta
        g["observado"] = g["subidas"].notna()
        partes.append(g)
    return pd.concat(partes, ignore_index=True)
