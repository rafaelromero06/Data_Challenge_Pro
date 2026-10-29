import argparse
import json
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frecuencias import db
from frecuencias.ingesta.consultas import QUERY_CONTEO, QUERY_LECTURAS_SEGMENTO, QUERY_VIAJES_RANGO


def nodos(plan: dict) -> list[dict]:
    resultado, pendientes = [], [(plan, 0)]
    while pendientes:
        actual, profundidad = pendientes.pop()
        resultado.append({"nivel": profundidad, "nodo": actual["Node Type"], "tabla": actual.get("Relation Name"),
                          "indice": actual.get("Index Name"), "filas": actual.get("Actual Rows"),
                          "ms": round(actual.get("Actual Total Time", 0), 1),
                          "bloques_cache": actual.get("Shared Hit Blocks"),
                          "bloques_disco": actual.get("Shared Read Blocks")})
        pendientes.extend((hijo, profundidad + 1) for hijo in reversed(actual.get("Plans", [])))
    return resultado


def explicar(conn, consulta: str, parametros: dict) -> dict:
    resultado = conn.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + consulta), parametros).scalar_one()
    raiz = resultado[0]
    return {"planificacion_ms": round(raiz["Planning Time"], 1), "ejecucion_ms": round(raiz["Execution Time"], 1),
            "filas": raiz["Plan"].get("Actual Rows"), "nodos": nodos(raiz["Plan"]),
            "lecturas_secuenciales": [n["tabla"] for n in nodos(raiz["Plan"]) if n["nodo"] == "Seq Scan"]}


def main():
    parser = argparse.ArgumentParser(description="EXPLAIN (ANALYZE, BUFFERS) de las consultas incrementales")
    parser.add_argument("--dia", default="2025-11-11")
    parser.add_argument("--salida", default="reportes/explain_fuente.json")
    args = parser.parse_args()
    desde = pd.Timestamp(args.dia)
    hasta = desde + pd.Timedelta(days=1)
    informe = {"dia": args.dia}
    with db.motor_fuente().connect() as conn:
        informe["solo_lectura"] = conn.execute(text("SHOW default_transaction_read_only")).scalar_one()
        informe["statement_timeout"] = conn.execute(text("SHOW statement_timeout")).scalar_one()
        parametros = {"desde": (desde - pd.Timedelta(hours=48)).to_pydatetime(), "hasta": hasta.to_pydatetime()}
        informe["viajes_del_rango"] = explicar(conn, QUERY_VIAJES_RANGO, parametros)
        ids = [r[0] for r in conn.execute(text(
            "SELECT idprogramacion FROM progvehiculos WHERE fechasalida >= :desde AND fechasalida < :hasta"),
            parametros)]
        informe["viajes"] = len(ids)
        informe["conteo_agregado"] = explicar(conn, QUERY_CONTEO, {"ids": ids})
        informe["lecturas_por_segmento"] = explicar(conn, QUERY_LECTURAS_SEGMENTO, {"ids": ids})
    Path(args.salida).parent.mkdir(parents=True, exist_ok=True)
    Path(args.salida).write_text(json.dumps(informe, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: (v if not isinstance(v, dict) else {c: v[c] for c in ("planificacion_ms", "ejecucion_ms", "filas",
                                                                                  "lecturas_secuenciales")})
                      for k, v in informe.items()}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
