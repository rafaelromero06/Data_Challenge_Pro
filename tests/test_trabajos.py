import uuid
from unittest import mock

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError, OperationalError

from frecuencias import db
from frecuencias.operacion import trabajos
from frecuencias.persistencia import ejecuciones, monitoreo
from frecuencias.persistencia.tablas import ejecucion


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


def _envejecer(conn, id_ejecucion: int, horas: int = 1) -> None:
    conn.execute(update(ejecucion).where(ejecucion.c.id == id_ejecucion)
                 .values(latido_en=text(f"clock_timestamp() - interval '{horas} hours'")))


@pytest.mark.integracion
def test_avance_y_latido_quedan_registrados(conn):
    propietario = f"cli@prueba#{uuid.uuid4().hex[:6]}"
    e = ejecuciones.iniciar(conn, "ingesta", {"prueba": True}, propietario=propietario)
    ejecuciones.avanzar(conn, e["id"], "ingesta", 140)
    fila = ejecuciones.obtener(conn, e["id"])
    assert (fila["etapa"], float(fila["avance"]), fila["intento"]) == ("ingesta", 100.0, 1)
    assert ejecuciones.latir(conn, propietario) == 1
    ejecuciones.finalizar(conn, e["id"], "exitosa")
    assert ejecuciones.latir(conn, propietario) == 0


@pytest.mark.integracion
def test_recuperacion_por_latido_vencido_y_por_instancia_anterior(conn):
    sufijo = uuid.uuid4().hex[:6]
    vencida = ejecuciones.iniciar(conn, "ingesta", None, propietario=f"cli@otro#{sufijo}")
    _envejecer(conn, vencida["id"])
    anterior = ejecuciones.iniciar(conn, "prediccion", None, propietario=f"api@h-{sufijo}#viejo")
    actual = ejecuciones.iniciar(conn, "prediccion", None, propietario=f"api@h-{sufijo}#nuevo")
    viva = ejecuciones.iniciar(conn, "ingesta", None, propietario=f"cli@otro#{sufijo}b")
    recuperadas = {r["id"] for r in ejecuciones.recuperar(conn, 180, f"api@h-{sufijo}#", f"api@h-{sufijo}#nuevo")}
    assert {vencida["id"], anterior["id"]} <= recuperadas
    assert actual["id"] not in recuperadas and viva["id"] not in recuperadas
    fila = ejecuciones.obtener(conn, vencida["id"])
    assert fila["estado"] == "interrumpida" and fila["finalizada_en"] is not None


@pytest.mark.integracion
def test_solo_un_ciclo_en_curso_a_la_vez(conn):
    conn.execute(update(ejecucion).where(ejecucion.c.tipo == "ciclo", ejecucion.c.estado == "en_curso")
                 .values(estado="fallida", finalizada_en=text("clock_timestamp()")))
    primero = ejecuciones.iniciar(conn, "ciclo", {"prueba": True})
    reintento = conn.begin_nested()
    with pytest.raises(IntegrityError, match="ux_ejecucion_ciclo_en_curso"):
        ejecuciones.iniciar(conn, "ciclo", {"prueba": True})
    reintento.rollback()
    ejecuciones.finalizar(conn, primero["id"], "fallida", error="prueba")
    segundo = ejecuciones.iniciar(conn, "ciclo", {"prueba": True}, reintento_de=primero["id"], intento=2)
    assert ejecuciones.obtener(conn, segundo["id"])["reintento_de"] == primero["id"]


@pytest.mark.integracion
def test_ultimo_reentrenamiento_y_metricas_de_monitoreo(conn):
    e = ejecuciones.iniciar(conn, "alertas", None)
    medidas = [{"metrica": "deriva_psi", "alcance": "ruta:prueba", "id_ruta": 2, "valor": 0.31, "umbral": 0.25,
                "estado": "alerta", "muestras": 400, "ventana_desde": "2025-12-06", "ventana_hasta": "2025-12-13",
                "detalle": {"media_reciente": 30.1}, "clave_ignorada": 1},
               {"metrica": "volumen_predicciones", "alcance": "global-prueba", "valor": None, "referencia": None,
                "estado": "sin_datos", "ventana_desde": "2025-12-12", "ventana_hasta": "2025-12-13"}]
    assert monitoreo.guardar(conn, medidas, e["id"]) == 2
    assert monitoreo.guardar(conn, [], e["id"]) == 0
    ultimo = next(m for m in monitoreo.ultimos(conn) if m["alcance"] == "ruta:prueba")
    assert float(ultimo["valor"]) == 0.31 and ultimo["execution_id"] == e["id"]
    filas, total = monitoreo.historial(conn, "deriva_psi", "ruta:prueba")
    assert total == 1 and filas[0]["estado"] == "alerta"
    conn.execute(update(ejecucion).where(ejecucion.c.tipo == "ciclo", ejecucion.c.estado == "en_curso")
                 .values(estado="fallida", finalizada_en=text("clock_timestamp()")))
    ciclo = ejecuciones.iniciar(conn, "ciclo", None)
    ejecuciones.finalizar(conn, ciclo["id"], "exitosa", detalle={"reentrenamiento": {}})
    inicio = ejecuciones.obtener(conn, ciclo["id"])["iniciada_en"]
    assert ejecuciones.ultimo_reentrenamiento(conn) >= inicio


def test_instancia_identifica_servicio_y_host():
    with mock.patch.dict(trabajos._proceso, {"servicio": "api", "instancia": None}):
        primera = trabajos.instancia()
        assert primera.startswith(trabajos.prefijo("api"))
        assert trabajos.instancia() == primera


def test_latido_periodico_y_detencion():
    motor = mock.MagicMock()
    with mock.patch.object(trabajos, "latir") as latir:
        hilo = trabajos.Latido(motor, "cli@x#1", 0.01)
        hilo.start()
        hilo.parar.wait(0.1)
        hilo.detener()
    assert latir.call_count >= 2
    assert not hilo.is_alive()


def test_latiendo_reutiliza_el_latido_del_proceso_o_crea_uno():
    motor = mock.MagicMock()
    vivo = mock.MagicMock()
    vivo.is_alive.return_value = True
    with mock.patch.dict(trabajos._proceso, {"latido": vivo}), mock.patch.object(trabajos, "Latido") as clase:
        with trabajos.latiendo(motor):
            pass
        clase.assert_not_called()
    with mock.patch.dict(trabajos._proceso, {"latido": None}), mock.patch.object(trabajos, "Latido") as clase:
        with trabajos.latiendo(motor):
            pass
        clase.return_value.start.assert_called_once()
        clase.return_value.detener.assert_called_once()


def test_fallas_de_base_en_latido_y_avance_no_rompen_el_trabajo():
    motor = mock.MagicMock()
    motor.begin.side_effect = OperationalError("update", {}, Exception("caida"))
    assert trabajos.latir(motor, "cli@x#1") == 0
    trabajos.avanzar(motor, 1, "ingesta", 10)
