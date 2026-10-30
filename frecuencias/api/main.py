import io
import json
import os
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.encoders import jsonable_encoder
from fastapi.responses import PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.exc import SQLAlchemyError

from frecuencias import db, seguridad, trazas
from frecuencias.analitica.carga import cargar_calendario, cargar_datos
from frecuencias.analitica.serie import preparar_serie
from frecuencias.api import esquemas as esq
from frecuencias.api.errores import registrar_manejadores
from frecuencias.config import CARPETA_DATOS, FEATURE_VERSION, NOMBRES_RUTAS, PASO_MIN, ZONA_HORARIA
from frecuencias.inferencia import artefactos
from frecuencias.inferencia.modelos import cargar_modelos, desde_registro
from frecuencias.inferencia.prediccion import Predictor
from frecuencias.ingesta import historico
from frecuencias.observabilidad import MiddlewareCorrelacion, configurar_logs, describir_error, log
from frecuencias.operacion import ciclo, pronostico, recomendacion, resumen, servicio, trabajos
from frecuencias.persistencia import (alertas, calidad, ejecuciones, monitoreo, parametros, predicciones,
                                      recomendaciones, registro_modelos)
from frecuencias.persistencia.consultas import TAMANO_MAXIMO
from frecuencias.optimizacion import oferta
from frecuencias.segmentos import carga as segmentos_carga
from frecuencias.segmentos import consultas as segmentos_consultas
from frecuencias.tiempo import a_zona, ahora_utc

