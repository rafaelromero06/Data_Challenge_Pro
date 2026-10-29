import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frecuencias import db, seguridad


def esperar_ejecucion(cliente: httpx.Client, id_ejecucion: int, limite_s: float) -> dict:
    fin = time.monotonic() + limite_s
    while time.monotonic() < fin:
        try:
            fila = cliente.get(f"/api/v1/frequency/executions/{id_ejecucion}").json()
        except httpx.RemoteProtocolError:
            continue
        if fila["estado"] != "en_curso":
            return fila
        time.sleep(3)
    raise TimeoutError(f"La ejecucion {id_ejecucion} no termino en {limite_s:.0f} s")


def main():
    parser = argparse.ArgumentParser(description="Prueba de humo de la API desplegada con Docker Compose")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--origen", default="base", choices=["base", "archivos"])
    parser.add_argument("--salida", default="reportes/humo_docker.json")
    args = parser.parse_args()
    nombre = f"humo-{uuid.uuid4().hex[:8]}"
    with db.motor_destino().begin() as conn:
        clave = seguridad.crear(conn, nombre, "operador", "pruebas/humo_docker.py")
    informe = {}
    try:
        with httpx.Client(base_url=args.url, timeout=120) as publico:
            salud = publico.get("/api/v1/salud").json()
            informe["salud"] = {k: salud[k] for k in ("estado", "horizontes_min", "ultima_franja_con_datos", "fuente_datos")}
            informe["salud"]["revision_base"] = salud["base_ai_frecuencias"].get("revision")
            informe["sin_clave"] = publico.get("/api/v1/rutas").status_code
            informe["docs"] = publico.get("/docs").status_code
        with httpx.Client(base_url=args.url, timeout=120, headers={seguridad.CABECERA: clave}) as cliente:
            inicio = time.monotonic()
            lanzada = cliente.post("/api/v1/frequency/executions",
                                   json={"origen": args.origen, "dias": 1, "reentrenar": "nunca"})
            informe["lanzamiento"] = {"estado_http": lanzada.status_code, **lanzada.json()}
            informe["segundo_lanzamiento_mientras_corre"] = cliente.post(
                "/api/v1/frequency/executions", json={"origen": args.origen, "dias": 1}).status_code
            fila = esperar_ejecucion(cliente, lanzada.json()["executionId"], 900)
            detalle = fila.get("detalle") or {}
            informe["ejecucion"] = {"estado": fila["estado"], "segundos": round(time.monotonic() - inicio),
                                    "error": fila.get("error"), "ingesta": detalle.get("ingesta"),
                                    "segmentos": detalle.get("segmentos"), "operacion": detalle.get("operacion"),
                                    "monitoreo": detalle.get("monitoreo"), "solicitado_por": detalle.get("solicitado_por")}
            informe["salud_despues"] = cliente.get("/api/v1/salud").json()["ultima_franja_con_datos"]
            informe["resumen_alertas"] = cliente.get("/api/v1/alerts/summary").json()
    finally:
        with db.motor_destino().begin() as conn:
            seguridad.revocar(conn, nombre)
    Path(args.salida).parent.mkdir(parents=True, exist_ok=True)
    Path(args.salida).write_text(json.dumps(informe, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(informe, ensure_ascii=False, default=str))
    sys.exit(0 if informe["ejecucion"]["estado"] == "exitosa" else 1)


if __name__ == "__main__":
    main()
