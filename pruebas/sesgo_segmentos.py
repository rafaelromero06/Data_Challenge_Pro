import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frecuencias import db
from frecuencias.analitica.carga import cargar_calendario
from frecuencias.persistencia.tablas import demanda, observacion_segmento, prediccion, segmento
from frecuencias.segmentos.carga import construir_perfil, tipos_dia
from frecuencias.tiempo import a_zona

ZONA = "America/Bogota"
ANTELACIONES_MIN = (15, 30, 45, 60)
HORAS_PICO = {6, 7, 8, 17, 18, 19}
SEGMENTOS_EN_INFORME = 5


def _local(serie: pd.Series) -> pd.Series:
    return pd.to_datetime(serie, utc=True).dt.tz_convert(ZONA).dt.tz_localize(None)


def leer(conn, inicio: pd.Timestamp, fin: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    predichas = pd.read_sql(
        select(prediccion.c.id_ruta, prediccion.c.franja_objetivo, prediccion.c.pasajeros_predichos,
               prediccion.c.generada_en)
        .where(prediccion.c.horizonte_min.in_(ANTELACIONES_MIN), prediccion.c.franja_objetivo >= a_zona(inicio),
               prediccion.c.franja_objetivo < a_zona(fin)), conn)
    observaciones = pd.read_sql(select(observacion_segmento).where(observacion_segmento.c.franja < a_zona(fin)), conn)
    segmentos = pd.read_sql(select(segmento.c.id_segmento, segmento.c.nro_segmento, segmento.c.id_ruta), conn)
    return predichas, observaciones, segmentos


def subidas_de_ruta(conn, inicio: pd.Timestamp, fin: pd.Timestamp) -> float:
    return float(conn.execute(select(func.sum(demanda.c.subidas)).where(
        demanda.c.valido, demanda.c.ts >= a_zona(inicio), demanda.c.ts < a_zona(fin))).scalar_one() or 0)


def repartir(predichas: pd.DataFrame, cuotas: pd.DataFrame, calendario) -> pd.DataFrame:
    p = predichas.assign(local=_local(predichas["franja_objetivo"]), predicho=predichas["pasajeros_predichos"].astype(float))
    p = p.sort_values("generada_en").drop_duplicates(["id_ruta", "local"], keep="last")
    p["hora"] = p["local"].dt.hour
    p["fecha"] = p["local"].dt.normalize()
    p["tipo_dia"] = tipos_dia(p["fecha"], calendario)
    unido = p[["id_ruta", "local", "fecha", "tipo_dia", "hora", "predicho"]].merge(
        cuotas[["id_ruta", "tipo_dia", "hora", "id_segmento", "cuota_subidas"]],
        on=["id_ruta", "tipo_dia", "hora"], how="inner", validate="many_to_many")
    unido["predicho"] *= unido["cuota_subidas"].astype(float)
    return unido


def observado_por_franja(observaciones: pd.DataFrame) -> pd.DataFrame:
    o = observaciones.assign(local=_local(observaciones["franja"]))
    return o.groupby(["id_ruta", "id_segmento", "local"], as_index=False)["subidas"].sum().rename(
        columns={"subidas": "observado"})


def cruzar(repartido: pd.DataFrame, observado: pd.DataFrame) -> pd.DataFrame:
    unido = repartido.merge(observado, on=["id_ruta", "id_segmento", "local"], how="left",
                            validate="one_to_one").fillna({"observado": 0})
    return unido.groupby(["id_ruta", "id_segmento", "fecha", "tipo_dia", "hora"], as_index=False)[
        ["predicho", "observado"]].sum()


def medir(df: pd.DataFrame) -> dict:
    observado, predicho = df["observado"].sum(), df["predicho"].sum()
    return {"observado": round(float(observado), 1), "predicho": round(float(predicho), 1),
            "wape": round(float((df["predicho"] - df["observado"]).abs().sum() / observado), 4) if observado else None,
            "sesgo": round(float((predicho - observado) / observado), 4) if observado else None, "filas": len(df)}


def por_grupo(df: pd.DataFrame, columna: str) -> dict:
    return {str(valor): medir(grupo) for valor, grupo in df.groupby(columna)}


def informe(cruce: pd.DataFrame, segmentos: pd.DataFrame) -> dict:
    df = cruce.merge(segmentos, on=["id_segmento", "id_ruta"], how="left", validate="many_to_one")
    df["franja"] = np.where(df["hora"].isin(HORAS_PICO), "pico", "valle")
    mitad = df.groupby("id_ruta")["nro_segmento"].transform("max") / 2
    df["mitad_recorrido"] = np.where(df["nro_segmento"] <= mitad, "primera", "segunda")
    filas = df.groupby(["id_ruta", "id_segmento", "nro_segmento"], as_index=False).agg(
        observado=("observado", "sum"), predicho=("predicho", "sum"))
    filas = filas[filas["observado"] >= filas["observado"].median()]
    filas["sesgo"] = (filas["predicho"] - filas["observado"]) / filas["observado"]
    extremos = filas.reindex(filas["sesgo"].abs().sort_values(ascending=False).index).head(SEGMENTOS_EN_INFORME)
    return {
        "total": medir(df), "por_ruta": por_grupo(df, "id_ruta"), "por_tipo_dia": por_grupo(df, "tipo_dia"),
        "por_franja": por_grupo(df, "franja"), "por_mitad_de_recorrido": por_grupo(df, "mitad_recorrido"),
        "segmentos_con_mayor_sesgo": [
            {"id_ruta": int(f.id_ruta), "nro_segmento": int(f.nro_segmento), "observado": round(float(f.observado), 1),
             "predicho": round(float(f.predicho), 1), "sesgo": round(float(f.sesgo), 4)}
            for f in extremos.itertuples()],
    }


def main():
    parser = argparse.ArgumentParser(description="Mide el error y el sesgo de la demanda repartida por segmento, con "
                                                 "cuotas calculadas solo con datos previos a la ventana evaluada")
    parser.add_argument("--desde", default="2025-12-01")
    parser.add_argument("--hasta", default="2025-12-19")
    parser.add_argument("--salida", default="reportes/sesgo_segmentos.json")
    args = parser.parse_args()
    inicio, fin = pd.Timestamp(args.desde), pd.Timestamp(args.hasta)
    with db.motor_destino().connect() as conn:
        predichas, observaciones, segmentos = leer(conn, inicio, fin)
        subidas_ruta = subidas_de_ruta(conn, inicio, fin)
    previas = observaciones[pd.to_datetime(observaciones["franja"], utc=True) < a_zona(inicio)]
    ventana = observaciones[pd.to_datetime(observaciones["franja"], utc=True) >= a_zona(inicio)]
    calendario = cargar_calendario()
    cuotas = construir_perfil(previas, calendario)
    cruce = cruzar(repartir(predichas, cuotas, calendario), observado_por_franja(ventana))
    resultado = {"ventana": [args.desde, args.hasta], "antelaciones_min": list(ANTELACIONES_MIN),
                 "cuotas_desde_observaciones_previas": len(previas), "predicciones": len(predichas),
                 "cobertura_asignacion": round(float(ventana["subidas"].sum()) / subidas_ruta, 4),
                 **informe(cruce, segmentos)}
    Path(args.salida).parent.mkdir(parents=True, exist_ok=True)
    Path(args.salida).write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: resultado[k] for k in ("ventana", "total", "por_ruta", "por_mitad_de_recorrido")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
