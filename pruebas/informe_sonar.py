import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

METRICAS = ["ncloc", "bugs", "vulnerabilities", "code_smells", "security_hotspots", "coverage",
            "duplicated_lines_density", "reliability_rating", "security_rating", "sqale_rating",
            "software_quality_blocker_issues", "software_quality_high_issues", "software_quality_medium_issues",
            "software_quality_low_issues"]


def esperar_tarea(cliente: httpx.Client, proyecto: str, limite_s: float = 300) -> None:
    fin = time.monotonic() + limite_s
    time.sleep(5)
    while time.monotonic() < fin:
        tareas = cliente.get("/api/ce/component", params={"component": proyecto}).json()
        if not tareas.get("queue"):
            return
        time.sleep(3)
    raise TimeoutError("SonarQube no termino de procesar el analisis")


def hallazgos(cliente: httpx.Client, proyecto: str) -> list[dict]:
    resultado, pagina = [], 1
    while True:
        datos = cliente.get("/api/issues/search", params={"components": proyecto, "resolved": "false", "ps": 500,
                                                          "p": pagina}).json()
        resultado += datos["issues"]
        if pagina * 500 >= datos["paging"]["total"]:
            return resultado
        pagina += 1


def main():
    parser = argparse.ArgumentParser(description="Resume el analisis de SonarQube del proyecto")
    parser.add_argument("--url", default="http://localhost:9000")
    parser.add_argument("--proyecto", default="sobusa-frecuencias")
    parser.add_argument("--salida", default="reportes/sonarqube.json")
    args = parser.parse_args()
    token = os.environ["SONAR_TOKEN"]
    with httpx.Client(base_url=args.url, timeout=60, headers={"Authorization": f"Bearer {token}"}) as cliente:
        esperar_tarea(cliente, args.proyecto)
        puerta = cliente.get("/api/qualitygates/project_status", params={"projectKey": args.proyecto}).json()
        medidas = cliente.get("/api/measures/component", params={"component": args.proyecto,
                                                                 "metricKeys": ",".join(METRICAS)}).json()
        lista = hallazgos(cliente, args.proyecto)
        puntos = cliente.get("/api/hotspots/search", params={"projectKey": args.proyecto, "ps": 500}).json()
    impactos = Counter((i["softwareQuality"], i["severity"]) for h in lista for i in h.get("impacts", []))
    graves = [{"regla": h["rule"], "archivo": h["component"].split(":", 1)[-1], "linea": h.get("line"),
               "mensaje": h["message"], "impactos": h.get("impacts"), "severidad": h.get("severity")}
              for h in lista if h.get("severity") in ("BLOCKER", "CRITICAL")
              or any(i["severity"] in ("BLOCKER", "HIGH") for i in h.get("impacts", []))]
    informe = {
        "puerta_de_calidad": puerta["projectStatus"]["status"],
        "condiciones": [{k: c.get(k) for k in ("metricKey", "status", "actualValue", "errorThreshold", "comparator")}
                        for c in puerta["projectStatus"].get("conditions", [])],
        "medidas": {m["metric"]: m.get("value") for m in medidas["component"]["measures"]},
        "hallazgos_abiertos": len(lista),
        "por_severidad": dict(Counter(h.get("severity") for h in lista)),
        "por_impacto": {f"{q}:{s}": n for (q, s), n in sorted(impactos.items())},
        "por_regla": dict(Counter(h["rule"] for h in lista).most_common(15)),
        "criticos_o_bloqueantes": graves,
        "todos": [{"regla": h["rule"], "severidad": h.get("severity"), "archivo": h["component"].split(":", 1)[-1],
                   "linea": h.get("line"), "mensaje": h["message"]} for h in lista],
        "puntos_de_seguridad": [{"regla": p["ruleKey"], "archivo": p["component"].split(":", 1)[-1], "linea": p.get("line"),
                                 "mensaje": p["message"], "estado": p["status"]} for p in puntos.get("hotspots", [])],
    }
    Path(args.salida).parent.mkdir(parents=True, exist_ok=True)
    Path(args.salida).write_text(json.dumps(informe, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in informe.items() if k != "puntos_de_seguridad"}, ensure_ascii=False))
    print("puntos de seguridad:", len(informe["puntos_de_seguridad"]))
    sys.exit(0 if not graves else 1)


if __name__ == "__main__":
    main()
