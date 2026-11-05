import asyncio
import json
import os
import uuid
from pathlib import Path
from unittest import mock

import httpx
import pandas as pd
import pytest
from sqlalchemy.exc import OperationalError

from frecuencias import db, seguridad
from frecuencias.api import esquemas as esq
from frecuencias.api import main as api
from frecuencias.inferencia import artefactos, modelos
from frecuencias.inferencia.modelos import SinModelos

OPENAPI = json.loads((Path(__file__).resolve().parents[1] / "docs" / "openapi.json").read_text(encoding="utf-8"))
ENDPOINTS_MINIMOS = [
    ("post", "/api/v1/frequency/executions"), ("get", "/api/v1/frequency/executions/{id_ejecucion}"),
    ("post", "/api/v1/predictions/run"), ("get", "/api/v1/predictions"),
    ("post", "/api/v1/recommendations/calculate"), ("get", "/api/v1/recommendations"),
    ("get", "/api/v1/alerts"), ("patch", "/api/v1/alerts/{id_alerta}/status"),
    ("get", "/api/v1/models"), ("post", "/api/v1/models/{id_modelo}/activate"),
]
RUTAS_PUBLICAS = {"/api/v1/salud"}
RUTAS_PROTEGIDAS = [(metodo, ruta) for ruta, operaciones in OPENAPI["paths"].items() if ruta not in RUTAS_PUBLICAS
                    for metodo in operaciones]
COLECCIONES = ["/api/v1/predictions", "/api/v1/recommendations", "/api/v1/alerts", "/api/v1/alerts/groups",
               "/api/v1/frequency/executions", "/api/v1/data-quality", "/api/v1/notifications",
               "/api/v1/monitoring/history"]
VALORES_RUTA = {"id_alerta": str(uuid.uuid4()), "prediction_id": str(uuid.uuid4()), "id_ejecucion": "1",
                "id_modelo": "1", "clave": "ocupacion_objetivo", "recurso": "alerts"}
SIN_AUTENTICACION = {"API_AUTENTICACION": "desactivada"}
CON_AUTENTICACION = {"API_AUTENTICACION": "activa"}


def _pedir(metodo: str, ruta: str, **kwargs) -> httpx.Response:
    async def enviar():
        transporte = httpx.ASGITransport(app=api.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transporte, base_url="http://prueba") as cliente:
            return await cliente.request(metodo, ruta, **kwargs)
    return asyncio.run(enviar())


def _arrancar() -> dict:
    async def arrancar():
        async with api.lifespan(api.app):
            return dict(api.estado)
    return asyncio.run(arrancar())


@pytest.fixture
def sin_preparar():
    with mock.patch.dict(api.estado, {"sin_preparar": "No hay modelos activos."}, clear=True):
        yield


@pytest.fixture(scope="module")
def lector():
    if db.url_destino() is None:
        pytest.skip("Base ai_frecuencias no configurada")
    nombre = f"contrato-{uuid.uuid4().hex[:8]}"
    try:
        with db.motor_destino().begin() as conn:
            clave = seguridad.crear(conn, nombre, "lector", "pytest")
    except OperationalError:
        pytest.skip("Base ai_frecuencias no disponible")
    with mock.patch.dict(api.estado, {"motor_ia": db.motor_destino()}), mock.patch.dict(os.environ, CON_AUTENTICACION):
        yield {seguridad.CABECERA: clave}
    with db.motor_destino().begin() as conn:
        seguridad.revocar(conn, nombre)


def test_el_contrato_publicado_es_el_de_la_api():
    assert json.loads(json.dumps(api.app.openapi())) == OPENAPI


@pytest.mark.parametrize("metodo, ruta", ENDPOINTS_MINIMOS)
def test_endpoints_minimos_de_la_especificacion(metodo, ruta):
    assert metodo in OPENAPI["paths"][ruta]


@pytest.mark.parametrize("metodo, ruta", RUTAS_PROTEGIDAS)
def test_toda_ruta_de_negocio_exige_clave_con_error_uniforme(metodo, ruta):
    with mock.patch.dict(os.environ, CON_AUTENTICACION):
        respuesta = _pedir(metodo.upper(), ruta.format(**VALORES_RUTA))
    assert respuesta.status_code == 401
    assert set(respuesta.json()["error"]) == {"codigo", "mensaje", "detalle", "correlation_id"}
    assert respuesta.json()["error"]["codigo"] == "no_autenticado"


