import json

import pandas as pd
from sqlalchemy import and_, func, select
from sqlalchemy.engine import Connection

from frecuencias.config import NOMBRES_RUTAS
from frecuencias.optimizacion.parametros import TIPOS_DIA, parametros_hora, tipo_dia_de
from frecuencias.persistencia import parametros
from frecuencias.persistencia.tablas import instantanea, perfil_segmento, prediccion, segmento
from frecuencias.tiempo import a_local


TOLERANCIA_MAPA_GRADOS = 0.00005
DECIMALES_MAPA = 5
CODIGOS_TIPO_DIA = {nombre: codigo for codigo, nombre in TIPOS_DIA.items()}


def _flotante(valor) -> float | None:
    return None if valor is None else round(float(valor), 3)


def catalogo(conn: Connection, id_ruta: int | None = None, con_geometria: bool = False) -> list[dict]:
    columnas = [segmento.c.id_segmento, segmento.c.id_ruta, segmento.c.nro_segmento, segmento.c.longitud_m,
                segmento.c.inicio_m]
    if con_geometria:
        columnas.append(func.ST_AsGeoJSON(segmento.c.geom).label("geojson"))
    consulta = select(*columnas).order_by(segmento.c.id_ruta, segmento.c.nro_segmento)
    if id_ruta is not None:
        consulta = consulta.where(segmento.c.id_ruta == id_ruta)
    filas = [dict(f) for f in conn.execute(consulta).mappings()]
    for fila in filas:
        if "geojson" in fila:
            fila["geometria"] = json.loads(fila.pop("geojson"))
    return filas


def _propiedades(f: dict, capacidad: float | None, nombres: dict) -> dict:
    carga = _flotante(f["carga_p90"])
    return {"routeId": f["id_ruta"], "routeName": nombres.get(f["id_ruta"]), "segmentId": f["id_segmento"],
            "segmentNumber": f["nro_segmento"], "fromKm": round(float(f["inicio_m"]) / 1000, 2),
            "toKm": round((float(f["inicio_m"]) + float(f["longitud_m"])) / 1000, 2), "loadP90": carga,
            "loadMean": _flotante(f["carga_prom"]), "boardingsMean": _flotante(f["subidas_prom"]),
            "tripsObserved": f["nro_viajes"],
            "occupancyP90": round(carga / capacidad, 3) if carga is not None and capacidad else None,
            "critical": False}


def mapa(conn: Connection, tipo_dia: str, hora: int, id_ruta: int | None = None,
         nombres: dict | None = None) -> dict:
    nombres = nombres or NOMBRES_RUTAS
    union = and_(perfil_segmento.c.id_segmento == segmento.c.id_segmento, perfil_segmento.c.tipo_dia == tipo_dia,
                 perfil_segmento.c.hora == hora)
    consulta = (select(segmento.c.id_segmento, segmento.c.id_ruta, segmento.c.nro_segmento, segmento.c.inicio_m,
                       segmento.c.longitud_m, perfil_segmento.c.carga_p90, perfil_segmento.c.carga_prom,
                       perfil_segmento.c.subidas_prom, perfil_segmento.c.nro_viajes,
                       func.ST_AsGeoJSON(func.ST_Simplify(segmento.c.geom, TOLERANCIA_MAPA_GRADOS),
                                         DECIMALES_MAPA).label("geojson"))
                .select_from(segmento.outerjoin(perfil_segmento, union))
                .order_by(segmento.c.id_ruta, segmento.c.nro_segmento))
    if id_ruta is not None:
        consulta = consulta.where(segmento.c.id_ruta == id_ruta)
    filas = [dict(f) for f in conn.execute(consulta).mappings()]
    capacidades = {r: parametros_hora(parametros.vigentes(conn, r), hora, CODIGOS_TIPO_DIA[tipo_dia]).capacidad
                   for r in {f["id_ruta"] for f in filas}}
    rasgos = [{"type": "Feature", "geometry": json.loads(f["geojson"]),
               "properties": _propiedades(f, capacidades[f["id_ruta"]], nombres)} for f in filas]
    criticos = {}
    for rasgo in rasgos:
        p = rasgo["properties"]
        actual = criticos.get(p["routeId"])
        if p["loadP90"] is not None and (actual is None or p["loadP90"] > actual["loadP90"]):
            criticos[p["routeId"]] = p
    for p in criticos.values():
        p["critical"] = True
    return {"type": "FeatureCollection", "features": rasgos, "dayType": tipo_dia, "hour": hora,
            "capacity": {str(r): c for r, c in capacidades.items()},
            "criticalSegments": [dict(p) for p in criticos.values()]}


