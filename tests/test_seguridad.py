import pytest
from sqlalchemy.exc import OperationalError

from frecuencias import db, seguridad


def test_huella_es_sha256_y_no_guarda_la_clave():
    clave = seguridad.generar()
    assert clave.startswith(seguridad.PREFIJO_CLAVE) and len(clave) > 40
    assert seguridad.huella(clave) == seguridad.huella(clave) and len(seguridad.huella(clave)) == 64
    assert seguridad.huella(clave) != seguridad.huella(seguridad.generar())


def test_jerarquia_de_roles():
    assert seguridad.autorizado("administrador", "operador") and seguridad.autorizado("operador", "lector")
    assert not seguridad.autorizado("lector", "operador") and not seguridad.autorizado("operador", "administrador")
    assert not seguridad.autorizado("desconocido", "lector")


def test_validar_sin_clave_no_consulta_la_base():
    assert seguridad.validar(None, "") is None and seguridad.validar(None, None) is None


@pytest.fixture
def conn():
    if db.url_destino() is None:
        pytest.skip("Base ai_frecuencias no configurada")
    try:
        conexion = db.motor_destino().connect()
    except OperationalError:
        pytest.skip("Base ai_frecuencias no disponible")
    transaccion = conexion.begin()
    yield conexion
    transaccion.rollback()
    conexion.close()


@pytest.mark.integracion
def test_crear_validar_y_revocar(conn):
    clave = seguridad.crear(conn, "prueba-unitaria", "operador", "pytest")
    quien = seguridad.validar(conn, clave)
    assert quien["nombre"] == "prueba-unitaria" and quien["rol"] == "operador"
    assert seguridad.validar(conn, clave + "x") is None
    assert all("huella" not in fila for fila in seguridad.listar(conn))
    assert seguridad.revocar(conn, "prueba-unitaria")
    assert seguridad.validar(conn, clave) is None
    assert not seguridad.revocar(conn, "prueba-unitaria")


@pytest.mark.integracion
def test_rol_desconocido(conn):
    with pytest.raises(ValueError, match="Rol desconocido"):
        seguridad.crear(conn, "x", "superusuario", "pytest")