def test_errores_de_validacion_con_formato_uniforme():
    with mock.patch.dict(os.environ, SIN_AUTENTICACION):
        respuesta = _pedir("POST", "/api/v1/predictions/run", json={"id_ruta": "x"})
    assert respuesta.status_code == 422
    assert respuesta.json()["error"]["codigo"] == "validacion"
    assert respuesta.json()["error"]["correlation_id"] == respuesta.headers["X-Correlation-Id"]


def test_salud_sin_preparar_responde_200_y_explica_que_falta(sin_preparar):
    respuesta = _pedir("GET", "/api/v1/salud")
    cuerpo = respuesta.json()
    assert respuesta.status_code == 200
    assert cuerpo["estado"] == "sin_preparar"
    assert cuerpo["motivo"] == "No hay modelos activos."
    assert "paquete_demo" in cuerpo["instrucciones"]
    assert cuerpo["horizontes_min"] == []
    assert cuerpo["base_ai_frecuencias"] == {"estado": "no_configurada"}


def test_sin_preparar_las_rutas_que_usan_modelos_responden_503_uniforme(sin_preparar):
    with mock.patch.dict(os.environ, SIN_AUTENTICACION):
        respuesta = _pedir("GET", "/api/v1/rutas")
    assert respuesta.status_code == 503
    assert respuesta.json()["error"]["codigo"] == "servicio_no_disponible"
    assert "No hay modelos activos." in respuesta.json()["error"]["mensaje"]


@pytest.mark.parametrize("error", [SinModelos("sin modelos"), api.SinHistorico("sin historico")])
def test_la_api_arranca_aunque_falten_datos_o_modelos(error):
    with mock.patch.object(api, "_cargar_estado", side_effect=error), \
            mock.patch.object(api.db, "url_destino", return_value=None), mock.patch.dict(api.estado, clear=True):
        cargado = _arrancar()
    assert cargado["sin_preparar"] == str(error)


def test_un_artefacto_alterado_si_detiene_el_arranque():
    with mock.patch.object(api, "_cargar_estado", side_effect=artefactos.ArtefactoAlterado("alterado")), \
            mock.patch.object(api.db, "url_destino", return_value=None), mock.patch.dict(api.estado, clear=True), \
            pytest.raises(artefactos.ArtefactoAlterado):
        _arrancar()


def test_sin_historico_no_se_presentan_datos_sinteticos_como_reales(tmp_path):
    with mock.patch.object(api, "ARCHIVO_DEMANDA", tmp_path / "no_existe.parquet"), pytest.raises(api.SinHistorico):
        api._leer_historia(None)


def test_un_artefacto_faltante_cuenta_como_sin_modelos():
    falta = FileNotFoundError(2, "No such file or directory", "manifiesto.json")
    with mock.patch.object(modelos, "desde_archivo", side_effect=falta), pytest.raises(SinModelos, match="manifiesto"):
        modelos.cargar_modelos()


def test_las_fechas_se_publican_con_zona():
    local = pd.Timestamp("2025-12-18 23:45")
    ruta = esq.Ruta(id_ruta=2, nombre="SILENCIO", primera_franja=local, ultima_franja=local, franjas_con_servicio=1)
    assert api._limpio({"referencia": local}) == {"referencia": "2025-12-18T23:45:00-05:00"}
    assert ruta.model_dump(mode="json")["ultima_franja"] == "2025-12-18T23:45:00-05:00"


@pytest.mark.integracion
@pytest.mark.parametrize("ruta", COLECCIONES)
def test_colecciones_paginadas_cumplen_el_esquema(lector, ruta):
    respuesta = _pedir("GET", f"{ruta}?size=2", headers=lector)
    assert respuesta.status_code == 200
    pagina = esq.Pagina.model_validate(respuesta.json())
    assert pagina.size == 2
    assert len(pagina.items) <= 2


@pytest.mark.integracion
def test_rol_lector_no_puede_lanzar_ciclos(lector):
    respuesta = _pedir("POST", "/api/v1/frequency/executions", headers=lector, json={"origen": "archivos"})
    assert respuesta.status_code == 403
    assert respuesta.json()["error"]["codigo"] == "sin_permiso"
