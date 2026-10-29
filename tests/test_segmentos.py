import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from frecuencias import db
from frecuencias.segmentos import carga

VACIO = (pd.DataFrame(columns=["estacionalidad", "id_estacionalidad", "fecha_inicio", "fecha_fin"]),
         pd.DataFrame(columns=["fecha", "hora_inicio", "hora_fin", "porcentaje_impacto"]))


def _obs(viaje, segmento, local, subidas, carga_maxima, bajadas=0):
    return {"idprogramacion": viaje, "id_segmento": segmento, "id_ruta": 2,
            "franja": pd.Timestamp(local).tz_localize("America/Bogota").tz_convert("UTC"), "subidas": subidas,
            "bajadas": bajadas, "pasajeros": 0, "carga_media": carga_maxima / 2, "carga_maxima": carga_maxima}


def test_perfil_con_ceros_implicitos_y_cuotas():
    observaciones = pd.DataFrame([
        _obs(1, 10, "2025-11-11 07:00", 4, 20.0), _obs(1, 11, "2025-11-11 07:15", 2, 30.0),
        _obs(2, 10, "2025-11-11 07:30", 6, 40.0),
    ])
    perfil = carga.construir_perfil(observaciones, VACIO).set_index("id_segmento")
    assert set(perfil["tipo_dia"]) == {"habil"} and set(perfil["hora"]) == {7}
    assert perfil.loc[10, "nro_viajes"] == 2 and perfil.loc[10, "subidas_prom"] == 5
    assert perfil.loc[10, "subidas_min"] == 4 and perfil.loc[11, "subidas_min"] == 0
    assert perfil.loc[11, "subidas_prom"] == 1 and perfil.loc[11, "subidas_max"] == 2
    assert perfil["cuota_subidas"].sum() == pytest.approx(1.0)
    assert perfil.loc[10, "cuota_subidas"] == pytest.approx(10 / 12, abs=1e-6)
    assert perfil.loc[10, "carga_max"] == 40 and perfil.loc[10, "carga_p90"] == pytest.approx(38.0)


def test_tipos_de_dia_con_festivos():
    calendario = (pd.DataFrame([{"estacionalidad": "FESTIVO_NACIONAL", "id_estacionalidad": 1,
                                 "fecha_inicio": "2025-11-17", "fecha_fin": "2025-11-17"}]), VACIO[1])
    fechas = pd.Series(pd.to_datetime(["2025-11-14", "2025-11-15", "2025-11-16", "2025-11-17"]))
    assert carga.tipos_dia(fechas, calendario).tolist() == ["habil", "sabado", "dom_fest", "dom_fest"]


def test_perfil_vacio():
    assert carga.construir_perfil(pd.DataFrame(), VACIO).empty


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


def _lectura(id_lectura, viaje, hora, lon, subidas, bajadas, lat=10.9001):
    return {"id": id_lectura, "idprogramacion": viaje, "id_ruta": 999, "fecha": pd.Timestamp(f"2025-11-11 {hora}"),
            "latitud": lat, "longitud": lon, "subidas": subidas, "bajadas": bajadas,
            "acumulada_subida": None, "acumulada_bajada": None}


@pytest.mark.integracion
def test_asignacion_al_segmento_mas_cercano_y_carga_balanceada(conn):
    conn.execute(text("""
        INSERT INTO ai_frecuencias.route_segment (id_segmento, id_config, id_ruta, nro_segmento, longitud_m, inicio_m, geom)
        VALUES (99901, 999, 999, 1, 1094, 0, ST_GeomFromText('LINESTRING(-74.80 10.90, -74.79 10.90)', 4326)),
               (99902, 999, 999, 2, 1094, 1094, ST_GeomFromText('LINESTRING(-74.79 10.90, -74.78 10.90)', 4326))"""))
    lecturas = pd.DataFrame([
        _lectura(1, 1, "07:00", -74.799, 5, 0), _lectura(2, 1, "07:10", -74.70, 1, 0, lat=10.95),
        _lectura(3, 1, "07:20", -74.781, 0, 5), _lectura(4, 2, "07:05", -74.795, 3, 0),
    ])
    observaciones, problemas = carga.procesar_lote(conn, lecturas)
    obs = observaciones.set_index(["idprogramacion", "id_segmento"])
    assert obs.loc[(1, 99901), "subidas"] == 5 and float(obs.loc[(1, 99901), "carga_maxima"]) == 5.0
    assert obs.loc[(1, 99902), "bajadas"] == 5 and float(obs.loc[(1, 99902), "carga_maxima"]) == 0.0
    assert pd.isna(obs.loc[(2, 99901), "carga_maxima"])
    assert pd.Timestamp(obs.loc[(1, 99902), "franja"]).tz_convert("America/Bogota").strftime("%H:%M") == "07:15"
    assert float(obs["distancia_max_m"].max()) < carga.DISTANCIA_MAXIMA_RUTA_M
    assert {(p["regla"], p["clave"]["idprogramacion"]) for p in problemas} == {("CAL-201", 1), ("CAL-202", 2)}
