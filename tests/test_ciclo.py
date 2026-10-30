from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import pandas as pd
import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

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
                              "pasajeros_predichos": predicho, "limite_inferior": predicho * 0.9,
                              "limite_superior": predicho * 1.1, "model_registry_id": modelo,
                              "horizonte_min": 60})
    conn = mock.MagicMock()
    conn.execute.return_value.mappings.return_value.all.return_value = filas
    return conn, pd.DataFrame(serie)


def _monitorear(dias: int):
    conn, serie = _escenario(dias)
    with mock.patch.object(ciclo.monitoreo_bd, "predicciones_por_dia", return_value=pd.Series(dtype=float)), \
            mock.patch.object(ciclo.monitoreo_bd, "alertas_gestionadas",
                              return_value=pd.DataFrame(columns=["codigo", "estado"])):
        return ciclo.monitorear(conn, {60: SimpleNamespace(model_registry_id=40)}, serie, HASTA,
                                {"error_pronostico": 15.0})


def _medida(medidas, metrica):
    return next(m for m in medidas if m["metrica"] == metrica)


def test_monitoreo_no_alerta_con_menos_de_tres_dias():
    alertas, medidas = _monitorear(1)
    assert alertas == []
    wape = _medida(medidas, "error_movil_wape")
    assert wape["valor"] == 0.3
    assert wape["estado"] == "sin_datos"
    assert wape["detalle"]["dias"] == 1


def test_monitoreo_alerta_por_error_y_cobertura_solo_del_modelo_activo():
    alertas, medidas = _monitorear(3)
    assert [a["codigo"] for a in alertas] == ["ALT-010", "ALT-010"]
    assert {a["clave_deduplicacion"].split(":")[1] for a in alertas} == {"h60", "cobertura"}
    assert _medida(medidas, "cobertura_intervalo")["valor"] == 0.0
    assert {m.get("model_registry_id") for m in medidas if "model_registry_id" in m} == {40}
    assert _medida(medidas, "volumen_predicciones")["estado"] == "sin_datos"


def test_segmentos_se_omiten_con_origen_archivos():
    assert ciclo.actualizar_segmentos(None, "archivos", {"franjas_leidas": 10}, 1)["estado"] == "omitido"
    with mock.patch.dict("os.environ", {"CICLO_SEGMENTOS": "desactivada"}):
        assert ciclo.actualizar_segmentos(None, "base", {"franjas_leidas": 10}, 1)["estado"] == "omitido"
    assert ciclo.actualizar_segmentos(None, "base", {"franjas_leidas": 0}, 1)["motivo"] == "sin datos nuevos"


def test_fallo_de_segmentos_no_interrumpe_el_ciclo():
    with mock.patch.object(ciclo.segmentos_carga, "actualizar", side_effect=RuntimeError('host "secreto"')), \
            mock.patch.object(ciclo.db, "motor_fuente"):
        resultado = ciclo.actualizar_segmentos(None, "base", {"franjas_leidas": 5, "desde": HASTA, "hasta": HASTA}, 1)
    assert resultado["estado"] == "fallido"
    assert "secreto" not in resultado["error"]


def _ventana(marca, desde, hasta):
    motor = mock.MagicMock()
    with mock.patch.object(ciclo.ejecuciones, "ultima_marca_agua", return_value=marca):
        return ciclo.ventana_operacion(motor, {"desde": pd.Timestamp(desde), "hasta": pd.Timestamp(hasta)})


def test_ventana_retoma_lo_pendiente_de_un_ciclo_que_no_termino():
    ultimo_exitoso = datetime(2025, 12, 12, 11, 0, tzinfo=timezone.utc)
    desde, hasta = _ventana(ultimo_exitoso, "2025-12-13 06:00", "2025-12-14 00:00")
    assert desde == pd.Timestamp("2025-12-12 06:00")
    assert hasta == pd.Timestamp("2025-12-14 00:00")


def test_ventana_sin_pendientes_ni_historia_y_con_tope_de_dias():
    assert _ventana(None, "2025-12-13 06:00", "2025-12-14")[0] == pd.Timestamp("2025-12-13 06:00")
    al_dia = datetime(2025, 12, 13, 11, 0, tzinfo=timezone.utc)
    assert _ventana(al_dia, "2025-12-13 06:00", "2025-12-14")[0] == pd.Timestamp("2025-12-13 06:00")
    muy_viejo = datetime(2025, 10, 1, tzinfo=timezone.utc)
    assert _ventana(muy_viejo, "2025-12-13 06:00", "2025-12-14")[0] == pd.Timestamp("2025-12-07")


def _paquetes(corte="2025-12-10"):
    return {60: SimpleNamespace(model_registry_id=40, meta={"data_cutoff": corte})}


