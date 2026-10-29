import pandas as pd

SEMANAS_REFERENCIA = 4


def _despachos(viajes: pd.DataFrame, id_ruta: int) -> pd.Series:
    validos = viajes[(viajes["id_ruta"] == id_ruta) & ~viajes["cancelada"].astype(bool)]
    return pd.to_datetime(validos["fechasalida"])


def buses_actuales(viajes: pd.DataFrame, id_ruta: int, inicio_hora: pd.Timestamp) -> tuple[float | None, str]:
    salidas = _despachos(viajes, id_ruta)
    fin = inicio_hora + pd.Timedelta(hours=1)
    del_dia = salidas[(salidas >= inicio_hora.normalize()) & (salidas < inicio_hora.normalize() + pd.Timedelta(days=1))]
    if not del_dia.empty:
        return float(((del_dia >= inicio_hora) & (del_dia < fin)).sum()), "observada"
    conteos = []
    for k in range(1, SEMANAS_REFERENCIA + 1):
        desde = inicio_hora - pd.Timedelta(weeks=k)
        hubo_servicio = ((salidas >= desde.normalize()) & (salidas < desde.normalize() + pd.Timedelta(days=1))).any()
        if hubo_servicio:
            conteos.append(int(((salidas >= desde) & (salidas < desde + pd.Timedelta(hours=1))).sum()))
    if not conteos:
        return None, "sin_datos"
    return float(pd.Series(conteos).median()), "habitual_4_semanas"


def despachos_en_ruta(viajes: pd.DataFrame, id_ruta: int, inicio_hora: pd.Timestamp, ciclo_min: float) -> int | None:
    salidas = _despachos(viajes, id_ruta)
    desde = inicio_hora - pd.Timedelta(minutes=ciclo_min)
    en_ventana = salidas[(salidas >= desde) & (salidas < inicio_hora)]
    return int(len(en_ventana)) if not salidas[salidas < inicio_hora].empty else None
