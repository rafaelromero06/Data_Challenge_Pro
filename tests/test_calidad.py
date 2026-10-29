import numpy as np
import pandas as pd

from frecuencias.analitica.calidad import REGLAS, validar_demanda, validar_viajes


def _demanda():
    dias = pd.date_range("2025-11-03", periods=5, freq="D")
    filas = [{"id_ruta": 2, "ts": d + pd.Timedelta(hours=6, minutes=15 * k), "subidas": 10.0, "bajadas": 10.0,
              "nro_viajes": 2, "lecturas": 100, "lecturas_negativas": 0, "max_subida_lectura": 5}
             for d in dias for k in range(10)]
    df = pd.DataFrame(filas)
    dia = df["ts"].dt.normalize()
    df.loc[dia == dias[4], "lecturas"] = 0
    df.loc[0, "max_subida_lectura"] = 200
    df.loc[13, "subidas"] = -1.0
    df.loc[dia == dias[2], "bajadas"] = 40.0
    df.loc[31, "lecturas_negativas"] = 2
    extra = pd.DataFrame([df.loc[5].to_dict(), {**df.loc[6].to_dict(), "id_ruta": 99},
                          {**df.loc[7].to_dict(), "subidas": np.nan}])
    return pd.concat([df, extra], ignore_index=True), dias


def test_validar_demanda_aplica_cada_regla():
    df, dias = _demanda()
    validado, problemas = validar_demanda(df, {2})
    reglas = {p["regla"] for p in problemas}
    assert reglas == {"CAL-001", "CAL-002", "CAL-003", "CAL-004", "CAL-005", "CAL-006", "CAL-007", "CAL-008"}
    assert len(validado) == 50
    assert validado.loc[validado["ts"].dt.normalize() == dias[4], "motivo_invalido"].eq("CAL-004").all()
    assert set(validado.loc[~validado["valido"], "motivo_invalido"]) == {"CAL-002", "CAL-003", "CAL-004"}
    assert (~validado["valido"]).sum() == 12


def test_problemas_llevan_dimension_y_tratamiento_del_catalogo():
    _, problemas = validar_demanda(_demanda()[0], {2})
    for p in problemas:
        dimension, severidad, tratamiento, _ = REGLAS[p["regla"]]
        assert (p["dimension"], p["severidad"], p["tratamiento"]) == (dimension, severidad, tratamiento)


def test_demanda_limpia_no_genera_problemas():
    df, _ = _demanda()
    limpio = df.iloc[:30].assign(max_subida_lectura=5, subidas=10.0, bajadas=10.0, lecturas_negativas=0)
    validado, problemas = validar_demanda(limpio, {2})
    assert problemas == [] and validado["valido"].all()


def test_validar_viajes():
    base = pd.Timestamp("2025-11-03 06:00")
    viajes = pd.DataFrame([
        {"idprogramacion": 1, "id_ruta": 2, "fechasalida": base, "fechainicio": base,
         "fechafin": base + pd.Timedelta(minutes=90), "cancelada": "N", "finalizado": "S"},
        {"idprogramacion": 2, "id_ruta": 2, "fechasalida": base, "fechainicio": base,
         "fechafin": base - pd.Timedelta(minutes=5), "cancelada": "N", "finalizado": "S"},
        {"idprogramacion": 3, "id_ruta": 2, "fechasalida": base, "fechainicio": base,
         "fechafin": base + pd.Timedelta(minutes=400), "cancelada": "N", "finalizado": "S"},
        {"idprogramacion": 4, "id_ruta": 2, "fechasalida": base, "fechainicio": base,
         "fechafin": base + pd.Timedelta(minutes=60), "cancelada": "S", "finalizado": "S"},
        {"idprogramacion": 5, "id_ruta": 99, "fechasalida": base, "fechainicio": base,
         "fechafin": base + pd.Timedelta(minutes=60), "cancelada": "N", "finalizado": "S"},
        {"idprogramacion": np.nan, "id_ruta": 2, "fechasalida": base, "fechainicio": base,
         "fechafin": base, "cancelada": "N", "finalizado": "N"},
    ])
    validos, problemas = validar_viajes(viajes, {2})
    por_regla = {p["regla"]: p["clave"].get("idprogramacion") for p in problemas}
    assert por_regla == {"CAL-001": None, "CAL-104": 5, "CAL-101": 2, "CAL-103": 3, "CAL-102": 4}
    assert sorted(validos["idprogramacion"].astype(int)) == [1, 2, 3, 4]
