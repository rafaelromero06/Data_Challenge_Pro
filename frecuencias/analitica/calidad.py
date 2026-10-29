import numpy as np
import pandas as pd

from frecuencias.config import MAX_SUBIDAS_LECTURA, MIN_FRACCION_LECTURAS_DIA

FUENTE_DEMANDA = "conteo_pasajeros_destino"
FUENTE_VIAJES = "progvehiculos"
DURACION_MAXIMA_VIAJE_MIN = 360
RAZON_BAJADAS_SOSPECHOSA = 1.5
DISTANCIA_MAXIMA_RUTA_M = 300
REGLAS = {
    "CAL-001": ("completitud", "error", "cuarentena", "Campos obligatorios nulos"),
    "CAL-002": ("validez", "error", "excluido", "Subidas o bajadas negativas"),
    "CAL-003": ("validez", "error", "excluido",
                f"Lectura imposible: mas de {MAX_SUBIDAS_LECTURA} subidas en una lectura del sensor"),
    "CAL-004": ("completitud", "error", "excluido",
                f"Dia sin lecturas del sensor: menos del {MIN_FRACCION_LECTURAS_DIA:.0%} de las lecturas habituales"),
    "CAL-005": ("unicidad", "advertencia", "reportado", "Franja repetida en el lote"),
    "CAL-006": ("relacional", "error", "cuarentena", "Ruta que no existe en la tabla rutas"),
    "CAL-007": ("consistencia", "advertencia", "marcado", "Lecturas con cantidad negativa (reinicio del contador)"),
    "CAL-008": ("consistencia", "advertencia", "marcado",
                f"Bajadas del dia mas de {RAZON_BAJADAS_SOSPECHOSA} veces las subidas"),
    "CAL-101": ("validez", "advertencia", "marcado", "Fin del viaje anterior a su inicio"),
    "CAL-102": ("consistencia", "advertencia", "marcado", "Viaje cancelado registrado como finalizado"),
    "CAL-103": ("validez", "advertencia", "marcado",
                f"Duracion del viaje mayor a {DURACION_MAXIMA_VIAJE_MIN} minutos"),
    "CAL-104": ("relacional", "error", "cuarentena", "Viaje de una ruta que no existe en la tabla rutas"),
    "CAL-201": ("validez", "advertencia", "excluido",
                f"Lecturas del sensor a mas de {DISTANCIA_MAXIMA_RUTA_M} m de la ruta: no se asignan a un segmento"),
    "CAL-202": ("consistencia", "advertencia", "marcado",
                "Viaje sin bajadas registradas: no se puede estimar la carga a bordo por segmento"),
}
OBLIGATORIAS_DEMANDA = ["id_ruta", "ts", "subidas"]
OBLIGATORIAS_VIAJES = ["idprogramacion", "id_ruta", "fechasalida"]


