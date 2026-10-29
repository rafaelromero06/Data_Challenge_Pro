from unittest import mock

import pytest

from frecuencias import db
from frecuencias.analitica import carga


def test_motor_fuente_exige_contrasena():
    with mock.patch.dict("os.environ", {"SOBUSA_DB_PASSWORD": "", "PGPASSWORD": ""}):
        with pytest.raises(RuntimeError, match="contrasena"):
            db.motor_fuente()


def test_motor_fuente_es_de_solo_lectura():
    entorno = {"PGPASSWORD": "x", "SOBUSA_DB_USER": "u", "SOBUSA_DB_HOST": "h", "SOBUSA_DB_PORT": "5432"}
    with mock.patch.dict("os.environ", entorno), mock.patch.object(db, "create_engine") as crear:
        db.motor_fuente()
    opciones = crear.call_args.kwargs["connect_args"]["options"]
    assert "default_transaction_read_only=on" in opciones and "statement_timeout" in opciones


def test_url_destino_sin_configurar():
    with mock.patch.dict("os.environ", {"AI_DB_HOST": ""}):
        assert db.url_destino() is None
        with pytest.raises(RuntimeError, match="ai_frecuencias"):
            db.motor_destino()


def test_url_destino_configurada():
    entorno = {"AI_DB_HOST": "db", "AI_DB_PORT": "5432", "AI_DB_NAME": "f", "AI_DB_USER": "u", "AI_DB_PASSWORD": "p"}
    with mock.patch.dict("os.environ", entorno):
        url = db.url_destino()
    assert (url.host, url.port, url.database, url.username) == ("db", 5432, "f", "u")


def test_calendario_vacio_sin_archivos_ni_base(tmp_path):
    with mock.patch.object(carga, "CARPETA_DATOS", tmp_path), mock.patch.dict("os.environ", {"SOBUSA_DB_HOST": ""}):
        estac, novedades = carga.cargar_calendario()
    assert estac.empty and novedades.empty
    assert {"fecha_inicio", "fecha_fin"} <= set(estac.columns)


def test_calendario_desde_la_base_si_no_hay_archivos(tmp_path):
    with mock.patch.object(carga, "CARPETA_DATOS", tmp_path), \
            mock.patch.dict("os.environ", {"SOBUSA_DB_HOST": "h"}), \
            mock.patch.object(carga, "motor_fuente") as motor, \
            mock.patch.object(carga.pd, "read_sql", return_value="leido") as leer:
        resultado = carga.cargar_calendario()
    assert resultado == ("leido", "leido") and leer.call_count == 2
    motor.assert_called_once()
