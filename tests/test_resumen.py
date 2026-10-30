import uuid

import pandas as pd
import pytest

from frecuencias.operacion import resumen

DIA = pd.Timestamp("2025-12-12")


def _rec(hora: int, recomendados: int, actuales: float | None, viable: bool = True, motivo: str | None = None,
         ocupacion: float = 0.7) -> dict:
    inicio = (DIA + pd.Timedelta(hours=hora, minutes=15)).tz_localize("America/Bogota")
    return {"id": uuid.uuid4(), "franja_inicio": inicio, "franja_fin": inicio + pd.Timedelta(hours=1),
            "buses_por_hora": recomendados, "buses_por_hora_actual": actuales, "headway_min": 60 / recomendados,
            "viable": viable, "motivo_no_viable": motivo, "demanda_esperada": 500.0, "ocupacion_esperada": ocupacion}


@pytest.mark.parametrize("rec, tipo, fragmento", [
    (_rec(6, 12, 10), "agregar", "Agregar 2 buses entre 06:15 y 07:15: de 10 a 12"),
    (_rec(6, 11, 10.4), "agregar", "Agregar 1 bus"),
    (_rec(6, 9, 10), "reducir", "Se puede retirar 1 bus"),
    (_rec(6, 6, 10, ocupacion=0.66), "reducir",
     "Se pueden retirar 4 buses entre 06:15 y 07:15: de 10 a 6 por hora; la ocupación esperada queda en 66%"),
    (_rec(6, 10, 10), "mantener", "Mantener 10 buses por hora"),
    (_rec(6, 8, None), "programar", "Programar 8 buses por hora entre 06:15 y 07:15 (headway de 8 min)"),
    (_rec(6, 30, 10, viable=False, motivo="La demanda exige 32 buses"), "no_viable", "La demanda exige 32 buses"),
    (None, "sin_dato", "Sin recomendación"),
])
def test_accion_en_lenguaje_operativo(rec, tipo, fragmento):
    accion = resumen.accion(rec)
    assert accion["tipo"] == tipo
    assert fragmento in accion["texto"]


def test_referencia_por_defecto_es_ahora_o_las_seis_del_dia_elegido():
    ahora = pd.Timestamp("2026-10-29 14:37")
    assert resumen.referencia_por_defecto(pd.Timestamp("2026-10-29"), ahora) == ahora
    assert resumen.referencia_por_defecto(DIA, ahora) == DIA + pd.Timedelta(hours=6)


def test_recomendacion_vigente_y_siguiente():
    recs = [_rec(h, 10, 10) for h in (5, 6, 7)]
    ahora, siguiente = resumen.vigentes(recs, DIA + pd.Timedelta(hours=6, minutes=30))
    assert ahora is recs[1]
    assert siguiente is recs[2]
    ahora, siguiente = resumen.vigentes(recs, DIA + pd.Timedelta(hours=5))
    assert ahora is recs[0]
    assert resumen.vigentes(recs, DIA + pd.Timedelta(hours=2))[0] is None
    assert resumen.vigentes(recs, DIA + pd.Timedelta(hours=9)) == (None, None)


def test_resumen_de_ruta_con_observado_conteo_y_alertas():
    recs = [_rec(7, 12, 10), _rec(6, 9, 10)]
    franjas = pd.date_range(DIA + pd.Timedelta(hours=6, minutes=15), periods=4, freq="15min")
    observado = pd.Series([100.0, 120.0, 110.0, 90.0], index=franjas)
    pendientes = [{"id": uuid.uuid4(), "codigo": c, "severidad": "critica", "explicacion": "x",
                   "ventana_inicio": None} for c in ("ALT-002", "ALT-001", "ALT-003", "ALT-004")]
    r = resumen.ruta(16, "CRA 54", recs, observado, DIA + pd.Timedelta(hours=6, minutes=20),
                     {"abiertas": 7, "criticas": 2}, pendientes)
    assert [h["recomendados"] for h in r["horas"]] == [9, 12]
    assert r["horas"][0]["demandaObservada"] == 420.0
    assert r["horas"][1]["demandaObservada"] is None
    assert r["ahora"]["tipo"] == "reducir"
    assert r["siguiente"]["tipo"] == "agregar"
    assert r["conteo"] == {"reducir": 1, "agregar": 1}
    assert r["alertas"]["delDia"] == 4
    assert len(r["alertas"]["principales"]) == 3
    assert r["alertas"]["criticas"] == 2


def test_horas_fuera_del_horario_de_despachos_no_piden_buses():
    ventana = (DIA + pd.Timedelta(hours=5), DIA + pd.Timedelta(hours=19, minutes=50))
    tarde = _rec(20, 4, 0)
    accion = resumen.accion(tarde, ventana)
    assert accion["tipo"] == "fuera_de_servicio"
    assert "05:00 a 19:50" in accion["texto"]
    assert resumen.accion(_rec(18, 6, 6), ventana)["tipo"] == "mantener"
    sin_observado = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    r = resumen.ruta(2, "SILENCIO", [_rec(18, 6, 6), tarde], sin_observado, DIA + pd.Timedelta(hours=18),
                     {"abiertas": 0, "criticas": 0}, [], ventana)
    assert r["conteo"] == {"mantener": 1, "fuera_de_servicio": 1}
    assert r["servicio"] == {"apertura": ventana[0], "cierre": ventana[1]}
    assert resumen.ruta(2, None, [], sin_observado, DIA, {}, [])["servicio"] is None
