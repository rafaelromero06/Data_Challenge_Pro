import json
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from sqlalchemy.exc import OperationalError

from frecuencias import db
from frecuencias.inferencia import artefactos, retencion
from frecuencias.persistencia import registro_modelos

T0 = datetime(2025, 12, 1, tzinfo=timezone.utc)


def _fila(id_modelo, estado, horas, horizonte=60, artefacto=None):
    return {"id": id_modelo, "horizonte_min": horizonte, "estado": estado, "artefacto": artefacto or f"paquetes/m{id_modelo}",
            "activado_en": T0 + timedelta(hours=horas) if estado in ("activo", "retirado") else None,
            "registrado_en": T0 + timedelta(hours=horas)}


@pytest.fixture(autouse=True)
def sin_activos_locales(tmp_path):
    with mock.patch.object(artefactos, "ARCHIVO_ACTIVOS", tmp_path / "no_existe.json"):
        yield


def test_planificar_conserva_activo_candidato_y_ultimos_retirados():
    filas = [_fila(1, "retirado", 1), _fila(2, "retirado", 2), _fila(3, "retirado", 3), _fila(4, "activo", 4),
             _fila(5, "rechazado", 5), _fila(6, "candidato", 6), _fila(7, "retirado", 1, horizonte=30)]
    plan = retencion.planificar(filas, retirados_a_conservar=2)
    assert [f["id"] for f in plan] == [1, 5]
    assert not any(f["protegido"] for f in plan)


def test_planificar_no_archiva_paquetes_compartidos_ni_ya_archivados():
    filas = [_fila(1, "rechazado", 1, artefacto="paquetes/comun"), _fila(2, "activo", 2, artefacto="paquetes/comun"),
             _fila(3, "rechazado", 3, artefacto="archivo/viejo")]
    plan = retencion.planificar(filas)
    assert [(f["id"], f["protegido"]) for f in plan] == [(1, True)]


def test_planificar_respeta_activos_locales(tmp_path):
    activos = tmp_path / "activos.json"
    activos.write_text(json.dumps({"60": {"artefacto": "paquetes/m1", "sha256": "x"}}), encoding="utf-8")
    with mock.patch.object(artefactos, "ARCHIVO_ACTIVOS", activos):
        assert retencion.planificar([_fila(1, "rechazado", 1)])[0]["protegido"]


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
def test_archivar_mueve_el_paquete_y_actualiza_el_registro(conn, tmp_path):
    carpeta = tmp_path / "paquetes" / "demanda-prueba-rechazado"
    carpeta.mkdir(parents=True)
    (carpeta / "meta.json").write_text("{}", encoding="utf-8")
    id_modelo = registro_modelos.registrar(conn, model_id="demanda-prueba", version="r1", horizonte_min=60,
                                           feature_version="v3", features=["a"],
                                           artefacto="paquetes/demanda-prueba-rechazado", artefacto_sha256="a" * 64)
    registro_modelos.rechazar(conn, id_modelo, "pytest")
    with mock.patch.object(retencion, "CARPETA_MODELOS", tmp_path), \
            mock.patch.object(retencion, "CARPETA_ARCHIVO", tmp_path / "archivo"):
        assert any(r["accion"] == "se archivaria" for r in retencion.archivar(conn, simular=True))
        assert carpeta.exists()
        resultado = [r for r in retencion.archivar(conn) if r["model_registry_id"] == id_modelo]
    assert resultado[0]["accion"] == "archivado"
    assert not carpeta.exists() and (tmp_path / "archivo" / "demanda-prueba-rechazado" / "meta.json").exists()
    assert registro_modelos.obtener(conn, id_modelo)["artefacto"] == "archivo/demanda-prueba-rechazado"


def test_archivar_fase1(tmp_path):
    (tmp_path / "demanda_h60.json").write_text("{}", encoding="utf-8")
    (tmp_path / "activos.json").write_text("{}", encoding="utf-8")
    with mock.patch.object(retencion, "CARPETA_MODELOS", tmp_path), \
            mock.patch.object(retencion, "CARPETA_ARCHIVO", tmp_path / "archivo"):
        assert retencion.archivar_fase1(simular=True) == ["demanda_h60.json"]
        assert retencion.archivar_fase1() == ["demanda_h60.json"]
    assert (tmp_path / "archivo" / "xgboost-fase1" / "demanda_h60.json").exists()
    assert (tmp_path / "activos.json").exists()
