import pandas as pd

SEMANAS_REFERENCIA = 4
TOLERANCIA_CIERRE = pd.Timedelta(minutes=1)
FRACCION_MINIMA_SERVICIO = 0.25


def _programados(viajes: pd.DataFrame, id_ruta: int) -> pd.Series:
    return pd.to_datetime(viajes.loc[viajes["id_ruta"] == id_ruta, "fechasalida"])


def ventana_servicio(viajes: pd.DataFrame, id_ruta: int, dia: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    salidas = _programados(viajes, id_ruta)
    base = pd.Timestamp(dia).normalize()
    aperturas, cierres = [], []
    for k in range(1, SEMANAS_REFERENCIA + 1):
        otro = base - pd.Timedelta(weeks=k)
        del_dia = salidas[(salidas >= otro) & (salidas < otro + pd.Timedelta(days=1))]
        if not del_dia.empty:
            aperturas.append(del_dia.min() - otro)
            cierres.append(del_dia.max() - otro)
    candidatas_apertura = [base + pd.Series(aperturas).median()] if aperturas else []
    candidatos_cierre = [base + pd.Series(cierres).median()] if cierres else []
    hoy = salidas[(salidas >= base) & (salidas < base + pd.Timedelta(days=1))]
    if not hoy.empty:
        candidatas_apertura.append(hoy.min())
        candidatos_cierre.append(hoy.max())
    if not candidatas_apertura:
        return None
    return min(candidatas_apertura), max(candidatos_cierre)


def fraccion_en_servicio(ventana: tuple[pd.Timestamp, pd.Timestamp] | None, inicio: pd.Timestamp,
                         fin: pd.Timestamp) -> float:
    if ventana is None:
        return 1.0
    apertura, cierre = ventana
    dentro = min(fin, cierre + TOLERANCIA_CIERRE) - max(inicio, apertura)
    return float(min(max(dentro / (fin - inicio), 0.0), 1.0))


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
