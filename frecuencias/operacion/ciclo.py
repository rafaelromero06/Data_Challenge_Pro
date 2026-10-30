import argparse
import os

import pandas as pd
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError

from frecuencias import db, trazas
from frecuencias.alertas import reglas
from frecuencias.analitica import monitoreo
from frecuencias.analitica.carga import cargar_calendario
from frecuencias.analitica.serie import preparar_serie
from frecuencias.config import ARCHIVO_DEMANDA, CARPETA_DATOS, PASO_MIN
from frecuencias.inferencia import retencion
from frecuencias.inferencia.modelos import cargar_modelos
from frecuencias.inferencia.prediccion import Predictor
from frecuencias.ingesta import historico
from frecuencias.observabilidad import (AVANCE, EJECUCIONES, LATENCIA_DATOS, MONITOREO, configurar_logs,
                                        describir_error, log)
from frecuencias.operacion import reentrenamiento, servicio, trabajos
from frecuencias.optimizacion.parametros import valor
from frecuencias.persistencia import ejecuciones, parametros
from frecuencias.persistencia import monitoreo as monitoreo_bd
from frecuencias.persistencia.tablas import modelo, prediccion
from frecuencias.resiliencia import CircuitoAbierto
from frecuencias.segmentos import carga as segmentos_carga
from frecuencias.tiempo import a_local, a_zona, ahora_utc

ERRORES_FUENTE = (OperationalError, InterfaceError, CircuitoAbierto)

HORAS_REFERENCIA = range(5, 21)
DIAS_MONITOREO = 7
DIAS_MINIMOS_MONITOREO = 3
DIAS_HISTORIA_VIAJES = 35
DIAS_MAXIMOS_PENDIENTES = 7
DIAS_ALERTAS_DESCARTADAS = 30
MINUTOS_PLAZO_DESPACHO = 20
REENTRENO_CADA_DIAS = int(os.environ.get("REENTRENO_CADA_DIAS", "7"))
REENTRENO_MIN_HORAS = float(os.environ.get("REENTRENO_MIN_HORAS", "24"))
MAX_INTENTOS = int(os.environ.get("CICLO_INTENTOS", "3"))
ESPERAS_REINTENTO_MIN = (5, 15, 45)
RUTAS = (2, 16, 21)
INDICE_CICLO_EN_CURSO = "ux_ejecucion_ciclo_en_curso"
COLUMNAS_DESPACHO = {"id_ruta", "fechasalida", "fechainicio", "cancelada", "excede_plazo_salida", "idvehiculo"}
AVANCE_OPERACION = (35.0, 75.0)


class CicloEnCurso(RuntimeError):
    pass


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
        e = ejecuciones.iniciar(conn, "ingesta", {"origen": origen, "ciclo": id_padre}, propietario=trabajos.instancia())
        resumen, problemas = historico.cargar(conn, franjas, viajes, e["id"], set(RUTAS))
        ejecuciones.finalizar(conn, e["id"], "exitosa", registros_leidos=len(franjas) + len(viajes),
                              registros_rechazados=resumen["rechazados"], marca_agua_desde=a_zona(desde),
                              marca_agua_hasta=a_zona(hasta), detalle={"origen": origen, "ciclo": id_padre, **resumen})
    EJECUCIONES.labels("ingesta", "exitosa").inc()
    return {"desde": desde, "hasta": hasta, **resumen, "problemas": problemas, "execution_id": e["id"]}


def ventana_operacion(destino, ingesta: dict) -> tuple[pd.Timestamp, pd.Timestamp]:
    with destino.connect() as conn:
        marca = ejecuciones.ultima_marca_agua(conn, "ciclo")
    hasta = ingesta["hasta"]
    if marca is None:
        return ingesta["desde"], hasta
    pendiente = max(a_local(pd.Timestamp(marca)), hasta - pd.Timedelta(days=DIAS_MAXIMOS_PENDIENTES))
    return min(ingesta["desde"], pendiente), hasta


def _corte_activo(paquetes: dict) -> pd.Timestamp | None:
    cortes = [pd.Timestamp(p.meta["data_cutoff"]) for p in paquetes.values() if getattr(p, "meta", None)]
    return min(cortes) if cortes else None


