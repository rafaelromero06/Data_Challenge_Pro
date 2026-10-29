import asyncio
import uuid

import httpx
import pytest

from frecuencias import db, seguridad
from frecuencias.api import main as api

pytestmark = [pytest.mark.lenta, pytest.mark.integracion]


@pytest.fixture(scope="module")
def claves():
    sufijo = uuid.uuid4().hex[:8]
    nombres = {rol: f"prueba-{rol}-{sufijo}" for rol in seguridad.ROLES}
    with db.motor_destino().begin() as conn:
        valores = {rol: seguridad.crear(conn, nombre, rol, "pytest") for rol, nombre in nombres.items()}
    yield valores
    with db.motor_destino().begin() as conn:
        for nombre in nombres.values():
            seguridad.revocar(conn, nombre)


@pytest.fixture(scope="module")
def cliente(claves):
    bucle = asyncio.new_event_loop()
    contexto = api.lifespan(api.app)
    bucle.run_until_complete(contexto.__aenter__())
    cliente_http = httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://prueba")

    def pedir(metodo, ruta, rol="operador", **kwargs):
        cabeceras = kwargs.pop("headers", {})
        if rol:
            cabeceras = {seguridad.CABECERA: claves[rol], **cabeceras}
        return bucle.run_until_complete(cliente_http.request(metodo, ruta, headers=cabeceras, **kwargs))

    yield pedir
    bucle.run_until_complete(cliente_http.aclose())
    bucle.run_until_complete(contexto.__aexit__(None, None, None))
    bucle.close()


@pytest.fixture(scope="module")
def referencia(cliente):
    rutas = cliente("GET", "/api/v1/rutas", rol="lector").json()
    ruta = next(r for r in rutas if r["id_ruta"] == 2)
    return ruta["ultima_franja"][:10] + "T07:00:00"


def test_salud_publica_y_correlation_id(cliente):
    propio = str(uuid.uuid4())
    respuesta = cliente("GET", "/api/v1/salud", rol=None, headers={"X-Correlation-Id": propio})
    assert respuesta.status_code == 200 and respuesta.headers["X-Correlation-Id"] == propio
    assert respuesta.json()["horizontes_min"] == [30, 60, 120]
    reemplazado = cliente("GET", "/api/v1/salud", rol=None, headers={"X-Correlation-Id": "no-es-uuid"})
    valor = reemplazado.headers["X-Correlation-Id"]
    assert valor != "no-es-uuid" and str(uuid.UUID(valor)) == valor


def test_sin_clave_o_con_clave_invalida_responde_401(cliente):
    sin_clave = cliente("GET", "/api/v1/rutas", rol=None)
    assert sin_clave.status_code == 401 and sin_clave.json()["error"]["codigo"] == "no_autenticado"
    invalida = cliente("GET", "/api/v1/rutas", rol=None, headers={seguridad.CABECERA: "fq_inventada"})
    assert invalida.status_code == 401


def test_roles_insuficientes_responden_403(cliente):
    lector = cliente("POST", "/api/v1/predictions/run", rol="lector", json={"id_ruta": 2, "guardar": False})
    assert lector.status_code == 403 and lector.json()["error"]["codigo"] == "sin_permiso"
    operador = cliente("POST", "/api/v1/models/1/activate", rol="operador", json={})
    assert operador.status_code == 403
    parametro = cliente("PUT", "/api/v1/parameters/ocupacion_objetivo", rol="operador", json={"valor": 80})
    assert parametro.status_code == 403


def test_prediccion_sin_guardar(cliente, referencia):
    respuesta = cliente("POST", "/api/v1/predictions/run",
                        json={"id_ruta": 2, "referencia": referencia, "guardar": False})
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert not cuerpo["saved"] and cuerpo["executionId"] is None
    assert [p["horizonMinutes"] for p in cuerpo["predictions"]] == [15, 30, 45, 60, 75, 90, 105, 120]
    for p in cuerpo["predictions"]:
        assert p["confidenceInterval"]["lower"] <= p["predictedPassengers"] <= p["confidenceInterval"]["upper"]
        assert p["recommendation"]["busesPerHour"] >= 1 and len(p["explanation"]) == 5
    assert len(cuerpo["recommendations"]) == 2


