import math

import pandas as pd
import pytest

from frecuencias.optimizacion import objetivo, oferta
from frecuencias.optimizacion.frecuencia import buses_necesarios, recomendar
from frecuencias.optimizacion.parametros import GENERALES, franja_de, parametros_hora

NOCTURNO = GENERALES["headway_maximo:nocturno"]
DIURNO = GENERALES["headway_maximo:diurno"]


def test_parametros_por_franja_y_tipo_de_dia():
    valores = {"factor_carga:06-09": 0.42, "tiempo_ciclo:sabado:06-09": 200.0, "flota_disponible": 55.0}
    p = parametros_hora(valores, 7, 1)
    assert (p.franja, p.tipo_dia) == ("06-09", "sabado")
    assert (p.factor_carga, p.tiempo_ciclo_min, p.flota_disponible) == (0.42, 200.0, 55)
    assert p.ocupacion_objetivo == pytest.approx(0.8)
    assert p.headway_maximo == DIURNO


def test_parametro_de_ruta_y_valor_general_por_defecto():
    p = parametros_hora({}, 10, 0)
    assert p.capacidad == GENERALES["capacidad_nominal"]
    assert p.tiempo_ciclo_min == GENERALES["tiempo_ciclo"]
    assert p.flota_disponible is None


def test_headway_maximo_nocturno():
    assert parametros_hora({}, 7, 2).headway_maximo == NOCTURNO
    assert parametros_hora({}, 21, 0).headway_maximo == NOCTURNO
    assert parametros_hora({}, 4, 0).headway_maximo == NOCTURNO


def test_franja_fuera_del_rango_operativo():
    assert franja_de(3) == "04-06" and franja_de(22) == "18-21" and franja_de(12) == "12-15"


def test_formula_de_buses():
    assert buses_necesarios(400, 50, 0.8) == 10
    assert buses_necesarios(401, 50, 0.8) == 11
    assert buses_necesarios(0, 50, 0.8) == 0


def test_funcion_objetivo_con_pesos_de_referencia_coincide_con_la_linea_base():
    p = parametros_hora({"factor_carga": 0.4, "tiempo_ciclo": 240.0, "flota_disponible": 100.0}, 7, 0)
    rec = recomendar(1000, 1250, p)
    assert rec["carga_maxima_esperada"] == 500
    assert rec["buses_por_hora"] == 13 and rec["limitado_por"] == "funcion_objetivo"
    assert rec["linea_base"]["buses_por_hora"] == 13
    assert rec["headway_min"] == pytest.approx(60 / 13, abs=0.01)
    assert rec["vehiculos_requeridos"] == math.ceil(240.0 * 13 / 60)
    assert rec["viable"] and rec["motivo_no_viable"] is None
    assert rec["objetivo"]["alternativas"][0]["buses"] == 13
    assert rec["objetivo"]["costo_total"] == rec["objetivo"]["alternativas"][0]["costo_total"]


def test_exceso_esperado_de_pasajeros():
    assert objetivo.exceso_esperado(100, 0, 80) == 20
    assert objetivo.exceso_esperado(100, 0, 120) == 0
    assert objetivo.exceso_esperado(100, 10, 100) == pytest.approx(10 / math.sqrt(2 * math.pi))
    assert objetivo.exceso_esperado(100, 10, 130) < objetivo.exceso_esperado(100, 10, 110)


def test_pesos_de_la_funcion_objetivo_mueven_la_frecuencia():
    base = {"factor_carga": 0.4, "tiempo_ciclo": 240.0}
    referencia = recomendar(1000, 1250, parametros_hora(base, 7, 0))["buses_por_hora"]
    mas_sobreocupacion = recomendar(1000, 1250, parametros_hora(base | {"peso_sobreocupacion": 5.0}, 7, 0))
    mas_costo = recomendar(1000, 1250, parametros_hora(base | {"peso_costo_operativo": 20.0}, 7, 0))
    assert mas_sobreocupacion["buses_por_hora"] > referencia > mas_costo["buses_por_hora"]
    assert mas_costo["limitado_por"] == "capacidad_critica"
    assert mas_costo["buses_por_hora"] == math.ceil(400 / (50 * 0.9))


def test_desviacion_frente_a_la_operacion_actual_estabiliza_la_frecuencia():
    p = parametros_hora({"factor_carga": 0.4, "tiempo_ciclo": 240.0, "peso_desviacion_frecuencia": 50.0}, 7, 0)
    assert recomendar(1000, 1250, p, buses_actual=12)["buses_por_hora"] == 12
    assert recomendar(1000, 1250, p, buses_actual=14)["buses_por_hora"] == 14


