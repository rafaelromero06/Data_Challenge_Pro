from unittest import mock

import pandas as pd
import pytest

from frecuencias import db
from frecuencias.ingesta import historico
from frecuencias.persistencia import ejecuciones

DESDE = pd.Timestamp("2025-12-02")
HASTA = pd.Timestamp("2025-12-03")


@pytest.fixture
def archivos(tmp_path):
    demanda = pd.DataFrame({"ts": pd.to_datetime(["2025-12-01 08:00", "2025-12-02 08:00", "2025-12-03 08:00"]),
                            "subidas": [1, 2, 3]})
    viajes = pd.DataFrame({"fechasalida": pd.to_datetime(["2025-12-01 09:00", "2025-12-02 09:00"]),
                           "idprogramacion": [1, 2]})
    demanda.to_parquet(tmp_path / "demanda.parquet")
    viajes.to_parquet(tmp_path / "viajes.parquet")
    with mock.patch.object(historico, "ARCHIVO_DEMANDA", tmp_path / "demanda.parquet"), \
            mock.patch.object(historico, "CARPETA_DATOS", tmp_path):
        yield


def test_leer_archivos_respeta_el_rango(archivos):
    demanda, viajes = historico.leer_archivos(DESDE, HASTA)
    assert demanda["subidas"].tolist() == [2]
    assert viajes["idprogramacion"].tolist() == [2]


def test_leer_archivos_sin_limites_devuelve_todo(archivos):
    demanda, viajes = historico.leer_archivos(None, None)
    assert len(demanda) == 3
    assert len(viajes) == 2


def test_recargar_rango_invertido_se_rechaza():
    with pytest.raises(ValueError, match="posterior"):
        historico.recargar_rango(mock.MagicMock(), HASTA, DESDE)


def test_recargar_rango_desde_archivos_guarda_solo_ese_rango(archivos):
    with mock.patch.object(historico, "_guardar_carga", return_value={"execution_id": 5}) as guardar:
        assert historico.recargar_rango("motor", DESDE, HASTA) == {"execution_id": 5}
    motor, demanda, viajes, detalle = guardar.call_args.args
    assert motor == "motor"
    assert demanda["subidas"].tolist() == [2]
    assert viajes["idprogramacion"].tolist() == [2]
    assert detalle["modo"] == "recarga_rango"
    assert detalle["origen"] == "archivos"


def test_recargar_rango_desde_la_base_usa_la_fuente():
    franjas, viajes = pd.DataFrame({"ts": [DESDE]}), pd.DataFrame()
    with mock.patch.object(historico, "extraer_rango", return_value=(franjas, viajes)) as extraer, \
            mock.patch.object(historico, "_guardar_carga", return_value={"execution_id": 6}):
        historico.recargar_rango("motor", DESDE, HASTA, "base", "fuente")
    extraer.assert_called_once_with("fuente", DESDE, HASTA)


def test_recarga_sin_filas_no_mueve_la_marca_de_agua():
    resumen = {"franjas_leidas": 0, "rechazados": 0}
    with mock.patch.object(historico, "cargar", return_value=(resumen, [])), \
            mock.patch.object(ejecuciones, "iniciar", return_value={"id": 9}), \
            mock.patch.object(ejecuciones, "finalizar") as finalizar:
        resultado = historico._guardar_carga(mock.MagicMock(), pd.DataFrame(), pd.DataFrame(), {"modo": "recarga_rango"})
    assert resultado["execution_id"] == 9
    assert finalizar.call_args.kwargs["marca_agua_hasta"] is None


def test_una_recarga_fallida_queda_registrada():
    with mock.patch.object(historico, "cargar", side_effect=RuntimeError("fuente caida")), \
            mock.patch.object(ejecuciones, "iniciar", return_value={"id": 10}), \
            mock.patch.object(ejecuciones, "finalizar") as finalizar, pytest.raises(RuntimeError):
        historico._guardar_carga(mock.MagicMock(), pd.DataFrame(), pd.DataFrame(), {"modo": "recarga_rango"})
    assert finalizar.call_args.args[2] == "fallida"


def test_carga_inicial_lee_los_archivos_hasta_la_fecha(archivos):
    with mock.patch.object(historico, "_guardar_carga", return_value={"execution_id": 11}) as guardar:
        assert historico.carga_inicial("motor", HASTA) == {"execution_id": 11}
    assert guardar.call_args.args[1]["subidas"].tolist() == [1, 2]
    assert guardar.call_args.args[3]["modo"] == "carga_inicial"


def _correr_cli(*argumentos):
    with mock.patch("sys.argv", ["historico", *argumentos]):
        historico.main()


@pytest.fixture
def motores():
    with mock.patch.object(db, "motor_destino", return_value="destino"), \
            mock.patch.object(db, "motor_fuente", return_value="fuente") as fuente:
        yield fuente


def test_cli_recarga_un_rango_desde_archivos_sin_tocar_la_fuente(motores):
    with mock.patch.object(historico, "recargar_rango", return_value={}) as recargar:
        _correr_cli("--desde", "2025-12-02", "--hasta", "2025-12-03")
    recargar.assert_called_once_with("destino", DESDE, HASTA, "archivos", None)
    motores.assert_not_called()


def test_cli_recarga_un_rango_desde_la_base(motores):
    with mock.patch.object(historico, "recargar_rango", return_value={}) as recargar:
        _correr_cli("--desde", "2025-12-02", "--hasta", "2025-12-03", "--origen", "base")
    recargar.assert_called_once_with("destino", DESDE, HASTA, "base", "fuente")


def test_cli_sin_rango_hace_la_carga_inicial(motores):
    with mock.patch.object(historico, "carga_inicial", return_value={}) as cargar:
        _correr_cli("--hasta", "2025-12-03")
    cargar.assert_called_once_with("destino", HASTA)


@pytest.mark.parametrize("argumentos", [["--desde", "2025-12-02"], ["--origen", "base"]])
def test_cli_rechaza_combinaciones_incompletas(motores, argumentos):
    with pytest.raises(SystemExit) as salida:
        _correr_cli(*argumentos)
    assert salida.value.code == 2