def _mascaras(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    fecha = df["ts"].dt.normalize()
    lect_dia = df.groupby(["id_ruta", fecha])["lecturas"].transform("sum")
    mediana = df.groupby(["id_ruta", fecha])["lecturas"].sum().groupby("id_ruta").median()
    sin_datos = lect_dia < MIN_FRACCION_LECTURAS_DIA * df["id_ruta"].map(mediana)
    imposibles = df["max_subida_lectura"] > MAX_SUBIDAS_LECTURA
    return sin_datos, imposibles


def limpiar_demanda(df: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    sin_datos, imposibles = _mascaras(df)
    if verbose:
        dias = df.loc[sin_datos, ["id_ruta"]].assign(fecha=df.loc[sin_datos, "ts"].dt.normalize()).drop_duplicates()
        print(f"Limpieza: {len(dias)} dias-ruta sin lecturas del sensor y "
              f"{int((imposibles & ~sin_datos).sum())} franjas con lecturas imposibles -> faltantes")
    return df[~sin_datos & ~imposibles].reset_index(drop=True)


def problema(regla: str, fuente: str, clave: dict, valor=None, detalle: str | None = None) -> dict:
    dimension, severidad, tratamiento, descripcion = REGLAS[regla]
    return {"regla": regla, "fuente": fuente, "dimension": dimension, "severidad": severidad,
            "tratamiento": tratamiento, "clave": clave, "valor": None if valor is None else str(valor),
            "detalle": detalle or descripcion}


def _clave_franja(fila) -> dict:
    return {"id_ruta": int(fila.id_ruta), "ts": pd.Timestamp(fila.ts).isoformat()}


def validar_demanda(df: pd.DataFrame, rutas_validas: set[int] | None = None) -> tuple[pd.DataFrame, list[dict]]:
    problemas = []
    nulos = df[OBLIGATORIAS_DEMANDA].isna().any(axis=1)
    for fila in df[nulos].itertuples():
        problemas.append(problema("CAL-001", FUENTE_DEMANDA, {"fila": int(fila.Index)}))
    datos = df[~nulos].copy()
    if rutas_validas is not None:
        ajena = ~datos["id_ruta"].isin(rutas_validas)
        for fila in datos[ajena].itertuples():
            problemas.append(problema("CAL-006", FUENTE_DEMANDA, _clave_franja(fila), fila.id_ruta))
        datos = datos[~ajena]
    repetidas = datos.duplicated(["id_ruta", "ts"], keep="last")
    for fila in datos[repetidas].itertuples():
        problemas.append(problema("CAL-005", FUENTE_DEMANDA, _clave_franja(fila)))
    datos = datos[~repetidas].reset_index(drop=True)

    sin_datos, imposibles = _mascaras(datos)
    negativas = (datos["subidas"] < 0) | (datos["bajadas"].fillna(0) < 0)
    datos["valido"] = ~(sin_datos | imposibles | negativas)
    datos["motivo_invalido"] = np.select(
        [negativas, sin_datos, imposibles], ["CAL-002", "CAL-004", "CAL-003"], default=None)
    datos.loc[datos["valido"], "motivo_invalido"] = None

    for fila in datos[negativas].itertuples():
        problemas.append(problema("CAL-002", FUENTE_DEMANDA, _clave_franja(fila), fila.subidas))
    for fila in datos[imposibles & ~sin_datos].itertuples():
        problemas.append(problema("CAL-003", FUENTE_DEMANDA, _clave_franja(fila), fila.max_subida_lectura))
    fechas = datos["ts"].dt.normalize()
    dias_vacios = datos.loc[sin_datos, ["id_ruta"]].assign(fecha=fechas[sin_datos]).drop_duplicates()
    for fila in dias_vacios.itertuples():
        problemas.append(problema("CAL-004", FUENTE_DEMANDA,
                                   {"id_ruta": int(fila.id_ruta), "fecha": fila.fecha.date().isoformat()}))
    if "lecturas_negativas" in datos:
        con_reinicios = datos["lecturas_negativas"].fillna(0) > 0
        resumen = datos[con_reinicios].groupby(["id_ruta", fechas[con_reinicios]])["lecturas_negativas"].sum()
        for (id_ruta, fecha), cantidad in resumen.items():
            problemas.append(problema("CAL-007", FUENTE_DEMANDA,
                                       {"id_ruta": int(id_ruta), "fecha": fecha.date().isoformat()}, int(cantidad)))
    por_dia = datos[datos["valido"]].groupby(["id_ruta", fechas[datos["valido"]]])[["subidas", "bajadas"]].sum()
    sospechosos = por_dia[por_dia["bajadas"] > RAZON_BAJADAS_SOSPECHOSA * por_dia["subidas"].clip(lower=1)]
    for (id_ruta, fecha), fila in sospechosos.iterrows():
        problemas.append(problema("CAL-008", FUENTE_DEMANDA,
                                   {"id_ruta": int(id_ruta), "fecha": fecha.date().isoformat()},
                                   round(fila["bajadas"] / max(fila["subidas"], 1), 2)))
    return datos, problemas


def validar_viajes(df: pd.DataFrame, rutas_validas: set[int] | None = None) -> tuple[pd.DataFrame, list[dict]]:
    problemas = []
    nulos = df[OBLIGATORIAS_VIAJES].isna().any(axis=1)
    for fila in df[nulos].itertuples():
        problemas.append(problema("CAL-001", FUENTE_VIAJES, {"fila": int(fila.Index)}))
    datos = df[~nulos].drop_duplicates("idprogramacion", keep="last").copy()
    if rutas_validas is not None:
        ajena = ~datos["id_ruta"].isin(rutas_validas)
        for fila in datos[ajena].itertuples():
            problemas.append(problema("CAL-104", FUENTE_VIAJES, {"idprogramacion": int(fila.idprogramacion)},
                                       fila.id_ruta))
        datos = datos[~ajena]
    duracion = (datos["fechafin"] - datos["fechainicio"]).dt.total_seconds() / 60
    for fila in datos[duracion < 0].itertuples():
        problemas.append(problema("CAL-101", FUENTE_VIAJES, {"idprogramacion": int(fila.idprogramacion)}))
    for fila in datos[duracion > DURACION_MAXIMA_VIAJE_MIN].itertuples():
        problemas.append(problema("CAL-103", FUENTE_VIAJES, {"idprogramacion": int(fila.idprogramacion)},
                                   round(float(duracion[fila.Index]), 1)))
    contradictorio = datos["cancelada"].eq("S") & datos["finalizado"].eq("S")
    for fila in datos[contradictorio].itertuples():
        problemas.append(problema("CAL-102", FUENTE_VIAJES, {"idprogramacion": int(fila.idprogramacion)}))
    return datos.reset_index(drop=True), problemas