PREFIJO = "/api/v1"
HORAS_MINIMAS_HISTORIA = 48
FILAS_MAXIMAS_EXPORTACION = 100_000
CARPETA_TABLERO = Path(__file__).resolve().parent.parent / "tablero"
TIPOS_EXPORTACION = {
    "csv": ("text/csv; charset=utf-8", "csv"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
}
ENTORNO = os.environ.get("ENTORNO", "desarrollo").lower()
DOCUMENTACION = ENTORNO != "produccion" or os.environ.get("API_DOCS", "").lower() == "activa"
REANUDAR_CICLOS = os.environ.get("CICLO_REANUDAR", "activa").lower() != "desactivada"

estado: dict = {}
CANDADO = threading.Lock()
CICLO = threading.Lock()


def _leer_historia(motor) -> tuple[pd.DataFrame, pd.DataFrame | None, str]:
    if motor is not None:
        try:
            with motor.connect() as conn:
                demanda = historico.leer_demanda(conn)
                if not demanda.empty:
                    desde = demanda["ts"].max() - pd.Timedelta(days=ciclo.DIAS_HISTORIA_VIAJES)
                    return demanda, historico.leer_viajes(conn, desde=desde), "ai_frecuencias.demand_history"
        except SQLAlchemyError as error:
            log("api").warning("historico_no_disponible", error=type(error).__name__)
    demanda = cargar_datos()
    archivo = CARPETA_DATOS / "viajes.parquet"
    if not archivo.exists():
        return demanda, None, "datos/demanda_15min.parquet"
    viajes = pd.read_parquet(archivo)
    viajes = viajes[viajes["fechasalida"] >= demanda["ts"].max() - pd.Timedelta(days=ciclo.DIAS_HISTORIA_VIAJES)].copy()
    viajes["cancelada"] = viajes["cancelada"].astype(str).str.upper().eq("S")
    return demanda, viajes, "datos/demanda_15min.parquet"


def _cargar_estado(motor) -> None:
    demanda, viajes, fuente = _leer_historia(motor)
    serie = preparar_serie(demanda)
    calendario = cargar_calendario()
    predictor = Predictor(cargar_modelos(motor), calendario)
    nombres = dict(NOMBRES_RUTAS)
    if (CARPETA_DATOS / "rutas.parquet").exists():
        rutas = pd.read_parquet(CARPETA_DATOS / "rutas.parquet")
        nombres = dict(zip(rutas["id_ruta"].astype(int), rutas["nombre"]))
    with CANDADO:
        estado.update(serie=serie, viajes=viajes, calendario=calendario, predictor=predictor, nombres=nombres,
                      fuente=fuente, cargado_en=ahora_utc())
    obs = serie[serie["observado"]]
    log("api").info("estado_cargado", fuente=fuente, franjas_con_servicio=len(obs), ultima_franja=str(obs["ts"].max()),
                    horizontes=predictor.horizontes)


def _reanudar(motor, recuperadas: list[dict]) -> None:
    for r in ciclo.reanudables(recuperadas) if REANUDAR_CICLOS else []:
        if not CICLO.acquire(blocking=False):
            return
        try:
            e, argumentos = ciclo.preparar_reintento(motor, r["id"], "api")
        except Exception as error:
            CICLO.release()
            log("api").warning("ciclo_no_reanudado", execution_id=r["id"], error=describir_error(error))
            continue
        threading.Thread(target=_ciclo_en_segundo_plano, args=(motor, argumentos, e), daemon=True,
                         name=f"ciclo-{e['id']}").start()
        log("api").warning("ciclo_reanudado", execution_id=r["id"], nuevo=e["id"], intento=e["intento"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    configurar_logs()
    estado["motor_ia"] = db.motor_destino() if db.url_destino() else None
    _cargar_estado(estado["motor_ia"])
    if estado["motor_ia"] is not None:
        _reanudar(estado["motor_ia"], trabajos.iniciar_proceso(estado["motor_ia"], "api"))
    yield
    trabajos.detener_proceso()
    if estado["motor_ia"] is not None:
        estado["motor_ia"].dispose()
    estado.clear()


app = FastAPI(
    title="SOBUSA - Frecuencias inteligentes",
    description="Prediccion de demanda por ruta y franja de 15 min con intervalo de confianza, recomendacion de "
                "frecuencia, alertas operativas y ciclo incremental con reentrenamiento controlado.",
    version=FEATURE_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if DOCUMENTACION else None,
    redoc_url=None,
    openapi_url="/openapi.json" if DOCUMENTACION else None,
)
app.add_middleware(MiddlewareCorrelacion)
registrar_manejadores(app)
trazas.configurar("api")
trazas.instrumentar_app(app)
router = APIRouter(prefix=PREFIJO)


def _motor():
    motor = estado.get("motor_ia")
    if motor is None:
        raise HTTPException(503, "La base ai_frecuencias no esta configurada (AI_DB_*).")
    return motor


esquema_clave = APIKeyHeader(name=seguridad.CABECERA, auto_error=False,
                             description="Clave de acceso; se crea con python -m frecuencias.seguridad crear")


def autenticacion_activa() -> bool:
    return os.environ.get("API_AUTENTICACION", "activa").lower() != "desactivada"


def principal(clave: str | None = Security(esquema_clave)) -> dict:
    if not autenticacion_activa():
        return {"nombre": None, "rol": "administrador"}
    if not clave:
        raise HTTPException(401, f"Falta la clave de acceso en la cabecera {seguridad.CABECERA}.")
    with _motor().begin() as conn:
        quien = seguridad.validar(conn, clave)
    if quien is None:
        raise HTTPException(401, "Clave de acceso invalida o revocada.")
    structlog.contextvars.bind_contextvars(usuario=quien["nombre"])
    return quien


def requiere(rol: str):
    def dependencia(quien: dict = Depends(principal)) -> dict:
        if not seguridad.autorizado(quien["rol"], rol):
            raise HTTPException(403, f"Esta operacion requiere el rol {rol}.")
        return quien
    return dependencia


LECTOR, OPERADOR, ADMINISTRADOR = requiere("lector"), requiere("operador"), requiere("administrador")


def _usuario(quien: dict, declarado: str | None) -> str:
    usuario = quien["nombre"] or declarado
    if not usuario:
        raise ValueError("Indique el usuario que realiza la operacion.")
    return usuario


def _vista() -> dict:
    with CANDADO:
        return {k: estado[k] for k in ("serie", "viajes", "calendario", "predictor", "nombres")}


def _limpio(valor):
    if isinstance(valor, dict):
        return {str(k): _limpio(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_limpio(v) for v in valor]
    if isinstance(valor, np.generic):
        valor = valor.item()
    if isinstance(valor, float) and np.isnan(valor):
        return None
    if isinstance(valor, pd.Timestamp):
        return valor.isoformat()
    return valor


def _o_nada(valor) -> float | None:
    return None if valor is None or pd.isna(valor) else round(float(valor), 2)


def _a_local(valor: datetime | None) -> pd.Timestamp | None:
    if valor is None:
        return None
    ts = pd.Timestamp(valor)
    return ts.tz_convert(ZONA_HORARIA).tz_localize(None) if ts.tzinfo is not None else ts


def _a_zona(valor: datetime | None):
    return None if valor is None else a_zona(_a_local(valor))


def _pagina(filas: list[dict], total: int, pagina: int, tamano: int) -> dict:
    return {"items": filas, "page": pagina, "size": min(max(tamano, 1), TAMANO_MAXIMO), "total": total}


def _referencia(obs: pd.DataFrame, referencia: datetime | None) -> pd.Timestamp:
    if referencia is None:
        return obs["ts"].max()
    return _a_local(referencia).floor(f"{PASO_MIN}min")


def _resumida(rec: dict) -> dict:
    return {"headwayMinutes": float(rec["headway_min"]), "busesPerHour": int(rec["buses_por_hora"]),
            "vehiclesRequired": int(rec["vehiculos_requeridos"]), "viable": bool(rec["viable"])}


def _respuesta_prediccion(r: dict, id_ruta: int, referencia: pd.Timestamp, generado: datetime,
                          observado: pd.Series, por_franja: dict) -> dict:
    return {
        "predictionId": str(r["prediction_id"]) if r.get("prediction_id") else None,
        "routeId": id_ruta, "generatedAt": generado, "referenceTime": referencia,
        "targetSlot": r["franja_objetivo"], "horizonMinutes": r["minutos_antelacion"],
        "modelHorizonMinutes": r["horizonte_modelo"], "predictedPassengers": r["pasajeros_predichos"],
        "confidenceInterval": {"lower": r["limite_inferior"], "upper": r["limite_superior"],
                               "level": r["nivel_confianza"]},
        "intervalCalibrated": r.get("intervalo_calibrado", True),
        "actualPassengers": _o_nada(observado.get(r["franja_objetivo"])),
        "baselinePassengers": r["linea_base_prom_4_semanas"], "usualService": r["servicio_habitual"],
        "modelVersion": f"{r['model_id']}@{r['version']}", "featureVersion": r["feature_version"],
        "latencyMs": r["latencia_ms"], "recommendation": por_franja.get(r["franja_objetivo"]),
        "explanation": [f"{v['descripcion']}: {v['efecto_pct']:+.1f} %" for v in r["explicacion"]["variables"]],
        "explanationDetail": _limpio(r["explicacion"]),
    }


def _respuesta_recomendacion(rec: dict) -> dict:
    return _limpio({k: v for k, v in rec.items() if k != "alertas"}
                   | {"recommendationId": str(rec["recommendation_id"]) if rec.get("recommendation_id") else None,
                      "alertas": [{"codigo": a["codigo"], "severidad": a["severidad"], "explicacion": a["explicacion"]}
                                  for a in rec["alertas"]]})


def _correr(peticion: esq.PeticionPrediccion, request: Request, quien: dict) -> dict:
    vista = _vista()
    serie = vista["serie"]
    obs = serie[(serie["id_ruta"] == peticion.id_ruta) & serie["observado"]]
    if obs.empty:
        raise LookupError(f"La ruta {peticion.id_ruta} no tiene datos. Consulte GET {PREFIJO}/rutas.")
    ref = _referencia(obs, peticion.referencia)
    recientes = obs[(obs["ts"] <= ref) & (obs["ts"] > ref - pd.Timedelta(hours=HORAS_MINIMAS_HISTORIA))]
    if recientes.empty:
        raise HTTPException(422, f"No hay datos de la ruta {peticion.id_ruta} en las {HORAS_MINIMAS_HISTORIA} h "
                                 f"previas a {ref}; la prediccion no seria confiable.")
    observado = obs.set_index("ts")["subidas"]
    generado = ahora_utc()
    motor = estado.get("motor_ia")
    id_ejecucion = None
    if peticion.guardar:
        motor = _motor()
        detalle = {"id_ruta": peticion.id_ruta, "referencia": str(ref), "solicitado_por": quien["nombre"]}
        with motor.begin() as conn:
            id_ejecucion = ejecuciones.iniciar(conn, "prediccion", detalle, request.state.correlation_id)["id"]
        try:
            with motor.begin() as conn:
                r = servicio.ejecutar(conn, vista["predictor"], serie, vista["viajes"], peticion.id_ruta, ref,
                                      id_ejecucion, observado=observado)
                ejecuciones.finalizar(conn, id_ejecucion, "exitosa", registros_leidos=len(r["predicciones"]),
                                      detalle={**detalle, "predicciones": len(r["predicciones"]),
                                               "recomendaciones": len(r["recomendaciones"]),
                                               "alertas": r["resumen_alertas"]})
        except Exception as error:
            with motor.begin() as conn:
                ejecuciones.finalizar(conn, id_ejecucion, "fallida", error=describir_error(error, 2000))
            raise
        predichas, recs, resumen_alertas = r["predicciones"], r["recomendaciones"], r["resumen_alertas"]
    else:
        predichas = pronostico.pronosticar(vista["predictor"], serie, peticion.id_ruta, ref)
        valores = {}
        if motor is not None:
            with motor.connect() as conn:
                valores = parametros.vigentes(conn, peticion.id_ruta)
        recs = recomendacion.recomendar_bloques(predichas, peticion.id_ruta, valores, vista["viajes"], observado)
        resumen_alertas = {"detectadas": sum(len(rec["alertas"]) for rec in recs), "guardadas": False}
    if motor is not None:
        try:
            with motor.connect() as conn:
                for rec in recs:
                    rec["tramo_critico"] = segmentos_consultas.tramo_critico(
                        conn, peticion.id_ruta, rec["parametros_hora"]["tipo_dia"], rec["franja_inicio"].hour,
                        rec["parametros_hora"]["capacidad"])
        except SQLAlchemyError as error:
            log("api").warning("tramo_critico_no_disponible", error=type(error).__name__)
    por_franja = {}
    for rec in recs:
        for franja in pd.date_range(rec["franja_inicio"], rec["franja_fin"], freq=f"{PASO_MIN}min", inclusive="left"):
            por_franja[franja] = _resumida(rec)
    return {
        "routeId": peticion.id_ruta, "routeName": vista["nombres"].get(peticion.id_ruta), "referenceTime": ref,
        "executionId": id_ejecucion, "saved": peticion.guardar,
        "predictions": [_respuesta_prediccion(p, peticion.id_ruta, ref, generado, observado, por_franja)
                        for p in predichas],
        "recommendations": [_respuesta_recomendacion(rec) for rec in recs],
        "alerts": resumen_alertas,
    }


def _ciclo_en_segundo_plano(motor, argumentos: dict, ejecucion: dict) -> None:
    try:
        ciclo.ejecutar_ciclo(motor, argumentos["origen"], argumentos["dias"], argumentos["reentrenar"], ejecucion)
        _cargar_estado(motor)
    except Exception as error:
        log("api").error("ciclo_api_fallido", execution_id=ejecucion["id"], error=describir_error(error))
    finally:
        CICLO.release()


def _estado_base_ia() -> dict:
    motor = estado.get("motor_ia")
    if motor is None:
        return {"estado": "no_configurada"}
    try:
        return {"estado": "ok", "revision": db.revision_esquema_ia(motor)}
    except SQLAlchemyError as error:
        return {"estado": "error", "detalle": type(error).__name__}


def _celda(valor):
    if isinstance(valor, (dict, list)):
        return json.dumps(jsonable_encoder(valor), ensure_ascii=False)
    if isinstance(valor, uuid.UUID):
        return str(valor)
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, datetime) and valor.tzinfo is not None:
        return _a_local(valor)
    return valor


def _todas(funcion, **filtros) -> list[dict]:
    filas, pagina = [], 1
    while len(filas) < FILAS_MAXIMAS_EXPORTACION:
        parte, total = funcion(**filtros, pagina=pagina, tamano=TAMANO_MAXIMO)
        filas += parte
        if not parte or len(filas) >= total:
            break
        pagina += 1
    return filas[:FILAS_MAXIMAS_EXPORTACION]


@app.get("/", include_in_schema=False)
def inicio():
    if CARPETA_TABLERO.exists():
        return RedirectResponse("/tablero/")
    return RedirectResponse("/docs" if DOCUMENTACION else f"{PREFIJO}/salud")


@app.get("/metrics", include_in_schema=False)
def metricas_prometheus():
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/salud")
def salud():
    vista = _vista()
    obs = vista["serie"][vista["serie"]["observado"]]
    return {
        "estado": "ok",
        "horizontes_min": vista["predictor"].horizontes,
        "modelos": {h: f"{p.meta['model_id']}@{p.meta['version']}" for h, p in vista["predictor"].paquetes.items()},
        "fuente_datos": estado["fuente"],
        "datos_cargados_en": estado["cargado_en"],
        "ultima_franja_con_datos": obs["ts"].max(),
        "ciclo_en_curso": CICLO.locked(),
        "base_ai_frecuencias": _estado_base_ia(),
    }


@router.get("/rutas", response_model=list[esq.Ruta], dependencies=[Depends(LECTOR)])
def rutas():
    vista = _vista()
    obs = vista["serie"][vista["serie"]["observado"]]
    return [esq.Ruta(id_ruta=int(id_ruta), nombre=vista["nombres"].get(int(id_ruta)), primera_franja=g["ts"].min(),
                     ultima_franja=g["ts"].max(), franjas_con_servicio=len(g))
            for id_ruta, g in obs.groupby("id_ruta")]


@router.post("/predictions/run", response_model=esq.RespuestaEjecucionPrediccion)
def correr_prediccion(peticion: esq.PeticionPrediccion, request: Request, quien: dict = Depends(OPERADOR)):
    return _correr(peticion, request, quien)


@router.post("/recommendations/calculate")
def calcular_recomendacion(peticion: esq.PeticionPrediccion, request: Request, quien: dict = Depends(OPERADOR)):
    r = _correr(peticion, request, quien)
    return {k: r[k] for k in ("routeId", "routeName", "referenceTime", "executionId", "saved", "recommendations",
                              "alerts")}


@router.get("/predictions", response_model=esq.Pagina, dependencies=[Depends(LECTOR)])
def listar_predicciones(routeId: int | None = None, horizonMinutes: int | None = None,
                        desde: datetime | None = Query(None, alias="from"), hasta: datetime | None = Query(None, alias="to"),
                        modelId: int | None = None, executionId: int | None = None, page: int = 1, size: int = 50):
    with _motor().connect() as conn:
        filas, total = predicciones.consultar(conn, routeId, horizonMinutes, _a_zona(desde), _a_zona(hasta), modelId,
                                              executionId, page, size)
    return _pagina(filas, total, page, size)


@router.get("/predictions/{prediction_id}/segments", dependencies=[Depends(LECTOR)])
def segmentos_de_prediccion(prediction_id: uuid.UUID):
    with _motor().connect() as conn:
        return segmentos_consultas.desagregar(conn, prediction_id)


@router.get("/segments", dependencies=[Depends(LECTOR)])
def listar_segmentos(routeId: int | None = None, geometry: bool = False):
    with _motor().connect() as conn:
        return segmentos_consultas.catalogo(conn, routeId, geometry)


@router.get("/segments/map", dependencies=[Depends(LECTOR)])
def mapa_de_segmentos(dayType: Literal["habil", "sabado", "dom_fest"] = "habil", hour: int = Query(7, ge=0, le=23),
                      routeId: int | None = None):
    with _motor().connect() as conn:
        return segmentos_consultas.mapa(conn, dayType, hour, routeId, _vista()["nombres"])


@router.get("/segments/profile", dependencies=[Depends(LECTOR)])
def perfil_por_segmento(routeId: int, dayType: Literal["habil", "sabado", "dom_fest"] = "habil",
                        hour: int = Query(7, ge=0, le=23)):
    with _motor().connect() as conn:
        filas = segmentos_consultas.perfil(conn, routeId, dayType, hour)
    if not filas:
        raise LookupError(f"No hay perfil por segmento para la ruta {routeId}, {dayType}, {hour} h.")
    return filas


@router.get("/recommendations", response_model=esq.Pagina, dependencies=[Depends(LECTOR)])
def listar_recomendaciones(routeId: int | None = None, desde: datetime | None = Query(None, alias="from"),
                           hasta: datetime | None = Query(None, alias="to"), viable: bool | None = None,
                           executionId: int | None = None, page: int = 1, size: int = 50):
    with _motor().connect() as conn:
        filas, total = recomendaciones.consultar(conn, routeId, _a_zona(desde), _a_zona(hasta), viable, executionId,
                                                 page, size)
    return _pagina(filas, total, page, size)


@router.get("/overview", dependencies=[Depends(LECTOR)])
def resumen_del_dia(day: date | None = None, at: datetime | None = None):
    vista = _vista()
    obs = vista["serie"][vista["serie"]["observado"]]
    ultimo = obs["ts"].max()
    dia = pd.Timestamp(day) if day else ultimo.normalize()
    referencia = _a_local(at) if at else resumen.referencia_por_defecto(dia, _a_local(ahora_utc()))
    desde, hasta = a_zona(dia), a_zona(dia + pd.Timedelta(days=1))
    with _motor().connect() as conn:
        recs = recomendaciones.vigentes_por_hora(conn, desde, hasta)
        abiertas = alertas.abiertas_por_ruta(conn)
        pendientes = alertas.pendientes_en(conn, desde, hasta)
    rutas = []
    for id_ruta in sorted(int(r) for r in obs["id_ruta"].unique()):
        observado = obs[obs["id_ruta"] == id_ruta].set_index("ts")["subidas"]
        ventana = None if vista["viajes"] is None else oferta.ventana_servicio(vista["viajes"], id_ruta, dia)
        rutas.append(resumen.ruta(
            id_ruta, vista["nombres"].get(id_ruta), [r for r in recs if r["id_ruta"] == id_ruta], observado, referencia,
            abiertas.get(id_ruta, {"abiertas": 0, "criticas": 0}),
            [a for a in pendientes if a["id_ruta"] == id_ruta], ventana))
    tipo = segmentos_carga.tipos_dia(pd.Series([dia]), vista["calendario"]).iloc[0]
    return _limpio({"dia": dia.date(), "tipoDia": tipo, "referencia": referencia, "ultimoDato": ultimo,
                    "rutas": rutas})


@router.get("/alerts", response_model=esq.Pagina, dependencies=[Depends(LECTOR)])
def listar_alertas(status: str | None = None, severity: str | None = None, code: str | None = None,
                   routeId: int | None = None, desde: datetime | None = Query(None, alias="from"),
                   hasta: datetime | None = Query(None, alias="to"), windowFrom: datetime | None = None,
                   windowTo: datetime | None = None, page: int = 1, size: int = 50):
    with _motor().connect() as conn:
        filas, total = alertas.consultar(conn, status, severity, code, routeId, _a_zona(desde), _a_zona(hasta), page,
                                         size, ventana_desde=_a_zona(windowFrom), ventana_hasta=_a_zona(windowTo))
    return _pagina(filas, total, page, size)


@router.get("/alerts/groups", response_model=esq.Pagina, dependencies=[Depends(LECTOR)])
def agrupar_alertas(status: str | None = None, severity: str | None = None, code: str | None = None,
                    routeId: int | None = None, windowFrom: datetime | None = None, windowTo: datetime | None = None,
                    page: int = 1, size: int = 50):
    with _motor().connect() as conn:
        grupos, total = alertas.agrupar(conn, status, severity, code, routeId, _a_zona(windowFrom),
                                        _a_zona(windowTo), page, size)
    return _pagina(grupos, total, page, size)


@router.post("/alerts/status")
def cambiar_estado_alertas(peticion: esq.PeticionEstadoAlertas, quien: dict = Depends(OPERADOR)):
    with _motor().begin() as conn:
        return alertas.cambiar_estados(conn, peticion.ids, peticion.estado, _usuario(quien, peticion.usuario),
                                       peticion.comentario, peticion.usuario_asignado)


@router.get("/alerts/summary", dependencies=[Depends(LECTOR)])
def resumir_alertas(routeId: int | None = None, desde: datetime | None = Query(None, alias="from"),
                    hasta: datetime | None = Query(None, alias="to")):
    with _motor().connect() as conn:
        return alertas.resumen(conn, _a_zona(desde), _a_zona(hasta), routeId)


@router.get("/alerts/{id_alerta}", dependencies=[Depends(LECTOR)])
def obtener_alerta(id_alerta: uuid.UUID):
    with _motor().connect() as conn:
        fila = alertas.obtener(conn, id_alerta)
        if fila is None:
            raise LookupError(f"No existe la alerta {id_alerta}.")
        return {**fila, "historial": alertas.historial(conn, id_alerta),
                "notificaciones": calidad.notificaciones(conn, id_alerta)[0]}


@router.patch("/alerts/{id_alerta}/status")
def cambiar_estado_alerta(id_alerta: uuid.UUID, peticion: esq.PeticionEstadoAlerta, quien: dict = Depends(OPERADOR)):
    with _motor().begin() as conn:
        fila = alertas.cambiar_estado(conn, id_alerta, peticion.estado, _usuario(quien, peticion.usuario),
                                      peticion.comentario, peticion.usuario_asignado)
        return {**fila, "historial": alertas.historial(conn, id_alerta)}


@router.post("/frequency/executions", status_code=202)
def crear_ejecucion(peticion: esq.PeticionEjecucion, tareas: BackgroundTasks, request: Request,
                    quien: dict = Depends(OPERADOR)):
    motor = _motor()
    argumentos = peticion.model_dump()
    return _lanzar(motor, tareas, argumentos, lambda: ciclo.iniciar(
        motor, peticion.origen, peticion.dias, peticion.reentrenar, request.state.correlation_id, quien["nombre"]))


def _lanzar(motor, tareas: BackgroundTasks, argumentos: dict, crear) -> dict:
    if not CICLO.acquire(blocking=False):
        raise HTTPException(409, "Ya hay un ciclo en curso en este proceso; consulte GET /api/v1/frequency/executions.")
    try:
        trabajos.recuperar(motor)
        ejecucion = crear()
    except ciclo.CicloEnCurso as error:
        CICLO.release()
        raise HTTPException(409, str(error)) from error
    except Exception:
        CICLO.release()
        raise
    if isinstance(ejecucion, tuple):
        ejecucion, argumentos = ejecucion
    tareas.add_task(_ciclo_en_segundo_plano, motor, argumentos, ejecucion)
    return {"executionId": ejecucion["id"], "correlationId": str(ejecucion["correlation_id"]), "status": "en_curso",
            "attempt": ejecucion.get("intento", 1), "location": f"{PREFIJO}/frequency/executions/{ejecucion['id']}"}


@router.post("/frequency/executions/{id_ejecucion}/retry", status_code=202)
def reintentar_ejecucion(id_ejecucion: int, tareas: BackgroundTasks, quien: dict = Depends(OPERADOR)):
    motor = _motor()
    return _lanzar(motor, tareas, {}, lambda: ciclo.preparar_reintento(motor, id_ejecucion, quien["nombre"]))


@router.get("/frequency/executions", response_model=esq.Pagina, dependencies=[Depends(LECTOR)])
def listar_ejecuciones(tipo: str | None = Query(None, alias="type"), status: str | None = None, page: int = 1,
                       size: int = 50):
    with _motor().connect() as conn:
        filas, total = ejecuciones.listar(conn, tipo, status, page, size)
    return _pagina(filas, total, page, size)


@router.get("/frequency/executions/{id_ejecucion}", dependencies=[Depends(LECTOR)])
def obtener_ejecucion(id_ejecucion: int):
    with _motor().connect() as conn:
        fila = ejecuciones.obtener(conn, id_ejecucion)
        if fila is None:
            raise LookupError(f"No existe la ejecucion {id_ejecucion}.")
        return {**fila, "calidad": calidad.resumen(conn, id_ejecucion)}


@router.get("/monitoring", dependencies=[Depends(LECTOR)])
def ultimo_monitoreo():
    with _motor().connect() as conn:
        filas = monitoreo.ultimos(conn)
    return {"metricas": filas, "alertas": sum(1 for f in filas if f["estado"] == "alerta")}


@router.get("/monitoring/history", response_model=esq.Pagina, dependencies=[Depends(LECTOR)])
def historial_monitoreo(metric: str | None = None, scope: str | None = None,
                        desde: datetime | None = Query(None, alias="from"),
                        hasta: datetime | None = Query(None, alias="to"), page: int = 1, size: int = 50):
    with _motor().connect() as conn:
        filas, total = monitoreo.historial(conn, metric, scope, _a_zona(desde), _a_zona(hasta), page, size)
    return _pagina(filas, total, page, size)


@router.get("/data-quality", response_model=esq.Pagina, dependencies=[Depends(LECTOR)])
def listar_problemas_calidad(executionId: int | None = None, rule: str | None = None, dimension: str | None = None,
                             page: int = 1, size: int = 50):
    with _motor().connect() as conn:
        filas, total = calidad.consultar(conn, executionId, rule, dimension, page, size)
    return _pagina(filas, total, page, size)


@router.get("/notifications", response_model=esq.Pagina, dependencies=[Depends(LECTOR)])
def listar_notificaciones(alertId: uuid.UUID | None = None, page: int = 1, size: int = 50):
    with _motor().connect() as conn:
        filas, total = calidad.notificaciones(conn, alertId, page, size)
    return _pagina(filas, total, page, size)


@router.get("/models", dependencies=[Depends(LECTOR)])
def listar_modelos(horizonMinutes: int | None = None, status: str | None = None):
    vista = _vista()
    cargados = {p.model_registry_id for p in vista["predictor"].paquetes.values()}
    motor = estado.get("motor_ia")
    if motor is None:
        return [{**p.meta, "id": p.model_registry_id, "estado": "activo", "en_memoria": True}
                for p in vista["predictor"].paquetes.values()]
    with motor.connect() as conn:
        filas = registro_modelos.listar(conn, horizonMinutes, status)
    return [{**f, "en_memoria": f["id"] in cargados} for f in filas]


@router.post("/models/{id_modelo}/activate")
def activar_modelo(id_modelo: int, peticion: esq.PeticionActivacion, quien: dict = Depends(ADMINISTRADOR)):
    usuario = _usuario(quien, peticion.usuario)
    with _motor().begin() as conn:
        fila = registro_modelos.obtener(conn, id_modelo)
        if fila is None:
            raise LookupError(f"No existe el modelo {id_modelo}.")
        try:
            paquete = desde_registro({int(fila["horizonte_min"]): fila})[int(fila["horizonte_min"])]
        except artefactos.ArtefactoAlterado as error:
            raise HTTPException(409, f"El artefacto no supera la verificacion de integridad: {error}") from error
        activado = registro_modelos.activar(conn, id_modelo, usuario)
    artefactos.marcar_activo_local(paquete.horizonte, paquete.carpeta, paquete.sha, id_modelo)
    with CANDADO:
        paquetes = {**estado["predictor"].paquetes, paquete.horizonte: paquete}
        estado["predictor"] = Predictor(paquetes, estado["calendario"])
    log("api").info("modelo_activado", model_registry_id=id_modelo, horizonte=paquete.horizonte, usuario=usuario)
    return activado


@router.get("/parameters", dependencies=[Depends(LECTOR)])
def listar_parametros(onlyCurrent: bool = True):
    with _motor().connect() as conn:
        return parametros.listar(conn, onlyCurrent)


@router.put("/parameters/{clave}")
def publicar_parametro(clave: str, peticion: esq.PeticionParametro, quien: dict = Depends(ADMINISTRADOR)):
    with _motor().begin() as conn:
        version = parametros.publicar(conn, clave, peticion.valor, _usuario(quien, peticion.usuario), peticion.id_ruta,
                                      peticion.unidad, peticion.descripcion, peticion.validado)
        vigentes = parametros.vigentes(conn, peticion.id_ruta)
    return {"clave": clave, "id_ruta": peticion.id_ruta, "version": version, "valor": vigentes.get(clave)}


@router.get("/export/{recurso}", dependencies=[Depends(LECTOR)])
def exportar(recurso: Literal["predictions", "recommendations", "alerts", "executions", "data-quality"],
             formato: Literal["csv", "xlsx"] = Query("csv", alias="format"), routeId: int | None = None,
             desde: datetime | None = Query(None, alias="from"), hasta: datetime | None = Query(None, alias="to"),
             executionId: int | None = None):
    with _motor().connect() as conn:
        consultas = {
            "predictions": lambda: _todas(predicciones.consultar, conn=conn, id_ruta=routeId, desde=_a_zona(desde),
                                          hasta=_a_zona(hasta), execution_id=executionId),
            "recommendations": lambda: _todas(recomendaciones.consultar, conn=conn, id_ruta=routeId,
                                              desde=_a_zona(desde), hasta=_a_zona(hasta), execution_id=executionId),
            "alerts": lambda: _todas(alertas.consultar, conn=conn, id_ruta=routeId, desde=_a_zona(desde),
                                     hasta=_a_zona(hasta)),
            "executions": lambda: _todas(ejecuciones.listar, conn=conn),
            "data-quality": lambda: _todas(calidad.consultar, conn=conn, execution_id=executionId),
        }
        filas = consultas[recurso]()
    tabla = pd.DataFrame([{k: _celda(v) for k, v in f.items()} for f in filas])
    tipo, extension = TIPOS_EXPORTACION[formato]
    buffer = io.BytesIO()
    if formato == "csv":
        buffer.write(tabla.to_csv(index=False).encode("utf-8-sig"))
    else:
        tabla.to_excel(buffer, index=False, sheet_name=recurso[:31])
    buffer.seek(0)
    nombre = f"{recurso}-{ahora_utc():%Y%m%dT%H%M%SZ}.{extension}"
    return StreamingResponse(buffer, media_type=tipo, headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


app.include_router(router)
if CARPETA_TABLERO.exists():
    app.mount("/tablero", StaticFiles(directory=CARPETA_TABLERO, html=True), name="tablero")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