def _errores_recientes(conn, paquetes: dict, serie: pd.DataFrame, desde: pd.Timestamp,
                       hasta: pd.Timestamp) -> pd.DataFrame:
    filas = conn.execute(
        select(prediccion.c.id_ruta, prediccion.c.franja_objetivo, prediccion.c.pasajeros_predichos,
               prediccion.c.limite_inferior, prediccion.c.limite_superior, prediccion.c.model_registry_id,
               modelo.c.horizonte_min)
        .join(modelo, modelo.c.id == prediccion.c.model_registry_id)
        .where(prediccion.c.franja_objetivo >= a_zona(desde), prediccion.c.franja_objetivo < a_zona(hasta))
    ).mappings().all()
    if not filas:
        return pd.DataFrame()
    p = pd.DataFrame(filas)
    p["ts"] = pd.to_datetime(p["franja_objetivo"], utc=True).dt.tz_convert("America/Bogota").dt.tz_localize(None)
    for columna in ("pasajeros_predichos", "limite_inferior", "limite_superior"):
        p[columna] = p[columna].astype(float)
    obs = serie[serie["observado"]][["id_ruta", "ts", "subidas"]]
    activos = {x.model_registry_id for x in paquetes.values()}
    unido = p.merge(obs, on=["id_ruta", "ts"])
    return unido[unido["model_registry_id"].isin(activos)]


def _alertas_de_monitoreo(medidas: list[dict], desde: pd.Timestamp, hasta: pd.Timestamp) -> list[dict]:
    alertas = []
    for m in medidas:
        if m["estado"] != "alerta":
            continue
        argumentos = (m["valor"], m["umbral"], m["horizonte_min"], m["model_registry_id"], desde, hasta, m["muestras"])
        if m["metrica"] == "error_movil_wape":
            alertas += reglas.por_modelo(*argumentos)
        elif m["metrica"] == "cobertura_intervalo":
            alertas += reglas.por_cobertura(*argumentos)
    return alertas


def monitorear(conn, paquetes: dict, serie: pd.DataFrame, hasta: pd.Timestamp, valores: dict) -> tuple[list, list]:
    desde = hasta - pd.Timedelta(days=DIAS_MONITOREO)
    tolerancia = max(valor(valores, "error_pronostico") / 100, 0.15)
    unido = _errores_recientes(conn, paquetes, serie, desde, hasta)
    medidas = [] if unido.empty else monitoreo.errores_por_horizonte(
        unido, tolerancia, valor(valores, "cobertura_intervalo_min") / 100, DIAS_MINIMOS_MONITOREO, desde, hasta)
    alertas = _alertas_de_monitoreo(medidas, desde, hasta)
    corte = _corte_activo(paquetes)
    if corte is not None:
        medidas += monitoreo.deriva(serie, corte, hasta, valor(valores, "deriva_psi_max"))
    conteos = monitoreo_bd.predicciones_por_dia(conn, hasta - pd.Timedelta(days=monitoreo.DIAS_VOLUMEN + 1), hasta)
    medidas.append(monitoreo.volumen(conteos, hasta, valor(valores, "volumen_predicciones_min") / 100))
    ahora = a_local(pd.Timestamp(ahora_utc()))
    inicio = ahora - pd.Timedelta(days=DIAS_ALERTAS_DESCARTADAS)
    medidas += monitoreo.alertas_descartadas(monitoreo_bd.alertas_gestionadas(conn, inicio, ahora),
                                             valor(valores, "alertas_descartadas_max") / 100, inicio, ahora)
    for m in medidas:
        if m["valor"] is not None:
            MONITOREO.labels(m["metrica"], m["alcance"]).set(float(m["valor"]))
    return alertas, medidas


def resumir_monitoreo(medidas: list[dict]) -> dict:
    return {f"{m['metrica']}:{m['alcance']}": {"valor": m["valor"], "estado": m["estado"]} for m in medidas}