def test_flota_es_restriccion_dura_cuando_alcanza_el_minimo():
    p = parametros_hora({"factor_carga": 0.4, "tiempo_ciclo": 240.0, "flota_disponible": 44.0}, 7, 0)
    rec = recomendar(1000, 1250, p)
    assert rec["buses_por_hora"] == 11 and rec["limitado_por"] == "flota"
    assert rec["vehiculos_requeridos"] <= 44 and rec["viable"]


def test_sensibilidad_de_pesos_cubre_cada_componente():
    rec = recomendar(1000, 1250, parametros_hora({"factor_carga": 0.4}, 7, 0))
    assert set(rec["objetivo"]["sensibilidad_pesos"]) == {
        f"{c}_x{f}" for c in ("costo_operativo", "tiempo_espera", "sobreocupacion", "desviacion_frecuencia")
        for f in ("0.5", "2")}
    assert set(rec["objetivo"]["componentes"]) == {"costo_operativo", "tiempo_espera", "sobreocupacion",
                                                   "desviacion_frecuencia"}


def test_servicio_minimo_con_demanda_baja():
    p = parametros_hora({}, 10, 0)
    rec = recomendar(10, 12, p)
    assert rec["buses_por_hora"] == math.ceil(60 / DIURNO)
    assert rec["limitado_por"] == "servicio_minimo"


def test_headway_minimo_topa_y_marca_no_viable():
    p = parametros_hora({"headway_minimo": 2.0, "flota_disponible": 1000.0}, 7, 0)
    rec = recomendar(5000, 6000, p)
    assert rec["buses_por_hora"] == 30 and rec["headway_min"] == 2.0
    assert not rec["viable"] and rec["limitado_por"] == "headway_minimo"


def test_flota_insuficiente_marca_no_viable():
    p = parametros_hora({"flota_disponible": 5.0, "tiempo_ciclo": 240.0}, 7, 0)
    rec = recomendar(1000, 1000, p)
    assert not rec["viable"] and "flota disponible" in rec["motivo_no_viable"]


def test_vehiculos_con_despachos_en_ruta_y_ocupacion_actual():
    p = parametros_hora({"factor_carga": 0.4}, 7, 0)
    rec = recomendar(1000, 1000, p, buses_actual=8, despachos_en_ruta=30)
    assert rec["vehiculos_requeridos"] == 30 + rec["buses_por_hora"]
    assert rec["metodo_vehiculos"] == "despachos_en_ventana_de_ciclo"
    assert rec["ocupacion_actual"] == pytest.approx(400 / (8 * 50))
    assert rec["headway_actual_min"] == 7.5


def test_sensibilidad_cubre_tres_ocupaciones_y_dos_escenarios():
    rec = recomendar(1000, 1250, parametros_hora({}, 7, 0))
    assert len(rec["sensibilidad"]) == 6
    assert rec["sensibilidad"]["ocupacion_65_alta"]["buses_por_hora"] >= rec["sensibilidad"]["ocupacion_85_alta"]["buses_por_hora"]


def test_no_se_recomienda_frecuencia_fuera_del_servicio_habitual():
    from frecuencias.operacion.recomendacion import recomendar_bloques
    predicciones = [{"franja_objetivo": pd.Timestamp("2025-12-15 00:15") + pd.Timedelta(minutes=15 * k),
                     "servicio_habitual": False} for k in range(8)]
    assert recomendar_bloques(predicciones, 2, {}, None) == []


def _viajes(salidas, canceladas=()):
    filas = [{"id_ruta": 2, "fechasalida": pd.Timestamp(s), "cancelada": s in canceladas} for s in salidas]
    return pd.DataFrame(filas)


def test_buses_actuales_observados_excluyen_cancelados():
    viajes = _viajes(["2025-12-01 07:00", "2025-12-01 07:10", "2025-12-01 07:20", "2025-12-01 07:30",
                      "2025-12-01 08:00"], canceladas={"2025-12-01 07:30"})
    assert oferta.buses_actuales(viajes, 2, pd.Timestamp("2025-12-01 07:00")) == (3.0, "observada")


def test_buses_actuales_habituales_cuando_el_dia_no_tiene_viajes():
    salidas = []
    for semanas, n in [(1, 4), (2, 6), (3, 5)]:
        dia = pd.Timestamp("2025-12-01 07:00") - pd.Timedelta(weeks=semanas)
        salidas += [dia + pd.Timedelta(minutes=10 * k) for k in range(n)]
    viajes = _viajes(salidas)
    assert oferta.buses_actuales(viajes, 2, pd.Timestamp("2025-12-01 07:00")) == (5.0, "habitual_4_semanas")


