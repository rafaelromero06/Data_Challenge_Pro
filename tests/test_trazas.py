import asyncio
import json
from unittest import mock

import httpx
import pytest
import sqlalchemy
import structlog
from fastapi import FastAPI
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from frecuencias import observabilidad, trazas


@pytest.fixture
def exportador():
    memoria = InMemorySpanExporter()
    trazas.configurar("pruebas", exportador=memoria)
    yield memoria
    trazas.apagar()


def test_sin_exportador_las_trazas_no_hacen_nada():
    with mock.patch.dict("os.environ", {"OTEL_TRACES_EXPORTER": "none"}):
        assert trazas.configurar("pruebas") is None
    assert not trazas.activo()
    with trazas.tramo("nada", valor=1) as span:
        assert not span.is_recording()
    assert trazas.ids_actuales() == {}
    assert not trazas.instrumentar_app(FastAPI())


def test_tramos_anidados_con_atributos_y_errores(exportador):
    with trazas.tramo("ciclo", execution_id=7, ruta=None, ventana=["a", "b"]):
        assert len(trazas.ids_actuales()["trace_id"]) == 32
        with pytest.raises(ValueError), trazas.tramo("ciclo.ingesta"):
            trazas.anotar(origen="base", vacio=None)
            raise ValueError("fuente")
    tramos = {s.name: s for s in exportador.get_finished_spans()}
    assert tramos["ciclo.ingesta"].parent.span_id == tramos["ciclo"].context.span_id
    assert tramos["ciclo"].attributes["execution_id"] == 7
    assert "ruta" not in tramos["ciclo"].attributes
    assert tramos["ciclo"].attributes["ventana"] == "['a', 'b']"
    assert tramos["ciclo.ingesta"].attributes["origen"] == "base"
    assert tramos["ciclo.ingesta"].status.status_code.name == "ERROR"
    assert tramos["ciclo"].resource.attributes["service.name"] == "frecuencias-pruebas"


def test_consultas_sql_quedan_trazadas(exportador):
    motor = sqlalchemy.create_engine("sqlite://")
    with trazas.tramo("consulta"), motor.connect() as conn:
        conn.execute(sqlalchemy.text("select 1"))
    nombres = [s.name for s in exportador.get_finished_spans()]
    assert "consulta" in nombres
    assert any(n.lower().startswith("select") for n in nombres)


def test_peticiones_http_y_logs_llevan_la_traza(exportador, capsys):
    app = FastAPI()
    app.add_middleware(observabilidad.MiddlewareCorrelacion)

    @app.get("/eco")
    def eco():
        observabilidad.log("pruebas").info("dentro")
        return {"ok": True}

    assert trazas.instrumentar_app(app)
    observabilidad.configurar_logs()

    async def pedir():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://prueba") as cliente:
            return await cliente.get("/eco", headers={observabilidad.CABECERA_CORRELACION: "no-uuid"})

    respuesta = asyncio.run(pedir())
    assert respuesta.status_code == 200
    servidor = next(s for s in exportador.get_finished_spans() if s.name == "GET /eco")
    assert servidor.attributes["correlation_id"] == respuesta.headers[observabilidad.CABECERA_CORRELACION]
    lineas = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith("{")]
    dentro = next(l for l in lineas if l["event"] == "dentro")
    assert dentro["trace_id"] == format(servidor.context.trace_id, "032x")
    structlog.reset_defaults()


def test_exportador_por_variable_de_entorno():
    with mock.patch.dict("os.environ", {"OTEL_TRACES_EXPORTER": "console", "OTEL_SERVICE_NAME": "frecuencias-x"}):
        proveedor = trazas.configurar("api")
        assert proveedor is not None and trazas.configurar("api") is proveedor
    trazas.apagar()
    with mock.patch.dict("os.environ", {"OTEL_TRACES_EXPORTER": "otlp",
                                        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9"}):
        assert trazas.configurar("api") is not None
    trazas.apagar()
    assert not trazas.activo()
