from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import OperationalError

from frecuencias import db
from frecuencias.persistencia import alertas, ejecuciones, parametros, predicciones, recomendaciones, registro_modelos

pytestmark = pytest.mark.integracion
REFERENCIA = datetime(2025, 12, 15, 22, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    if db.url_destino() is None:
        pytest.skip("Base ai_frecuencias no configurada")
    try:
        conexion = db.motor_destino().connect()
    except OperationalError:
        pytest.skip("Base ai_frecuencias no disponible: levante docker compose up -d db")
    transaccion = conexion.begin()
    yield conexion
    transaccion.rollback()
    conexion.close()


def _modelo(conn, version="v1", horizonte=60):
    return registro_modelos.registrar(conn, model_id=f"demanda-prueba-h{horizonte}", version=version,
                                      horizonte_min=horizonte, feature_version="v3", features=["a"],
                                      artefacto="x", artefacto_sha256="a" * 64)


def _prediccion(id_modelo, **cambios):
    fila = {"model_registry_id": id_modelo, "id_ruta": 16, "referencia": REFERENCIA,
            "franja_objetivo": REFERENCIA + timedelta(minutes=60), "horizonte_min": 60, "pasajeros_predichos": 400,
            "limite_inferior": 350, "limite_superior": 460, "nivel_confianza": 0.8, "feature_version": "v3"}
    return {**fila, **cambios}


def test_finalizar_conserva_el_detalle_inicial(conn):
    e = ejecuciones.iniciar(conn, "ciclo", {"origen": "archivos", "solicitado_por": "ana"})
    ejecuciones.finalizar(conn, e["id"], "exitosa", detalle={"predicciones": 8, "origen": "archivos"})
    assert ejecuciones.obtener(conn, e["id"])["detalle"] == {"origen": "archivos", "solicitado_por": "ana",
                                                              "predicciones": 8}


def test_ejecucion_y_marca_de_agua(conn):
    e = ejecuciones.iniciar(conn, "ingesta", {"origen": "prueba"})
    assert e["correlation_id"] is not None
    ejecuciones.finalizar(conn, e["id"], "exitosa", registros_leidos=10, marca_agua_hasta=REFERENCIA)
    assert ejecuciones.ultima_marca_agua(conn, "ingesta") >= REFERENCIA
    assert ejecuciones.obtener(conn, e["id"])["estado"] == "exitosa"


def test_activar_modelo_retira_el_anterior(conn):
    m1, m2 = _modelo(conn, "v1"), _modelo(conn, "v2")
    registro_modelos.activar(conn, m1, "prueba")
    registro_modelos.activar(conn, m2, "prueba")
    assert registro_modelos.obtener(conn, m1)["estado"] == "retirado"
    assert registro_modelos.activo(conn, 60)["id"] == m2
    with pytest.raises(ValueError):
        registro_modelos.rechazar(conn, m2, "prueba")


def test_prediccion_repetida_no_se_duplica(conn):
    id_modelo = _modelo(conn)
    primera = predicciones.guardar(conn, [_prediccion(id_modelo)])
    segunda = predicciones.guardar(conn, [_prediccion(id_modelo, pasajeros_predichos=410)])
    filas, total = predicciones.consultar(conn, id_ruta=16, model_registry_id=id_modelo)
    assert primera == segunda and total == 1 and float(filas[0]["pasajeros_predichos"]) == 410


def test_recomendacion_ligada_a_prediccion(conn):
    id_prediccion = predicciones.guardar(conn, [_prediccion(_modelo(conn))])[0]
    ids = recomendaciones.guardar(conn, [{"prediction_id": id_prediccion, "id_ruta": 16, "franja_inicio": REFERENCIA,
                                          "franja_fin": REFERENCIA + timedelta(hours=1), "buses_por_hora": 8,
                                          "headway_min": 7.5, "vehiculos_requeridos": 12, "viable": True}])
    filas, total = recomendaciones.consultar(conn, id_ruta=16, desde=REFERENCIA)
    assert len(ids) == 1 and total >= 1


def test_alertas_deduplicacion_estados_e_historial(conn):
    datos = {"codigo": "ALT-002", "severidad": "critica", "clave_deduplicacion": "prueba-ALT-002",
             "id_ruta": 16, "explicacion": "Ocupacion esperada 95 %", "valor_observado": 95, "umbral": 90}
    a1, nueva1 = alertas.registrar(conn, datos)
    a2, nueva2 = alertas.registrar(conn, {**datos, "valor_observado": 97})
    assert a1 == a2 and nueva1 and not nueva2
    assert float(alertas.obtener(conn, a1)["valor_observado"]) == 97

    alertas.cambiar_estado(conn, a1, "reconocida", "operador", "revisando")
    alertas.cambiar_estado(conn, a1, "asignada", "operador", usuario_asignado="jefe")
    with pytest.raises(ValueError):
        alertas.cambiar_estado(conn, a1, "abierta", "operador")
    alertas.cambiar_estado(conn, a1, None, "jefe", comentario="en camino")
    alertas.cambiar_estado(conn, a1, "cerrada", "jefe", "resuelta")
    estados = [h["estado_nuevo"] for h in alertas.historial(conn, a1)]
    assert estados == ["abierta", "reconocida", "asignada", "asignada", "cerrada"]

    a3, nueva3 = alertas.registrar(conn, datos)
    assert nueva3 and a3 != a1
    assert alertas.consultar(conn, estado="abierta", codigo="ALT-002", id_ruta=16)[1] >= 1


def test_umbral_versionado_y_por_ruta(conn):
    general = parametros.vigentes(conn)["ocupacion_critica"]
    version = parametros.publicar(conn, "ocupacion_critica", general + 2, "prueba")
    assert version >= 2 and parametros.vigentes(conn)["ocupacion_critica"] == general + 2
    parametros.publicar(conn, "ocupacion_critica", general - 2, "prueba", id_ruta=21)
    assert parametros.vigentes(conn, 21)["ocupacion_critica"] == general - 2
    assert parametros.vigentes(conn, 16)["ocupacion_critica"] == general + 2
    with pytest.raises(ValueError):
        parametros.publicar(conn, "clave_inexistente", 1, "prueba")
