from types import SimpleNamespace
from unittest import mock

import pandas as pd

from frecuencias.operacion import ciclo

HASTA = pd.Timestamp("2025-12-13")


def _escenario(dias: int):
    filas, serie = [], []
    for d in range(dias):
        for k in range(8):
            ts = pd.Timestamp("2025-12-12 07:00") - pd.Timedelta(days=d) + pd.Timedelta(minutes=15 * k)
            serie.append({"id_ruta": 2, "ts": ts, "subidas": 100.0, "observado": True})
            for modelo, predicho in ((40, 130.0), (25, 100.0)):
                filas.append({"id_ruta": 2, "franja_objetivo": ts.tz_localize("America/Bogota").tz_convert("UTC"),
                              "pasajeros_predichos": predicho, "model_registry_id": modelo, "horizonte_min": 60})
    conn = mock.MagicMock()
    conn.execute.return_value.mappings.return_value.all.return_value = filas
    return conn, pd.DataFrame(serie)


def test_monitoreo_no_alerta_con_menos_de_tres_dias():
    conn, serie = _escenario(1)
    alertas, resumen = ciclo.monitorear(conn, {60: SimpleNamespace(model_registry_id=40)}, serie, HASTA, 0.15)
    assert alertas == []
    assert resumen["60"]["wape_movil"] == 0.3 and resumen["60"]["dias"] == 1 and not resumen["60"]["evaluable"]


def test_monitoreo_alerta_con_tres_dias_y_solo_evalua_el_modelo_activo():
    conn, serie = _escenario(3)
    alertas, resumen = ciclo.monitorear(conn, {60: SimpleNamespace(model_registry_id=40)}, serie, HASTA, 0.15)
    assert [a["codigo"] for a in alertas] == ["ALT-010"]
    assert resumen["60"]["model_registry_id"] == 40 and resumen["60"]["evaluable"]


def test_segmentos_se_omiten_con_origen_archivos():
    assert ciclo.actualizar_segmentos(None, "archivos", {"franjas_leidas": 10}, 1)["estado"] == "omitido"
    with mock.patch.dict("os.environ", {"CICLO_SEGMENTOS": "desactivada"}):
        assert ciclo.actualizar_segmentos(None, "base", {"franjas_leidas": 10}, 1)["estado"] == "omitido"
    assert ciclo.actualizar_segmentos(None, "base", {"franjas_leidas": 0}, 1)["motivo"] == "sin datos nuevos"


def test_fallo_de_segmentos_no_interrumpe_el_ciclo():
    with mock.patch.object(ciclo.segmentos_carga, "actualizar", side_effect=RuntimeError('host "secreto"')), \
            mock.patch.object(ciclo.db, "motor_fuente"):
        resultado = ciclo.actualizar_segmentos(None, "base", {"franjas_leidas": 5, "desde": HASTA, "hasta": HASTA}, 1)
    assert resultado["estado"] == "fallido" and "secreto" not in resultado["error"]