def perfil(conn: Connection, id_ruta: int, tipo_dia: str, hora: int) -> list[dict]:
    consulta = (select(perfil_segmento, segmento.c.nro_segmento, segmento.c.inicio_m, segmento.c.longitud_m)
                .join(segmento, segmento.c.id_segmento == perfil_segmento.c.id_segmento)
                .where(perfil_segmento.c.id_ruta == id_ruta, perfil_segmento.c.tipo_dia == tipo_dia,
                       perfil_segmento.c.hora == hora)
                .order_by(segmento.c.nro_segmento))
    return [dict(f) for f in conn.execute(consulta).mappings()]


def tramo_critico(conn: Connection, id_ruta: int, tipo_dia: str, hora: int, capacidad: float) -> dict | None:
    filas = [f for f in perfil(conn, id_ruta, tipo_dia, hora) if f["carga_p90"] is not None]
    if not filas:
        return None
    f = max(filas, key=lambda x: x["carga_p90"])
    return {"segmentId": f["id_segmento"], "segmentNumber": f["nro_segmento"],
            "fromKm": round(float(f["inicio_m"]) / 1000, 2),
            "toKm": round((float(f["inicio_m"]) + float(f["longitud_m"])) / 1000, 2),
            "loadP90": _flotante(f["carga_p90"]), "loadMax": _flotante(f["carga_max"]),
            "occupancyP90": round(float(f["carga_p90"]) / capacidad, 3) if capacidad else None,
            "tripsObserved": f["nro_viajes"], "period": [str(f["periodo_desde"]), str(f["periodo_hasta"])],
            "dayType": tipo_dia, "hour": hora}


def desagregar(conn: Connection, prediction_id) -> dict:
    fila = conn.execute(select(prediccion.c.id_ruta, prediccion.c.franja_objetivo, prediccion.c.pasajeros_predichos,
                               prediccion.c.limite_inferior, prediccion.c.limite_superior,
                               instantanea.c.variables["tipo_dia"].astext.label("tipo_dia"))
                        .join(instantanea, instantanea.c.prediction_id == prediccion.c.id, isouter=True)
                        .where(prediccion.c.id == prediction_id)).mappings().first()
    if fila is None:
        raise LookupError(f"No existe la prediccion {prediction_id}.")
    franja = a_local(pd.Timestamp(fila["franja_objetivo"]))
    codigo_tipo = int(float(fila["tipo_dia"])) if fila["tipo_dia"] is not None else 0
    tipo = tipo_dia_de(codigo_tipo)
    capacidad = parametros_hora(parametros.vigentes(conn, fila["id_ruta"]), franja.hour, codigo_tipo).capacidad
    filas = perfil(conn, fila["id_ruta"], tipo, franja.hour)
    if not filas:
        raise LookupError(f"No hay perfil por segmento para la ruta {fila['id_ruta']}, {tipo}, {franja.hour} h: "
                          "corra python -m frecuencias.segmentos.carga.")
    puntual, inferior, superior = (float(fila[c]) for c in ("pasajeros_predichos", "limite_inferior", "limite_superior"))
    segmentos = [{
        "segmentId": f["id_segmento"], "segmentNumber": f["nro_segmento"],
        "fromKm": round(float(f["inicio_m"]) / 1000, 2), "lengthMeters": float(f["longitud_m"]),
        "boardingShare": float(f["cuota_subidas"]),
        "predictedBoardings": round(puntual * float(f["cuota_subidas"]), 2),
        "lower": round(inferior * float(f["cuota_subidas"]), 2), "upper": round(superior * float(f["cuota_subidas"]), 2),
        "historicalBoardings": {"mean": _flotante(f["subidas_prom"]), "min": f["subidas_min"], "max": f["subidas_max"]},
        "historicalLoad": {"mean": _flotante(f["carga_prom"]), "p90": _flotante(f["carga_p90"]),
                           "max": _flotante(f["carga_max"])},
    } for f in filas]
    return {"predictionId": str(prediction_id), "routeId": fila["id_ruta"], "targetSlot": franja,
            "dayType": tipo, "hour": franja.hour, "predictedPassengers": puntual,
            "criticalSegment": tramo_critico(conn, fila["id_ruta"], tipo, franja.hour, capacidad),
            "segments": segmentos}
