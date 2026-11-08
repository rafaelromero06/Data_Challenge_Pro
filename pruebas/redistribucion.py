import argparse
import json
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frecuencias import db
from frecuencias.analitica.carga import cargar_calendario
from frecuencias.analitica.serie import preparar_serie
from frecuencias.config import HORIZONTES_MIN, NOMBRES_RUTAS
from frecuencias.inferencia.modelos import desde_registro
from frecuencias.inferencia.prediccion import Predictor
from frecuencias.ingesta import historico
from frecuencias.operacion import ciclo, pronostico, recomendacion
from frecuencias.persistencia import parametros
from frecuencias.persistencia.tablas import modelo
from frecuencias.tiempo import a_zona

ESTADOS_EN_SERVICIO = ("activo", "retirado")


def modelos_previos(conn, inicio: pd.Timestamp) -> dict:
    filas = conn.execute(select(modelo).where(modelo.c.estado.in_(ESTADOS_EN_SERVICIO),
                                              modelo.c.data_cutoff < a_zona(inicio))
                         .order_by(modelo.c.data_cutoff.desc(), modelo.c.id.desc())).mappings().all()
    elegidos = {}
    for fila in filas:
        elegidos.setdefault(int(fila["horizonte_min"]), dict(fila))
    faltan = sorted(set(HORIZONTES_MIN) - set(elegidos))
    if faltan:
        raise SystemExit(f"No hay modelos con corte anterior a {inicio:%Y-%m-%d} para los horizontes {faltan}")
    return elegidos


def referencias(dia: pd.Timestamp, conocidas: set) -> list[pd.Timestamp]:
    return [r for r in (dia + pd.Timedelta(hours=h) for h in ciclo.HORAS_REFERENCIA) if r in conocidas]


def recalcular(conn, desde: pd.Timestamp, hasta: pd.Timestamp, paquetes: dict) -> pd.DataFrame:
    serie = preparar_serie(historico.leer_demanda(conn))
    viajes = historico.leer_viajes(conn, desde=desde - pd.Timedelta(days=ciclo.DIAS_HISTORIA_VIAJES),
                                   hasta=hasta + pd.Timedelta(days=1))
    predictor = Predictor(paquetes, cargar_calendario())
    conocidas = set(serie["ts"])
    filas = []
    for id_ruta in ciclo.RUTAS:
        valores = parametros.vigentes(conn, id_ruta)
        observado = serie[(serie["id_ruta"] == id_ruta) & serie["observado"]].set_index("ts")["subidas"]
        for dia in pd.date_range(desde, hasta, freq="D", inclusive="left"):
            for referencia in referencias(dia, conocidas):
                predichas = pronostico.pronosticar(predictor, serie, id_ruta, referencia)
                for rec in recomendacion.recomendar_bloques(predichas, id_ruta, valores, viajes, observado)[:1]:
                    filas.append({
                        "id_ruta": id_ruta, "inicio": rec["franja_inicio"], "recomendado": rec["buses_por_hora"],
                        "operado": rec["buses_por_hora_actual"], "origen_operado": rec["origen_frecuencia_actual"],
                        "linea_base": rec["linea_base"]["buses_por_hora"], "ocupacion": rec["ocupacion_esperada"],
                        "ciclo_min": rec["tiempo_ciclo_min"], "viable": rec["viable"],
                    })
    return pd.DataFrame(filas)


def proporcion(serie: pd.Series) -> float:
    return round(float(serie.mean()), 4)


def resumir(df: pd.DataFrame, dias: int) -> dict:
    medidas = df[df["operado"].notna()]
    horas_recomendadas = (medidas["recomendado"] * medidas["ciclo_min"] / 60).sum()
    horas_operadas = (medidas["operado"] * medidas["ciclo_min"] / 60).sum()
    return {
        "horas_ruta": len(df),
        "horas_con_oferta_medida": len(medidas),
        "buses_por_hora_recomendados": round(float(medidas["recomendado"].mean()), 2),
        "buses_por_hora_operados": round(float(medidas["operado"].mean()), 2),
        "mediana_ocupacion_esperada": round(float(df["ocupacion"].median()), 4),
        "frente_a_linea_base": {"igual": proporcion(df["recomendado"] == df["linea_base"]),
                                "menos": proporcion(df["recomendado"] < df["linea_base"]),
                                "mas": proporcion(df["recomendado"] > df["linea_base"])},
        "vehiculo_horas": {"recomendadas": round(float(horas_recomendadas)), "operadas": round(float(horas_operadas)),
                           "diferencia": round(float(horas_recomendadas - horas_operadas)),
                           "diferencia_por_dia": round(float(horas_recomendadas - horas_operadas) / dias, 1)},
        "viables": proporcion(df["viable"]),
    }


def main():
    parser = argparse.ArgumentParser(description="Recalcula la frecuencia recomendada frente a la operada, hora a hora, "
                                                 "con los modelos que estaban en servicio antes del periodo")
    parser.add_argument("--desde", default="2025-12-01")
    parser.add_argument("--hasta", default="2025-12-15", help="Fecha local excluida")
    parser.add_argument("--salida", default="reportes/redistribucion.json")
    args = parser.parse_args()
    desde, hasta = pd.Timestamp(args.desde), pd.Timestamp(args.hasta)
    dias = (hasta - desde).days
    with db.motor_destino().connect() as conn:
        registro = modelos_previos(conn, desde)
        df = recalcular(conn, desde, hasta, desde_registro(registro))
    informe = {
        "periodo": [args.desde, args.hasta], "dias": dias,
        "metodo": "Mismas referencias que el ciclo (05:00 a 20:00) y primer bloque de cada una; parametros vigentes; "
                  "operado = salidas observadas en la hora, como tasa en horas parciales; vehiculo-horas = buses por "
                  "hora x tiempo de ciclo / 60",
        "modelos": {h: {"id": int(f["id"]), "version": f["version"], "estado": f["estado"],
                        "data_cutoff": str(f["data_cutoff"])} for h, f in sorted(registro.items())},
        "total": resumir(df, dias),
        "por_ruta": {NOMBRES_RUTAS[r]: resumir(g, dias) for r, g in df.groupby("id_ruta")},
        "operado_observado": proporcion(df["origen_operado"] == "observada"),
    }
    Path(args.salida).parent.mkdir(parents=True, exist_ok=True)
    Path(args.salida).write_text(json.dumps(informe, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(informe, ensure_ascii=False))


if __name__ == "__main__":
    main()
