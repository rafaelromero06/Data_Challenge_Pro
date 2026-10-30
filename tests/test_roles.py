import base64
import hashlib
import hmac
import uuid

import pytest
from psycopg2 import errors
from sqlalchemy.exc import OperationalError

from frecuencias import db
from frecuencias.persistencia import roles


def test_verificador_scram_sigue_el_formato_de_postgresql():
    sal = b"0123456789abcdef"
    verificador = roles.verificador_scram("clave-de-prueba-larga", sal, 4096)
    metodo, resto = verificador.split("$", 1)
    iteraciones_y_sal, claves = resto.split("$")
    iteraciones, sal_b64 = iteraciones_y_sal.split(":")
    almacenada, servidor = claves.split(":")
    salada = hashlib.pbkdf2_hmac("sha256", b"clave-de-prueba-larga", sal, 4096)
    cliente = hmac.new(salada, b"Client Key", hashlib.sha256).digest()
    assert metodo == "SCRAM-SHA-256"
    assert iteraciones == "4096"
    assert base64.b64decode(sal_b64) == sal
    assert base64.b64decode(almacenada) == hashlib.sha256(cliente).digest()
    assert base64.b64decode(servidor) == hmac.new(salada, b"Server Key", hashlib.sha256).digest()
    assert "clave-de-prueba-larga" not in verificador
    primero, segundo = (roles.verificador_scram("x" * 20) for _ in range(2))
    assert primero != segundo


@pytest.mark.parametrize("usuario, clave", [("Mayuscula", "x" * 20), ("con-guion", "x" * 20), ("", "x" * 20),
                                            ("frecuencias_app", "corta")])
def test_usuario_y_clave_invalidos(usuario, clave):
    with pytest.raises(ValueError):
        roles.validar(usuario, clave)


@pytest.fixture
def conexion():
    if db.url_destino() is None:
        pytest.skip("Base ai_frecuencias no configurada")
    try:
        motor = db.motor_destino()
        cruda = motor.raw_connection()
    except OperationalError:
        pytest.skip("Base ai_frecuencias no disponible")
    yield cruda
    cruda.rollback()
    cruda.close()


@pytest.mark.integracion
def test_usuario_de_aplicacion_se_crea_y_hereda_el_rol(conexion):
    usuario = f"prueba_{uuid.uuid4().hex[:8]}"
    assert roles.asegurar_usuario(conexion, usuario, "x" * 24) == "creado"
    assert roles.asegurar_usuario(conexion, usuario, "y" * 24) == "actualizado"
    with conexion.cursor() as cursor:
        cursor.execute("SELECT rolcanlogin, rolsuper, rolcreaterole, rolpassword LIKE 'SCRAM-SHA-256$%%' "
                       "FROM pg_authid WHERE rolname = %s", (usuario,))
        assert cursor.fetchone() == (True, False, False, True)
        cursor.execute("SELECT pg_has_role(%s, %s, 'MEMBER')", (usuario, roles.ROL_APLICACION))
        assert cursor.fetchone()[0]


@pytest.mark.integracion
@pytest.mark.parametrize("sentencia, permitida", [
    ("SELECT count(*) FROM ai_frecuencias.prediction", True),
    ("UPDATE ai_frecuencias.alert SET actualizada_en = actualizada_en WHERE false", True),
    ("SELECT version_num FROM ai_frecuencias.alembic_version", True),
    ("DELETE FROM ai_frecuencias.alert WHERE false", False),
    ("TRUNCATE ai_frecuencias.alert_history", False),
    ("CREATE TABLE ai_frecuencias.intrusa (x int)", False),
    ("CREATE TABLE public.intrusa (x int)", False),
    ("ALTER TABLE ai_frecuencias.alert ADD COLUMN x int", False),
    ("UPDATE ai_frecuencias.alembic_version SET version_num = version_num", False),
])
def test_rol_de_aplicacion_solo_lee_y_escribe_datos(conexion, sentencia, permitida):
    with conexion.cursor() as cursor:
        cursor.execute(f"SET LOCAL ROLE {roles.ROL_APLICACION}")
        if permitida:
            cursor.execute(sentencia)
        else:
            with pytest.raises(errors.InsufficientPrivilege):
                cursor.execute(sentencia)
