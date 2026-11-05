import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

LIMITE_S = 3600
ESPERA_S = 10


def pedir(base: str, clave: str, ruta: str, cuerpo: dict | None = None) -> dict:
    datos = None if cuerpo is None else json.dumps(cuerpo).encode("utf-8")
    cabeceras = {"X-API-Key": clave, "Content-Type": "application/json"}
    solicitud = urllib.request.Request(f"{base}/api/v1{ruta}", data=datos, headers=cabeceras)
    try:
        with urllib.request.urlopen(solicitud, timeout=120) as respuesta:
            return json.load(respuesta)
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{ruta} respondio {error.code}: {error.read().decode('utf-8')}") from error


def esperar(base: str, clave: str, id_ejecucion: int) -> dict:
    fin = time.monotonic() + LIMITE_S
    anterior = None
    while time.monotonic() < fin:
        fila = pedir(base, clave, f"/frequency/executions/{id_ejecucion}")
        etapa = (fila.get("etapa"), fila.get("avance"))
        if etapa != anterior:
            print(f"  {fila.get('etapa')}: {fila.get('avance')} %", flush=True)
            anterior = etapa
        if fila["estado"] != "en_curso":
            return fila
        time.sleep(ESPERA_S)
    raise SystemExit(f"La ejecucion {id_ejecucion} no termino en {LIMITE_S} s")


def esperar_recarga(base: str, clave: str) -> dict:
    fin = time.monotonic() + LIMITE_S
    while time.monotonic() < fin:
        salud = pedir(base, clave, "/salud")
        if not salud["ciclo_en_curso"]:
            return salud
        time.sleep(ESPERA_S)
    raise SystemExit("La API no termino de recargar el estado")


def decisiones(detalle: dict) -> dict:
    resultado = {}
    for horizonte, d in (detalle.get("reentrenamiento") or {}).items():
        candidato, activo = d.get("candidato") or {}, d.get("activo") or {}
        resultado[horizonte] = {
            "resultado": d.get("resultado"), "motivo": d.get("motivo"), "ventana": d.get("ventana"),
            "franjas_evaluadas": d.get("franjas"), "wape_candidato": candidato.get("WAPE"),
            "wape_activo": activo.get("WAPE"), "wape_linea_base": (d.get("linea_base") or {}).get("WAPE"),
            "poisson_candidato": candidato.get("desviacion_poisson"), "poisson_activo": activo.get("desviacion_poisson"),
            "version_nueva": d.get("version_nueva"),
        }
    return resultado


def resumir(antes: dict, fila: dict, despues: dict, activos: list[dict]) -> dict:
    detalle = fila.get("detalle") or {}
    return {
        "ejecucion": fila["id"], "estado": fila["estado"], "error": fila.get("error"),
        "ultimo_dato_antes": antes["ultima_franja_con_datos"], "ultimo_dato_despues": despues["ultima_franja_con_datos"],
        "ingesta": detalle.get("ingesta"), "operacion": detalle.get("operacion"), "monitoreo": detalle.get("monitoreo"),
        "motivo_reentrenamiento": detalle.get("motivo_reentrenamiento"), "decisiones": decisiones(detalle),
        "modelos_antes": antes["modelos"], "modelos_despues": despues["modelos"],
        "activos": [{k: m.get(k) for k in ("id", "horizonte_min", "version", "aprobado_por", "data_cutoff")}
                    for m in activos],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Demuestra un ciclo completo: llegan datos nuevos, se leen, se evalua "
                                                 "el error, se entrena un candidato y se decide si se activa")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--clave", default=os.environ.get("FRECUENCIAS_CLAVE"), help="Clave con rol operador o superior")
    parser.add_argument("--origen", default="archivos", choices=["archivos", "base"])
    parser.add_argument("--dias", type=int, default=3)
    parser.add_argument("--reentrenar", default="siempre", choices=["siempre", "auto", "nunca"])
    parser.add_argument("--salida", default="reportes/demo_ciclo.json")
    args = parser.parse_args()
    if not args.clave:
        parser.error("Indique --clave o la variable FRECUENCIAS_CLAVE")
    antes = pedir(args.url, args.clave, "/salud")
    print(f"Ultimo dato conocido: {antes['ultima_franja_con_datos']}")
    lanzada = pedir(args.url, args.clave, "/frequency/executions",
                    {"origen": args.origen, "dias": args.dias, "reentrenar": args.reentrenar})
    print(f"Ciclo {lanzada['executionId']} en curso")
    fila = esperar(args.url, args.clave, lanzada["executionId"])
    despues = esperar_recarga(args.url, args.clave)
    informe = resumir(antes, fila, despues, pedir(args.url, args.clave, "/models?status=activo"))
    salida = Path(args.salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(informe, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Estado: {informe['estado']}. Ultimo dato: {informe['ultimo_dato_despues']}")
    for horizonte, decision in informe["decisiones"].items():
        print(f"  {horizonte} min: {decision['resultado']} (candidato {decision['wape_candidato']}, "
              f"activo {decision['wape_activo']}, linea base {decision['wape_linea_base']})")
    print(f"Informe en {salida}")
    return 0 if informe["estado"] == "exitosa" else 1


if __name__ == "__main__":
    sys.exit(main())
