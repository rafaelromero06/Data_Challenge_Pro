from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from frecuencias.observabilidad import log

CODIGOS_HTTP = {400: "solicitud_invalida", 401: "no_autenticado", 403: "sin_permiso", 404: "no_encontrado",
                409: "conflicto", 422: "validacion", 503: "servicio_no_disponible"}


def respuesta_error(request: Request, estado: int, codigo: str, mensaje: str, detalle=None) -> JSONResponse:
    cuerpo = {"error": {"codigo": codigo, "mensaje": mensaje, "detalle": detalle,
                        "correlation_id": getattr(request.state, "correlation_id", None)}}
    return JSONResponse(status_code=estado, content=jsonable_encoder(cuerpo))


def registrar_manejadores(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http(request: Request, exc: HTTPException):
        return respuesta_error(request, exc.status_code, CODIGOS_HTTP.get(exc.status_code, "error_http"),
                               str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validacion(request: Request, exc: RequestValidationError):
        return respuesta_error(request, 422, "validacion", "La solicitud no cumple el contrato.", exc.errors())

    @app.exception_handler(LookupError)
    async def _no_encontrado(request: Request, exc: LookupError):
        return respuesta_error(request, 404, "no_encontrado", str(exc))

    @app.exception_handler(ValueError)
    async def _valor(request: Request, exc: ValueError):
        return respuesta_error(request, 400, "solicitud_invalida", str(exc))

    @app.exception_handler(SQLAlchemyError)
    async def _base(request: Request, exc: SQLAlchemyError):
        log("api").error("error_base_datos", error=type(exc).__name__)
        return respuesta_error(request, 503, "base_no_disponible", "La base ai_frecuencias no respondio.")

    @app.exception_handler(Exception)
    async def _inesperado(request: Request, exc: Exception):
        log("api").exception("error_inesperado")
        return respuesta_error(request, 500, "error_interno", "Error inesperado; revise los logs con el correlation_id.")
