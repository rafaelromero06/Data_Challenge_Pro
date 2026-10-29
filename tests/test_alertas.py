import pandas as pd
import pytest

from frecuencias.alertas import reglas
from frecuencias.optimizacion.frecuencia import recomendar
from frecuencias.optimizacion.parametros import parametros_hora

INICIO = pd.Timestamp("2025-12-01 07:00")
FIN = INICIO + pd.Timedelta(hours=1)


def _rec(demanda, alta, actual):
    p = parametros_hora({"factor_carga": 0.4, "flota_disponible": 1000.0}, 7, 0)
    rec = recomendar(demanda, alta, p, buses_actual=actual)
    rec |= {"id_ruta": 2, "franja_inicio": INICIO, "franja_fin": FIN, "horizonte_min": 60}
    return rec, p


def test_oferta_insuficiente_genera_alt_001_002_003():
    rec, p = _rec(1000, 1250, 8)
    alertas = reglas.por_recomendacion(rec, p)
    assert [a["codigo"] for a in alertas] == ["ALT-001", "ALT-002", "ALT-003"]
    assert alertas[0]["clave_deduplicacion"] == "ALT-001:2:2025-12-01T07:00"
    assert alertas[1]["severidad"] == "critica"


def test_baja_utilizacion_genera_alt_004():
    rec, p = _rec(100, 120, 20)
    assert [a["codigo"] for a in reglas.por_recomendacion(rec, p)] == ["ALT-004"]


def _rec_directa(recomendados, actual, carga_alta=100.0, ocupacion=0.5):
    return {"id_ruta": 2, "franja_inicio": INICIO, "franja_fin": FIN, "buses_por_hora": recomendados,
            "buses_por_hora_actual": actual, "carga_maxima_esperada": carga_alta, "ocupacion_actual": ocupacion,
            "viable": True, "motivo_no_viable": None, "headway_min": round(60 / recomendados, 2)}


def _codigos(rec, p):
    return [a["codigo"] for a in reglas.por_recomendacion(rec, p)]


def test_alt_003_respeta_la_tolerancia_absoluta_o_relativa():
    p = parametros_hora({}, 7, 0)
    assert _codigos(_rec_directa(14, 13), p) == []
    assert _codigos(_rec_directa(14, 12), p) == ["ALT-003"]
    assert _codigos(_rec_directa(5, 4), p) == ["ALT-003"]
    assert reglas.oferta_minima_aceptable(14, p) == 12
    assert reglas.oferta_minima_aceptable(5, p) == pytest.approx(4.25)


def test_alt_001_solo_cuando_la_demanda_supera_la_capacidad():
    p = parametros_hora({}, 7, 0)
    assert _codigos(_rec_directa(10, 10, carga_alta=490), p) == []
    assert _codigos(_rec_directa(10, 10, carga_alta=510), p) == ["ALT-001"]


def test_umbrales_de_alerta_vienen_de_los_parametros_versionados():
    valores = {"tolerancia_frecuencia_buses": 1.0, "tolerancia_frecuencia_pct": 0.0, "umbral_alta_demanda": 85.0}
    p = parametros_hora(valores, 7, 0)
    assert (p.tolerancia_buses, p.tolerancia_pct, p.umbral_alta_demanda) == (1.0, 0.0, 0.85)
    assert _codigos(_rec_directa(14, 13), p) == ["ALT-003"]


def test_sin_oferta_actual_no_hay_alertas_de_frecuencia():
    rec, p = _rec(1000, 1250, None)
    assert reglas.por_recomendacion(rec, p) == []


def _obs(observado, predicho=100.0):
    return {"id_ruta": 2, "inicio": INICIO, "fin": FIN, "observado": observado, "predicho": predicho,
            "limite_inferior": 70.0, "limite_superior": 130.0}


def test_demanda_observada_fuera_del_intervalo():
    assert reglas.por_observacion(_obs(50), 0.15)[0]["codigo"] == "ALT-005"
    assert reglas.por_observacion(_obs(150), 0.15)[0]["codigo"] == "ALT-007"
    assert reglas.por_observacion(_obs(95), 0.15) == []
    assert reglas.por_observacion(_obs(50, predicho=0), 0.15) == []


def test_hueco_de_despachos_genera_alt_006():
    salidas = pd.Series(pd.to_datetime(["2025-12-01 07:00", "2025-12-01 07:05", "2025-12-01 07:30",
                                        "2025-12-01 07:38"]))
    alertas = reglas.por_despachos(salidas, 2, 10, INICIO, FIN)
    assert len(alertas) == 1
    assert alertas[0]["ventana_inicio"] == pd.Timestamp("2025-12-01 07:05")
    assert alertas[0]["valor_observado"] == 25.0 and alertas[0]["umbral"] == 20.0


def test_problemas_de_calidad_se_agrupan_por_ruta_y_dia():
    problemas = [
        {"regla": "CAL-004", "clave": {"id_ruta": 2, "fecha": "2025-12-01"}, "detalle": "sin lecturas"},
        {"regla": "CAL-003", "clave": {"id_ruta": 16, "ts": "2025-12-01T07:15:00"}, "detalle": "lectura imposible"},
        {"regla": "CAL-003", "clave": {"id_ruta": 16, "ts": "2025-12-01T09:00:00"}, "detalle": "lectura imposible"},
        {"regla": "CAL-101", "clave": {"idprogramacion": 1}, "detalle": "fin antes del inicio"},
    ]
    alertas = {a["codigo"]: a for a in reglas.por_calidad(problemas, 7)}
    assert set(alertas) == {"ALT-008", "ALT-009"}
    assert alertas["ALT-008"]["valor_observado"] == 2
    assert alertas["ALT-009"]["clave_deduplicacion"] == "ALT-009:2:2025-12-01"
    assert alertas["ALT-009"]["execution_id"] == 7


def test_retraso_de_datos():
    assert reglas.por_retraso(INICIO, INICIO + pd.Timedelta(hours=10), 2) == []
    alerta = reglas.por_retraso(INICIO, INICIO + pd.Timedelta(hours=60), 2)[0]
    assert alerta["codigo"] == "ALT-009" and alerta["valor_observado"] == 60.0


def test_degradacion_del_modelo():
    assert reglas.por_modelo(0.10, 0.15, 60, 1, INICIO, FIN, 300) == []
    alerta = reglas.por_modelo(0.20, 0.15, 60, 1, INICIO, FIN, 300)[0]
    assert alerta["codigo"] == "ALT-010" and alerta["clave_deduplicacion"] == "ALT-010:h60:2025-12-01"
