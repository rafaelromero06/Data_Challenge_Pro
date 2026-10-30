import os
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor

NOMBRE = "frecuencias"
RUTAS_EXCLUIDAS = "metrics,api/v1/salud"
_estado: dict = {"proveedor": None, "servicio": None}


def exportador_configurado() -> str:
    return os.environ.get("OTEL_TRACES_EXPORTER", "none").strip().lower()


def activo() -> bool:
    return _estado["proveedor"] is not None


def configurar(servicio: str, exportador=None) -> TracerProvider | None:
    tipo = exportador_configurado()
    if exportador is None and tipo in ("", "none"):
        return None
    if _estado["proveedor"] is not None and exportador is None:
        return _estado["proveedor"]
    recurso = Resource.create({
        "service.name": os.environ.get("OTEL_SERVICE_NAME", f"frecuencias-{servicio}"),
        "service.namespace": "sobusa",
        "deployment.environment": os.environ.get("ENTORNO", "desarrollo"),
    })
    proveedor = TracerProvider(resource=recurso)
    if exportador is not None:
        proveedor.add_span_processor(SimpleSpanProcessor(exportador))
    elif tipo == "console":
        proveedor.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        proveedor.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    _instrumentar_sqlalchemy(proveedor)
    _estado.update(proveedor=proveedor, servicio=servicio)
    return proveedor


def _instrumentar_sqlalchemy(proveedor: TracerProvider) -> None:
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    instrumentador = SQLAlchemyInstrumentor()
    if instrumentador.is_instrumented_by_opentelemetry:
        instrumentador.uninstrument()
    instrumentador.instrument(tracer_provider=proveedor, enable_commenter=False, skip_dep_check=True)


def apagar() -> None:
    proveedor = _estado["proveedor"]
    if proveedor is not None:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        instrumentador = SQLAlchemyInstrumentor()
        if instrumentador.is_instrumented_by_opentelemetry:
            instrumentador.uninstrument()
        proveedor.shutdown()
    _estado.update(proveedor=None, servicio=None)


def tracer() -> trace.Tracer:
    proveedor = _estado["proveedor"]
    return proveedor.get_tracer(NOMBRE) if proveedor is not None else trace.NoOpTracer()


@contextmanager
def tramo(nombre: str, **atributos):
    limpios = {k: v if isinstance(v, (bool, int, float, str)) else str(v) for k, v in atributos.items()
               if v is not None}
    with tracer().start_as_current_span(nombre, attributes=limpios) as span:
        yield span


def anotar(**atributos) -> None:
    span = trace.get_current_span()
    for k, v in atributos.items():
        if v is not None:
            span.set_attribute(k, v if isinstance(v, (bool, int, float, str)) else str(v))


def ids_actuales() -> dict:
    contexto = trace.get_current_span().get_span_context()
    if not contexto.is_valid:
        return {}
    return {"trace_id": format(contexto.trace_id, "032x"), "span_id": format(contexto.span_id, "016x")}


def instrumentar_app(app) -> bool:
    proveedor = _estado["proveedor"]
    if proveedor is None:
        return False
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, tracer_provider=proveedor, excluded_urls=RUTAS_EXCLUIDAS)
    return True