def motivo_reentrenamiento(paquetes: dict, hasta: pd.Timestamp, alertas_modelo: list, medidas: list[dict],
                           ultimo=None, ahora=None) -> str | None:
    ahora = pd.Timestamp(ahora or ahora_utc())
    if ultimo is not None and ahora - pd.Timestamp(ultimo) < pd.Timedelta(hours=REENTRENO_MIN_HORAS):
        return None
    if alertas_modelo:
        return "degradacion"
    if any(m["metrica"] == "deriva_psi" and m["estado"] == "alerta" for m in medidas):
        return "deriva"
    corte = _corte_activo(paquetes)
    if corte is not None and (hasta - corte).days >= REENTRENO_CADA_DIAS:
        return "periodicidad"
    return None


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


def alertas_despacho(conn, viajes: pd.DataFrame | None, desde: pd.Timestamp, hasta: pd.Timestamp) -> list[dict]:
    if viajes is None or viajes.empty or not COLUMNAS_DESPACHO <= set(viajes.columns):
        return []
    cierre = (hasta - pd.Timedelta(minutes=MINUTOS_PLAZO_DESPACHO)).floor("h")
    alertas = []
    for id_ruta in RUTAS:
        valores = parametros.vigentes(conn, id_ruta)
        alertas += reglas.por_despachos(viajes, id_ruta, desde.floor("h"), cierre,
                                        valor(valores, "despachos_fallidos_min"),
                                        valor(valores, "despachos_fallidos_pct") / 100)
    return alertas


def iniciar(destino, origen: str, dias: int, reentrenar: str, correlation_id=None,
            solicitado_por: str | None = None, reintento_de: int | None = None, intento: int = 1) -> dict:
    detalle = {"origen": origen, "dias": dias, "reentrenar": reentrenar, "solicitado_por": solicitado_por}
    try:
        with destino.begin() as conn:
            return ejecuciones.iniciar(conn, "ciclo", detalle, correlation_id, trabajos.instancia(), reintento_de,
                                       intento)
    except IntegrityError as error:
        if INDICE_CICLO_EN_CURSO in str(error.orig):
            raise CicloEnCurso("Ya hay un ciclo en curso; consulte GET /api/v1/frequency/executions.") from error
        raise


def preparar_reintento(destino, id_ejecucion: int, solicitado_por: str | None = None) -> tuple[dict, dict]:
    with destino.connect() as conn:
        original = ejecuciones.obtener(conn, id_ejecucion)
    if original is None:
        raise LookupError(f"No existe la ejecucion {id_ejecucion}.")
    if original["tipo"] != "ciclo":
        raise ValueError("Solo se pueden reintentar ejecuciones de tipo ciclo.")
    if original["estado"] not in ejecuciones.ESTADOS_REINTENTABLES:
        raise ValueError(f"La ejecucion {id_ejecucion} esta {original['estado']}; solo se reintentan las fallidas "
                         "o interrumpidas.")
    previo = original["detalle"] or {}
    argumentos = {"origen": previo.get("origen", "base"), "dias": int(previo.get("dias", 1)),
                  "reentrenar": previo.get("reentrenar", "auto")}
    e = iniciar(destino, **argumentos, correlation_id=original["correlation_id"], solicitado_por=solicitado_por,
                reintento_de=id_ejecucion, intento=int(original["intento"]) + 1)
    return e, argumentos


def reanudables(recuperadas: list[dict]) -> list[dict]:
    return [r for r in recuperadas if r["tipo"] == "ciclo" and int(r["intento"]) < MAX_INTENTOS]


def reintentable(error: Exception) -> bool:
    return isinstance(error, ERRORES_FUENTE)


def espera_reintento(intento: int) -> pd.Timedelta:
    return pd.Timedelta(minutes=ESPERAS_REINTENTO_MIN[min(max(intento, 1), len(ESPERAS_REINTENTO_MIN)) - 1])


def _avance(destino, e: dict, etapa: str, porcentaje: float) -> None:
    trabajos.avanzar(destino, e["id"], etapa, porcentaje)
    AVANCE.labels("ciclo").set(round(porcentaje, 1))