def test_prediccion_guardada_queda_consultable_y_auditada(cliente, referencia):
    cuerpo = cliente("POST", "/api/v1/predictions/run", json={"id_ruta": 2, "referencia": referencia}).json()
    assert cuerpo["saved"] and cuerpo["executionId"]
    assert all(p["predictionId"] for p in cuerpo["predictions"])
    guardadas = cliente("GET", f"/api/v1/predictions?executionId={cuerpo['executionId']}", rol="lector").json()
    assert guardadas["total"] == 8
    recomendaciones = cliente("GET", f"/api/v1/recommendations?executionId={cuerpo['executionId']}", rol="lector").json()
    assert recomendaciones["total"] == 2
    ejecucion = cliente("GET", f"/api/v1/frequency/executions/{cuerpo['executionId']}", rol="lector").json()
    assert ejecucion["tipo"] == "prediccion" and ejecucion["estado"] == "exitosa"
    assert ejecucion["detalle"]["solicitado_por"].startswith("prueba-operador-")


def test_segmentos_perfil_y_desagregacion(cliente, referencia):
    catalogo = cliente("GET", "/api/v1/segments?routeId=2", rol="lector").json()
    assert len(catalogo) == 135 and catalogo[0]["nro_segmento"] == 1
    perfil = cliente("GET", "/api/v1/segments/profile?routeId=16&dayType=habil&hour=7", rol="lector")
    assert perfil.status_code == 200
    assert sum(float(f["cuota_subidas"]) for f in perfil.json()) == pytest.approx(1.0, abs=1e-3)
    cuerpo = cliente("POST", "/api/v1/predictions/run", json={"id_ruta": 16, "referencia": referencia}).json()
    assert cuerpo["recommendations"][0]["tramo_critico"]["segmentNumber"] >= 1
    primera = cuerpo["predictions"][0]
    desagregada = cliente("GET", f"/api/v1/predictions/{primera['predictionId']}/segments", rol="lector").json()
    assert sum(s["predictedBoardings"] for s in desagregada["segments"]) == pytest.approx(
        primera["predictedPassengers"], rel=0.01)
    assert desagregada["criticalSegment"]["loadP90"] > 0


def test_errores_uniformes(cliente):
    no_existe = cliente("POST", "/api/v1/predictions/run", json={"id_ruta": 999, "guardar": False})
    assert no_existe.status_code == 404 and no_existe.json()["error"]["codigo"] == "no_encontrado"
    invalida = cliente("POST", "/api/v1/predictions/run", json={"id_ruta": "x"})
    assert invalida.status_code == 422 and invalida.json()["error"]["correlation_id"]
    alerta = cliente("PATCH", f"/api/v1/alerts/{uuid.uuid4()}/status", json={"estado": "reconocida"})
    assert alerta.status_code == 404


def test_listados_paginados_y_resumen_de_alertas(cliente):
    for ruta in ("/api/v1/alerts", "/api/v1/frequency/executions", "/api/v1/data-quality", "/api/v1/notifications"):
        cuerpo = cliente("GET", f"{ruta}?size=5", rol="lector").json()
        assert set(cuerpo) == {"items", "page", "size", "total"} and len(cuerpo["items"]) <= 5
    resumen = cliente("GET", "/api/v1/alerts/summary", rol="lector").json()
    assert resumen["total"] == sum(resumen["por_estado"].values())
    assert set(resumen["por_codigo"]) <= {f"ALT-{n:03d}" for n in range(1, 11)}


def test_modelos_activos_en_memoria(cliente):
    activos = [m for m in cliente("GET", "/api/v1/models?status=activo", rol="lector").json() if m["en_memoria"]]
    assert sorted(m["horizonte_min"] for m in activos) == [30, 60, 120]


def test_exportacion_csv(cliente):
    respuesta = cliente("GET", "/api/v1/export/executions?format=csv", rol="lector")
    assert respuesta.status_code == 200 and respuesta.headers["content-type"].startswith("text/csv")
    assert "tipo" in respuesta.text.splitlines()[0]


def test_tablero_documentacion_y_metricas(cliente):
    assert cliente("GET", "/tablero/", rol=None).status_code == 200
    assert cliente("GET", "/docs", rol=None).status_code == 200
    respuesta = cliente("GET", "/metrics", rol=None)
    assert respuesta.status_code == 200 and "frecuencias_predicciones_total" in respuesta.text
