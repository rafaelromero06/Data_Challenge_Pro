import logging
import os
import re
import sys
import time
import uuid

import structlog
from prometheus_client import Counter, Gauge, Histogram
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

CABECERA_CORRELACION = "X-Correlation-Id"

PETICIONES = Counter("frecuencias_peticiones_total", "Peticiones HTTP atendidas", ["metodo", "ruta", "estado"])
DURACION = Histogram("frecuencias_peticion_segundos", "Duracion de las peticiones HTTP", ["metodo", "ruta"])
PREDICCIONES = Counter("frecuencias_predicciones_total", "Predicciones generadas", ["horizonte_min"])
RECOMENDACIONES = Counter("frecuencias_recomendaciones_total", "Recomendaciones generadas", ["viable"])
ALERTAS = Counter("frecuencias_alertas_total", "Alertas generadas o actualizadas", ["codigo", "severidad"])
EJECUCIONES = Counter("frecuencias_ejecuciones_total", "Ejecuciones terminadas", ["tipo", "estado"])
CIRCUITOS = Gauge("frecuencias_circuito_abierto", "1 si el circuito de la integracion externa esta abierto",
                  ["integracion"])
LATENCIA_DATOS = Gauge("frecuencias_latencia_datos_horas", "Horas entre la ultima franja recibida y la ejecucion")
NOTIFICACIONES = Counter("frecuencias_notificaciones_total", "Notificaciones por canal y estado", ["canal", "estado"])


def configurar_logs() -> None:
    nivel = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=nivel, force=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(nivel),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def log(nombre: str = "frecuencias"):
    return structlog.get_logger(nombre)


PATRONES_SENSIBLES = [
    (re.compile(r'(server at|host|user|usuario|database|base de datos) "[^"]*"', re.IGNORECASE), r'\1 "***"'),
    (re.compile(r"\b(host|hostaddr|user|password|dbname)=\S+", re.IGNORECASE), r"\1=***"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "***"),
    (re.compile(r"\[[0-9a-f:]{6,}\]", re.IGNORECASE), "[***]"),
]


def describir_error(error: Exception, largo: int = 500) -> str:
    texto = f"{type(error).__name__}: {error}"
    texto = re.split(r"\n?\[SQL:|\(Background on this error", texto)[0]
    for patron, reemplazo in PATRONES_SENSIBLES:
        texto = patron.sub(reemplazo, texto)
    return " ".join(texto.split())[:largo]


def nuevo_correlation_id() -> str:
    return str(uuid.uuid4())


def normalizar_correlation_id(valor: str | None) -> str:
    try:
        return str(uuid.UUID(valor))
    except (TypeError, ValueError, AttributeError):
        return nuevo_correlation_id()


class MiddlewareCorrelacion(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        correlation_id = normalizar_correlation_id(request.headers.get(CABECERA_CORRELACION))
        request.state.correlation_id = correlation_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
        inicio = time.perf_counter()
        respuesta = await call_next(request)
        ruta = request.scope.get("route")
        plantilla = getattr(ruta, "path", request.url.path)
        duracion = time.perf_counter() - inicio
        PETICIONES.labels(request.method, plantilla, str(respuesta.status_code)).inc()
        DURACION.labels(request.method, plantilla).observe(duracion)
        respuesta.headers[CABECERA_CORRELACION] = correlation_id
        log("api").info("peticion", metodo=request.method, ruta=plantilla, estado=respuesta.status_code,
                        duracion_ms=round(duracion * 1000, 1))
        return respuesta