def _referencias(dia: pd.Timestamp, desde: pd.Timestamp, hasta: pd.Timestamp, conocidas: set) -> list[pd.Timestamp]:
    candidatas = (dia + pd.Timedelta(hours=hora) for hora in HORAS_REFERENCIA)
    return [r for r in candidatas if desde <= r < hasta and r in conocidas]


def operar(destino, e: dict, predictor: Predictor, serie: pd.DataFrame, viajes: pd.DataFrame | None,
           desde: pd.Timestamp, hasta: pd.Timestamp) -> dict:
    totales = {"predicciones": 0, "recomendaciones": 0, "alertas_nuevas": 0, "alertas_actualizadas": 0}
    dias = pd.date_range(desde.normalize(), hasta, freq="D", inclusive="left")
    conocidas = set(serie["ts"])
    pasos, hechos = max(len(dias) * len(RUTAS), 1), 0
    inicio, fin = AVANCE_OPERACION
    with destino.begin() as conn:
        for dia in dias:
            for id_ruta in RUTAS:
                observado = serie[(serie["id_ruta"] == id_ruta) & serie["observado"]].set_index("ts")["subidas"]
                for referencia in _referencias(dia, desde, hasta, conocidas):
                    r = servicio.ejecutar(conn, predictor, serie, viajes, id_ruta, referencia, e["id"],
                                          bloques_max=1, observado=observado)
                    totales["predicciones"] += len(r["predicciones"])
                    totales["recomendaciones"] += len(r["recomendaciones"])
                    totales["alertas_nuevas"] += r["resumen_alertas"]["nuevas"]
                    totales["alertas_actualizadas"] += r["resumen_alertas"]["actualizadas"]
                hechos += 1
                _avance(destino, e, "prediccion_y_recomendacion", inicio + (fin - inicio) * hechos / pasos)
    return totales


def alertas_del_lote(conn, e: dict, ingesta: dict, serie: pd.DataFrame, viajes: pd.DataFrame | None,
                     ventana: tuple[pd.Timestamp, pd.Timestamp], valores: dict, origen: str) -> dict:
    desde, hasta = ventana
    resumen = {
        "alertas_calidad": servicio.registrar_alertas(
            conn, reglas.por_calidad(ingesta["problemas"], ingesta.get("execution_id")), e["id"]),
        "alertas_despacho": servicio.registrar_alertas(conn, alertas_despacho(conn, viajes, desde, hasta), e["id"]),
        "alertas_retraso": {"nuevas": 0, "actualizadas": 0},
    }
    ultima = serie.loc[serie["observado"], "ts"].max()
    ahora = a_local(pd.Timestamp(ahora_utc()))
    latencia = (ahora - ultima) / pd.Timedelta(hours=1)
    LATENCIA_DATOS.set(latencia)
    if origen == "base":
        resumen["alertas_retraso"] = servicio.registrar_alertas(
            conn, reglas.por_retraso(ultima, ahora, valores.get("datos_atrasados", 2.0)), e["id"])
    resumen["latencia_datos_horas"] = round(latencia, 1)
    return resumen


def _reentrenar(destino, e: dict, motivo: str, paquetes: dict, serie: pd.DataFrame, calendario) -> dict:
    _avance(destino, e, "reentrenamiento", 88)
    with trazas.tramo("ciclo.reentrenamiento", motivo=motivo):
        decisiones = reentrenamiento.campeon_retador(destino, paquetes, serie, calendario)
    resultado = {"reentrenamiento": decisiones, "motivo_reentrenamiento": motivo}
    if any(d.get("resultado") == "activado" for d in decisiones.values()):
        with destino.begin() as conn:
            resultado["retencion"] = retencion.archivar(conn)
    return resultado


def _serializable(ingesta: dict) -> dict:
    return {k: (str(v) if isinstance(v, pd.Timestamp) else v) for k, v in ingesta.items() if k != "problemas"}


