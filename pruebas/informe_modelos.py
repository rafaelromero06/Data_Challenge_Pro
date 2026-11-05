import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frecuencias import db
from frecuencias.persistencia.tablas import modelo

CAMPOS = ("id", "model_id", "version", "horizonte_min", "estado", "feature_version", "semilla", "data_cutoff",
          "artefacto", "artefacto_sha256", "aprobado_por", "entrenado_en", "activado_en")


def comparacion(fila: dict) -> dict:
    prueba = (fila.get("metricas") or {}).get("prueba") or {}
    ventana = prueba.get("ventana_reentrenamiento") or {}
    return {k: ventana.get(k) for k in ("ventana", "franjas", "candidato", "activo", "linea_base", "activar")}


def main():
    parser = argparse.ArgumentParser(description="Resume los modelos activos a partir del registro de modelos")
    parser.add_argument("--salida", default="reportes/modelos_activos.json")
    args = parser.parse_args()
    with db.motor_destino().connect() as conn:
        filas = conn.execute(select(modelo).where(modelo.c.estado == "activo").order_by(modelo.c.horizonte_min)).mappings()
        activos = [{**{k: f[k] for k in CAMPOS}, "comparacion": comparacion(f)} for f in filas]
    Path(args.salida).parent.mkdir(parents=True, exist_ok=True)
    Path(args.salida).write_text(json.dumps(activos, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps([{k: a[k] for k in ("id", "horizonte_min", "version", "data_cutoff")} for a in activos],
                     ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