def test_buses_actuales_sin_datos():
    assert oferta.buses_actuales(_viajes([]).reindex(columns=["id_ruta", "fechasalida", "cancelada"]), 2,
                                 pd.Timestamp("2025-12-01 07:00")) == (None, "sin_datos")


def test_despachos_en_ventana_de_ciclo():
    viajes = _viajes(["2025-12-01 03:00", "2025-12-01 05:00", "2025-12-01 06:00", "2025-12-01 06:50"])
    assert oferta.despachos_en_ruta(viajes, 2, pd.Timestamp("2025-12-01 07:00"), 120) == 3
    assert oferta.despachos_en_ruta(viajes, 2, pd.Timestamp("2025-12-01 07:00"), 60) == 2
    assert oferta.despachos_en_ruta(viajes, 2, pd.Timestamp("2025-12-01 02:00"), 120) is None


def test_ventana_de_servicio_combina_lo_habitual_y_el_dia():
    dia = pd.Timestamp("2025-12-14")
    salidas = [dia - pd.Timedelta(weeks=s) + pd.Timedelta(hours=h) for s in (1, 2, 3) for h in (5, 20)]
    viajes = _viajes(salidas + [dia + pd.Timedelta(hours=5, minutes=10), dia + pd.Timedelta(hours=12)])
    assert oferta.ventana_servicio(viajes, 2, dia + pd.Timedelta(hours=14)) == (dia + pd.Timedelta(hours=5),
                                                                               dia + pd.Timedelta(hours=20))
    tarde = _viajes([dia + pd.Timedelta(hours=5), dia + pd.Timedelta(hours=21)])
    assert oferta.ventana_servicio(tarde, 2, dia)[1] == dia + pd.Timedelta(hours=21)
    assert oferta.ventana_servicio(viajes, 16, dia) is None


def _bloque(dia: pd.Timestamp, hora: int, minuto: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    inicio = dia + pd.Timedelta(hours=hora, minutes=minuto)
    return inicio, inicio + pd.Timedelta(hours=1)


def test_fraccion_del_bloque_dentro_del_horario_de_despachos():
    dia = pd.Timestamp("2025-12-14")
    ventana = (dia + pd.Timedelta(hours=5), dia + pd.Timedelta(hours=20))
    assert oferta.fraccion_en_servicio(ventana, *_bloque(dia, 7, 15)) == 1.0
    assert oferta.fraccion_en_servicio(ventana, *_bloque(dia, 19, 15)) == pytest.approx(46 / 60)
    assert oferta.fraccion_en_servicio(ventana, *_bloque(dia, 20, 15)) == 0.0
    assert oferta.fraccion_en_servicio(ventana, *_bloque(dia, 4, 15)) == pytest.approx(15 / 60)
    assert oferta.fraccion_en_servicio(None, *_bloque(dia, 23, 0)) == 1.0


def _predicciones_desde(inicio: str, franjas: int) -> list[dict]:
    base = pd.Timestamp(inicio)
    return [{"franja_objetivo": base + pd.Timedelta(minutes=15 * k), "servicio_habitual": True,
             "variables": {"tipo_dia": 2}, "pasajeros_predichos": 60.0, "limite_inferior": 50.0,
             "limite_superior": 80.0, "minutos_antelacion": 15 * (k + 1), "prediction_id": None,
             "explicacion": {"variables": []}} for k in range(franjas)]


def test_no_se_recomienda_despues_del_ultimo_despacho_y_la_hora_parcial_se_prorratea():
    from frecuencias.operacion.recomendacion import recomendar_bloques
    dia = pd.Timestamp("2025-12-14")
    salidas = [dia - pd.Timedelta(weeks=s) + pd.Timedelta(hours=h) for s in (1, 2) for h in (5, 20)]
    salidas += [dia + pd.Timedelta(hours=19, minutes=m) for m in (30, 45, 60)]
    recs = recomendar_bloques(_predicciones_desde("2025-12-14 19:15", 8), 2, {}, _viajes(salidas))
    assert len(recs) == 1
    assert recs[0]["franja_inicio"] == dia + pd.Timedelta(hours=19, minutes=15)
    assert recs[0]["fraccion_servicio"] == pytest.approx(46 / 60, abs=1e-3)
    assert recs[0]["buses_por_hora_actual"] == pytest.approx(3 / (46 / 60), abs=0.01)
    assert recs[0]["ventana_servicio"] == [str(dia + pd.Timedelta(hours=5)), str(dia + pd.Timedelta(hours=20))]