def test_motivos_de_reentrenamiento_y_espera_minima_entre_intentos():
    ahora = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    deriva = [{"metrica": "deriva_psi", "estado": "alerta"}]
    assert ciclo.motivo_reentrenamiento(_paquetes(), HASTA, [{"codigo": "ALT-010"}], [], None, ahora) == "degradacion"
    assert ciclo.motivo_reentrenamiento(_paquetes(), HASTA, [], deriva, None, ahora) == "deriva"
    assert ciclo.motivo_reentrenamiento(_paquetes("2025-12-01"), HASTA, [], [], None, ahora) == "periodicidad"
    assert ciclo.motivo_reentrenamiento(_paquetes(), HASTA, [], [], None, ahora) is None
    reciente = ahora - timedelta(hours=2)
    assert ciclo.motivo_reentrenamiento(_paquetes(), HASTA, [{"codigo": "ALT-010"}], deriva, reciente, ahora) is None


def test_politica_de_reintentos():
    recuperadas = [{"id": 1, "tipo": "ciclo", "intento": 1}, {"id": 2, "tipo": "ciclo", "intento": ciclo.MAX_INTENTOS},
                   {"id": 3, "tipo": "ingesta", "intento": 1}]
    assert [r["id"] for r in ciclo.reanudables(recuperadas)] == [1]
    assert ciclo.reintentable(OperationalError("select", {}, Exception("caida")))
    assert not ciclo.reintentable(ValueError("dato"))
    assert ciclo.espera_reintento(1) == pd.Timedelta(minutes=5)
    assert ciclo.espera_reintento(9) == pd.Timedelta(minutes=45)


def test_iniciar_traduce_el_indice_unico_a_ciclo_en_curso():
    motor = mock.MagicMock()
    error = IntegrityError("insert", {}, Exception('duplicate key violates "ux_ejecucion_ciclo_en_curso"'))
    motor.begin.return_value.__enter__.side_effect = error
    with pytest.raises(ciclo.CicloEnCurso):
        ciclo.iniciar(motor, "base", 1, "auto")
    otro = IntegrityError("insert", {}, Exception("otra restriccion"))
    motor.begin.return_value.__enter__.side_effect = otro
    with pytest.raises(IntegrityError):
        ciclo.iniciar(motor, "base", 1, "auto")


@pytest.mark.parametrize("original, mensaje", [
    (None, "No existe"),
    ({"tipo": "ingesta", "estado": "fallida"}, "tipo ciclo"),
    ({"tipo": "ciclo", "estado": "exitosa"}, "esta exitosa"),
])
def test_solo_se_reintentan_ciclos_fallidos_o_interrumpidos(original, mensaje):
    with mock.patch.object(ciclo.ejecuciones, "obtener", return_value=original), \
            pytest.raises((LookupError, ValueError), match=mensaje):
        ciclo.preparar_reintento(mock.MagicMock(), 7)


def test_reintento_hereda_parametros_y_correlacion():
    original = {"tipo": "ciclo", "estado": "interrumpida", "intento": 1, "correlation_id": "c-1",
                "detalle": {"origen": "archivos", "dias": 2, "reentrenar": "nunca"}}
    with mock.patch.object(ciclo.ejecuciones, "obtener", return_value=original), \
            mock.patch.object(ciclo, "iniciar", return_value={"id": 8, "intento": 2}) as iniciar:
        e, argumentos = ciclo.preparar_reintento(mock.MagicMock(), 7, "ana")
    assert e["id"] == 8
    assert argumentos == {"origen": "archivos", "dias": 2, "reentrenar": "nunca"}
    assert iniciar.call_args.kwargs == {"origen": "archivos", "dias": 2, "reentrenar": "nunca",
                                             "correlation_id": "c-1", "solicitado_por": "ana", "reintento_de": 7,
                                             "intento": 2}


def test_alertas_de_despacho_usan_parametros_por_ruta_y_horas_cerradas():
    viajes = pd.DataFrame([{"id_ruta": 2, "fechasalida": pd.Timestamp("2025-12-12 07:00") + pd.Timedelta(minutes=10 * k),
                            "fechainicio": None, "cancelada": False, "excede_plazo_salida": False,
                            "idvehiculo": f"B{k}"} for k in range(12)])
    with mock.patch.object(ciclo.parametros, "vigentes", return_value={}):
        alertas = ciclo.alertas_despacho(mock.MagicMock(), viajes, pd.Timestamp("2025-12-12 06:30"),
                                         pd.Timestamp("2025-12-12 08:30"))
    assert [a["ventana_inicio"] for a in alertas] == [pd.Timestamp("2025-12-12 07:00")]
    assert ciclo.alertas_despacho(None, viajes.drop(columns="excede_plazo_salida"), HASTA, HASTA) == []
    assert ciclo.alertas_despacho(None, None, HASTA, HASTA) == []