def _ejecutar(destino, origen: str, dias: int, reentrenar: str, e: dict) -> dict:
    _avance(destino, e, "ingesta", 5)
    with trazas.tramo("ciclo.ingesta", origen=origen):
        ingesta = ingerir(destino, origen, dias, e["id"])
    desde, hasta = ventana_operacion(destino, ingesta)
    resumen = {"ingesta": _serializable(ingesta), "ventana": [str(desde), str(hasta)]}
    _avance(destino, e, "segmentos", 25)
    with trazas.tramo("ciclo.segmentos"):
        resumen["segmentos"] = actualizar_segmentos(destino, origen, ingesta, e["id"])
    with destino.connect() as conn:
        demanda = historico.leer_demanda(conn)
        viajes = historico.leer_viajes(conn, desde=desde - pd.Timedelta(days=DIAS_HISTORIA_VIAJES))
        valores = parametros.vigentes(conn)
    serie = preparar_serie(demanda)
    calendario = cargar_calendario()
    paquetes = cargar_modelos(destino)
    _avance(destino, e, "prediccion_y_recomendacion", AVANCE_OPERACION[0])
    with trazas.tramo("ciclo.operacion", dias=len(pd.date_range(desde.normalize(), hasta, freq="D"))):
        resumen["operacion"] = operar(destino, e, Predictor(paquetes, calendario), serie, viajes, desde, hasta)
    _avance(destino, e, "alertas", 75)
    with trazas.tramo("ciclo.alertas"), destino.begin() as conn:
        resumen |= alertas_del_lote(conn, e, ingesta, serie, viajes, (desde, hasta), valores, origen)
    _avance(destino, e, "monitoreo", 82)
    with trazas.tramo("ciclo.monitoreo"), destino.begin() as conn:
        alertas_modelo, medidas = monitorear(conn, paquetes, serie, hasta, valores)
        resumen["alertas_modelo"] = servicio.registrar_alertas(conn, alertas_modelo, e["id"])
        resumen["metricas_monitoreo"] = monitoreo_bd.guardar(conn, medidas, e["id"])
        ultimo = ejecuciones.ultimo_reentrenamiento(conn)
    resumen["monitoreo"] = resumir_monitoreo(medidas)
    if reentrenar == "auto":
        motivo = motivo_reentrenamiento(paquetes, hasta, alertas_modelo, medidas, ultimo)
    else:
        motivo = "solicitado" if reentrenar == "siempre" else None
    if motivo:
        resumen |= _reentrenar(destino, e, motivo, paquetes, serie, calendario)
    with destino.begin() as conn:
        ejecuciones.finalizar(conn, e["id"], "exitosa", registros_leidos=ingesta.get("franjas_leidas", 0),
                              marca_agua_desde=a_zona(desde), marca_agua_hasta=a_zona(hasta), detalle=resumen)
    AVANCE.labels("ciclo").set(100)
    EJECUCIONES.labels("ciclo", "exitosa").inc()
    log("ciclo").info("ciclo_terminado", execution_id=e["id"], **resumen["operacion"])
    return {"execution_id": e["id"], **resumen}


def _registrar_fallo(destino, e: dict, error: Exception) -> None:
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


def ejecutar_ciclo(destino, origen: str = "base", dias: int = 1, reentrenar: str = "auto",
                   ejecucion: dict | None = None) -> dict:
    e = ejecucion or iniciar(destino, origen, dias, reentrenar)
    with trabajos.latiendo(destino), trazas.tramo("ciclo", execution_id=e["id"], origen=origen,
                                                   intento=e.get("intento", 1)):
        try:
            return _ejecutar(destino, origen, dias, reentrenar, e)
        except Exception as error:
            _registrar_fallo(destino, e, error)
            raise


def main():
    parser = argparse.ArgumentParser(description="Ciclo incremental: ingesta, prediccion, recomendacion, alertas y reentrenamiento")
    parser.add_argument("--origen", choices=["base", "archivos"], default="base")
    parser.add_argument("--dias", type=int, default=1)
    parser.add_argument("--reentrenar", choices=["auto", "siempre", "nunca"], default="auto")
    args = parser.parse_args()
    configurar_logs()
    trazas.configurar("ciclo")
    motor = db.motor_destino()
    trabajos.recuperar(motor, "cli")
    resultado = ejecutar_ciclo(motor, args.origen, args.dias, args.reentrenar)
    print({k: v for k, v in resultado.items() if k not in ("monitoreo",)})
    trazas.apagar()


if __name__ == "__main__":
    main()
