import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError

from frecuencias import db
from frecuencias.config import ESQUEMA_IA
from frecuencias.persistencia.tablas import metadata

pytestmark = pytest.mark.integracion


@pytest.fixture(scope="module")
def inspector():
    if db.url_destino() is None:
        pytest.skip("Base ai_frecuencias no configurada")
    try:
        motor = db.motor_destino()
        with motor.connect():
            pass
    except OperationalError:
        pytest.skip("Base ai_frecuencias no disponible")
    return inspect(motor)


@pytest.mark.parametrize("tabla", sorted(metadata.tables.values(), key=lambda t: t.name), ids=lambda t: t.name)
def test_definicion_coincide_con_la_base(inspector, tabla):
    reales = {c["name"]: c for c in inspector.get_columns(tabla.name, schema=ESQUEMA_IA)}
    assert set(reales) == {c.name for c in tabla.columns}
    for columna in tabla.columns:
        if not columna.primary_key:
            assert reales[columna.name]["nullable"] == columna.nullable, columna.name
