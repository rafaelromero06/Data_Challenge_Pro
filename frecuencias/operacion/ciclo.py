import argparse
import os

import pandas as pd
from sqlalchemy import select
from sqlalchemy.exc import InterfaceError, OperationalError

from frecuencias import db
from frecuencias.alertas import reglas
from frecuencias.analitica.carga import cargar_calendario
from frecuencias.analitica.serie import preparar_serie
from frecuencias.config import ARCHIVO_DEMANDA, CARPETA_DATOS, PASO_MIN
from frecuencias.inferencia import retencion
from frecuencias.inferencia.evaluacion import metricas
from frecuencias.inferencia.modelos import cargar_modelos
from frecuencias.inferencia.prediccion import Predictor
from frecuencias.ingesta import historico
from frecuencias.observabilidad import EJECUCIONES, LATENCIA_DATOS, describir_error, log
from frecuencias.operacion import reentrenamiento, servicio
from frecuencias.persistencia import ejecuciones, parametros
from frecuencias.persistencia.tablas import modelo, prediccion
from frecuencias.resiliencia import CircuitoAbierto
from frecuencias.segmentos import carga as segmentos_carga
from frecuencias.tiempo import a_local, a_zona, ahora_utc

ERRORES_FUENTE = (OperationalError, InterfaceError, CircuitoAbierto)

HORAS_REFERENCIA = range(5, 21)
DIAS_MONITOREO = 7
DIAS_MINIMOS_MONITOREO = 3
DIAS_HISTORIA_VIAJES = 35
REENTRENO_CADA_DIAS = int(os.environ.get("REENTRENO_CADA_DIAS", "7"))
RUTAS = (2, 16, 21)


def extraer_local(desde: pd.Timestamp, hasta: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    demanda = pd.read_parquet(ARCHIVO_DEMANDA)
    viajes = pd.read_parquet(CARPETA_DATOS / "viajes.parquet")
    return (demanda[(demanda["ts"] >= desde) & (demanda["ts"] < hasta)].reset_index(drop=True),
            viajes[(viajes["fechasalida"] >= desde) & (viajes["fechasalida"] < hasta)].reset_index(drop=True))


def ingerir(destino, origen: str, dias: int, id_padre: int) -> dict:
    with destino.connect() as conn:
        ultima = historico.ultima_franja(conn)
    if ultima is None:
        raise RuntimeError("El historico esta vacio: corra primero python -m frecuencias.ingesta.historico")
    desde = ultima + pd.Timedelta(minutes=PASO_MIN)
    hasta = min(desde.normalize() + pd.Timedelta(days=dias), a_local(pd.Timestamp(ahora_utc())).floor("h"))
    if hasta <= desde:
        return {"desde": desde, "hasta": desde, "franjas_leidas": 0, "problemas": []}
    franjas, viajes = extraer_local(desde, hasta) if origen == "archivos" else historico.extraer_rango(db.motor_fuente(), desde, hasta)
    with destino.begin() as conn:
        e = ejecuciones.iniciar(conn, "ingesta", {"origen": origen, "ciclo": id_padre})
        resumen, problemas = historico.cargar(conn, franjas, viajes, e["id"], set(RUTAS))
        ejecuciones.finalizar(conn, e["id"], "exitosa", registros_leidos=len(franjas) + len(viajes),
                              registros_rechazados=resumen["rechazados"], marca_agua_desde=a_zona(desde),
                              marca_agua_hasta=a_zona(hasta), detalle={"origen": origen, "ciclo": id_padre, **resumen})
    EJECUCIONES.labels("ingesta", "exitosa").inc()
    return {"desde": desde, "hasta": hasta, **resumen, "problemas": problemas, "execution_id": e["id"]}


def monitorear(conn, paquetes: dict, serie: pd.DataFrame, hasta: pd.Timestamp, tolerancia: float) -> tuple[list, dict]:
    desde = hasta - pd.Timedelta(days=DIAS_MONITOREO)
    filas = conn.execute(
        select(prediccion.c.id_ruta, prediccion.c.franja_objetivo, prediccion.c.pasajeros_predichos,
               prediccion.c.model_registry_id, modelo.c.horizonte_min)
        .join(modelo, modelo.c.id == prediccion.c.model_registry_id)
        .where(prediccion.c.franja_objetivo >= a_zona(desde), prediccion.c.franja_objetivo < a_zona(hasta))
    ).mappings().all()
    if not filas:
        return [], {}
    p = pd.DataFrame(filas)
    p["ts"] = pd.to_datetime(p["franja_objetivo"], utc=True).dt.tz_convert("America/Bogota").dt.tz_localize(None)
    p["pasajeros_predichos"] = p["pasajeros_predichos"].astype(float)
    obs = serie[serie["observado"]][["id_ruta", "ts", "subidas"]]
    activos = {p.model_registry_id for p in paquetes.values()}
    unido = p.merge(obs, on=["id_ruta", "ts"])
    unido = unido[unido["model_registry_id"].isin(activos)]
    alertas, resumen = [], {}
    for (h, id_modelo), g in unido.groupby(["horizonte_min", "model_registry_id"]):
        wape = metricas(g["subidas"], g["pasajeros_predichos"])["WAPE"]
        dias = int(g["ts"].dt.normalize().nunique())
        evaluable = dias >= DIAS_MINIMOS_MONITOREO
        resumen[str(int(h))] = {"wape_movil": round(wape, 4), "franjas": int(len(g)), "dias": dias,
                                "evaluable": evaluable, "model_registry_id": int(id_modelo)}
        if evaluable:
            alertas += reglas.por_modelo(wape, tolerancia, int(h), int(id_modelo), desde, hasta, len(g))
    return alertas, resumen


def requiere_reentrenar(paquetes: dict, hasta: pd.Timestamp, alertas_modelo: list) -> bool:
    if alertas_modelo:
        return True
    cortes = [pd.Timestamp(p.meta["data_cutoff"]) for p in paquetes.values()]
    return bool(cortes) and (hasta - min(cortes)).days >= REENTRENO_CADA_DIAS


def actualizar_segmentos(destino, origen: str, ingesta: dict, id_padre: int) -> dict:
    if origen != "base":
        return {"estado": "omitido", "motivo": "el origen archivos no trae coordenadas de las lecturas"}
    if os.environ.get("CICLO_SEGMENTOS", "activa").lower() == "desactivada":
        return {"estado": "omitido", "motivo": "desactivado con CICLO_SEGMENTOS"}
    if not ingesta.get("franjas_leidas"):
        return {"estado": "omitido", "motivo": "sin datos nuevos"}
    try:
        r = segmentos_carga.actualizar(destino, db.motor_fuente(), ingesta["desde"], ingesta["hasta"], id_padre)
    except Exception as error:
        log("ciclo").warning("segmentos_fallidos", error=describir_error(error))
        return {"estado": "fallido", "error": describir_error(error, 300)}
    return {"estado": "exitoso", **{k: r[k] for k in ("execution_id", "viajes", "lecturas", "observaciones",
                                                       "problemas")}}


def iniciar(destino, origen: str, dias: int, reentrenar: str, correlation_id=None,
            solicitado_por: str | None = None) -> dict:
    with destino.begin() as conn:
        return ejecuciones.iniciar(conn, "ciclo", {"origen": origen, "dias": dias, "reentrenar": reentrenar,
                                                   "solicitado_por": solicitado_por}, correlation_id)


def ejecutar_ciclo(destino, origen: str = "base", dias: int = 1, reentrenar: str = "auto",
                   ejecucion: dict | None = None) -> dict:
    e = ejecucion or iniciar(destino, origen, dias, reentrenar)
    try:
        ingesta = ingerir(destino, origen, dias, e["id"])
        resumen = {"ingesta": {k: (str(v) if isinstance(v, pd.Timestamp) else v)
                               for k, v in ingesta.items() if k != "problemas"}}
        resumen["segmentos"] = actualizar_segmentos(destino, origen, ingesta, e["id"])
        with destino.connect() as conn:
            demanda = historico.leer_demanda(conn)
            viajes = historico.leer_viajes(conn, desde=ingesta["desde"] - pd.Timedelta(days=DIAS_HISTORIA_VIAJES))
            valores = parametros.vigentes(conn)
        serie = preparar_serie(demanda)
        calendario = cargar_calendario()
        paquetes = cargar_modelos(destino)
        predictor = Predictor(paquetes, calendario)
        totales = {"predicciones": 0, "recomendaciones": 0, "alertas_nuevas": 0, "alertas_actualizadas": 0}
        dias_nuevos = pd.date_range(ingesta["desde"].normalize(), ingesta["hasta"], freq="D", inclusive="left")
        conocidas = set(serie["ts"])
        with destino.begin() as conn:
            for dia in dias_nuevos:
                for id_ruta in RUTAS:
                    observado = serie[(serie["id_ruta"] == id_ruta) & serie["observado"]].set_index("ts")["subidas"]
                    for hora in HORAS_REFERENCIA:
                        referencia = dia + pd.Timedelta(hours=hora)
                        if referencia >= ingesta["hasta"] or referencia not in conocidas:
                            continue
                        r = servicio.ejecutar(conn, predictor, serie, viajes, id_ruta, referencia, e["id"],
                                              bloques_max=1, observado=observado)
                        totales["predicciones"] += len(r["predicciones"])
                        totales["recomendaciones"] += len(r["recomendaciones"])
                        totales["alertas_nuevas"] += r["resumen_alertas"]["nuevas"]
                        totales["alertas_actualizadas"] += r["resumen_alertas"]["actualizadas"]
            calidad = servicio.registrar_alertas(conn, reglas.por_calidad(ingesta["problemas"], ingesta.get("execution_id")),
                                                 e["id"])
            alertas_modelo, monitoreo = monitorear(conn, paquetes, serie, ingesta["hasta"],
                                                   max(valores.get("error_pronostico", 15.0) / 100, 0.15))
            degradacion = servicio.registrar_alertas(conn, alertas_modelo, e["id"])
            ultima = serie.loc[serie["observado"], "ts"].max()
            ahora = a_local(pd.Timestamp(ahora_utc()))
            LATENCIA_DATOS.set((ahora - ultima) / pd.Timedelta(hours=1))
            retraso = {"nuevas": 0, "actualizadas": 0}
            if origen == "base":
                retraso = servicio.registrar_alertas(
                    conn, reglas.por_retraso(ultima, ahora, valores.get("datos_atrasados", 2.0)), e["id"])
        resumen |= {"operacion": totales, "alertas_calidad": calidad, "monitoreo": monitoreo,
                    "alertas_modelo": degradacion, "alertas_retraso": retraso,
                    "latencia_datos_horas": round((ahora - ultima) / pd.Timedelta(hours=1), 1)}
        if reentrenar == "siempre" or (reentrenar == "auto" and requiere_reentrenar(paquetes, ingesta["hasta"], alertas_modelo)):
            resumen["reentrenamiento"] = reentrenamiento.campeon_retador(destino, paquetes, serie, calendario)
            if any(d.get("resultado") == "activado" for d in resumen["reentrenamiento"].values()):
                with destino.begin() as conn:
                    resumen["retencion"] = retencion.archivar(conn)
        with destino.begin() as conn:
            ejecuciones.finalizar(conn, e["id"], "exitosa", registros_leidos=ingesta.get("franjas_leidas", 0),
                                  marca_agua_desde=a_zona(ingesta["desde"]), marca_agua_hasta=a_zona(ingesta["hasta"]),
                                  detalle=resumen)
        EJECUCIONES.labels("ciclo", "exitosa").inc()
        log("ciclo").info("ciclo_terminado", execution_id=e["id"], **totales)
        return {"execution_id": e["id"], **resumen}
    except Exception as error:
        with destino.begin() as conn:
            if isinstance(error, ERRORES_FUENTE):
                servicio.registrar_alertas(conn, reglas.por_fuente_no_disponible(
                    describir_error(error, 300), a_local(pd.Timestamp(ahora_utc()))), e["id"])
            ejecuciones.finalizar(conn, e["id"], "fallida", error=describir_error(error, 2000))
        EJECUCIONES.labels("ciclo", "fallida").inc()
        if isinstance(error, ERRORES_FUENTE):
            log("ciclo").error("ciclo_fallido", execution_id=e["id"], error=describir_error(error))
        else:
            log("ciclo").exception("ciclo_fallido", execution_id=e["id"])
        raise


def main():
    parser = argparse.ArgumentParser(description="Ciclo incremental: ingesta, prediccion, recomendacion, alertas y reentrenamiento")
    parser.add_argument("--origen", choices=["base", "archivos"], default="base")
    parser.add_argument("--dias", type=int, default=1)
    parser.add_argument("--reentrenar", choices=["auto", "siempre", "nunca"], default="auto")
    args = parser.parse_args()
    resultado = ejecutar_ciclo(db.motor_destino(), args.origen, args.dias, args.reentrenar)
    print({k: v for k, v in resultado.items() if k not in ("monitoreo",)})


if __name__ == "__main__":
    main()
