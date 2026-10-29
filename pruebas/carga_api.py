import argparse
import asyncio
import json
import secrets
import sys
import time
import uuid
from pathlib import Path

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frecuencias import db, seguridad

UMBRAL_P95_S = 2.0
CONSULTAS = [
    ("predicciones paginadas", "GET", "/api/v1/predictions?size=50&page={pagina}", None),
    ("predicciones por ruta", "GET", "/api/v1/predictions?routeId={ruta}&size=100", None),
    ("recomendaciones paginadas", "GET", "/api/v1/recommendations?size=50&page={pagina}", None),
    ("alertas filtradas", "GET", "/api/v1/alerts?status=abierta&routeId={ruta}&size=50", None),
    ("resumen de alertas", "GET", "/api/v1/alerts/summary", None),
    ("ejecuciones", "GET", "/api/v1/frequency/executions?size=50", None),
    ("perfil por segmento", "GET", "/api/v1/segments/profile?routeId={ruta}&dayType=habil&hour={hora}", None),
    ("prediccion bajo demanda", "POST", "/api/v1/predictions/run", {"id_ruta": "{ruta}", "guardar": False}),
]
RUTAS = (2, 16, 21)


def _formatear(valor, contexto):
    if isinstance(valor, str):
        texto = valor.format(**contexto)
        return int(texto) if texto.isdigit() else texto
    if isinstance(valor, dict):
        return {k: _formatear(v, contexto) for k, v in valor.items()}
    return valor


async def usuario(cliente: httpx.AsyncClient, fin: float, tiempos: dict, errores: dict) -> None:
    while time.perf_counter() < fin:
        nombre, metodo, ruta, cuerpo = secrets.choice(CONSULTAS)
        contexto = {"pagina": secrets.randbelow(20) + 1, "ruta": secrets.choice(RUTAS), "hora": secrets.randbelow(16) + 5}
        inicio = time.perf_counter()
        try:
            respuesta = await cliente.request(metodo, _formatear(ruta, contexto), json=_formatear(cuerpo, contexto))
            ok = respuesta.status_code < 400
        except httpx.HTTPError:
            ok = False
        tiempos.setdefault(nombre, []).append(time.perf_counter() - inicio)
        if not ok:
            errores[nombre] = errores.get(nombre, 0) + 1


async def ejecutar(url: str, clave: str, usuarios: int, duracion_s: float) -> dict:
    tiempos, errores = {}, {}
    limites = httpx.Limits(max_connections=usuarios, max_keepalive_connections=usuarios)
    async with httpx.AsyncClient(base_url=url, headers={seguridad.CABECERA: clave}, timeout=60, limits=limites) as cliente:
        await cliente.get("/api/v1/salud")
        inicio = time.perf_counter()
        fin = inicio + duracion_s
        await asyncio.gather(*(usuario(cliente, fin, tiempos, errores) for _ in range(usuarios)))
        total_s = time.perf_counter() - inicio
    resultado = {}
    for nombre, valores in sorted(tiempos.items()):
        v = np.array(valores)
        resultado[nombre] = {"peticiones": len(v), "errores": errores.get(nombre, 0),
                             "p50_ms": round(float(np.percentile(v, 50)) * 1000, 1),
                             "p95_ms": round(float(np.percentile(v, 95)) * 1000, 1),
                             "p99_ms": round(float(np.percentile(v, 99)) * 1000, 1)}
    todas = np.concatenate([np.array(v) for v in tiempos.values()])
    paginadas = [r["p95_ms"] for n, r in resultado.items() if n != "prediccion bajo demanda"]
    return {"usuarios": usuarios, "duracion_s": round(total_s, 1), "peticiones": int(todas.size),
            "peticiones_por_segundo": round(todas.size / total_s, 1), "errores": sum(errores.values()),
            "p95_global_ms": round(float(np.percentile(todas, 95)) * 1000, 1),
            "p95_maximo_consultas_paginadas_ms": max(paginadas),
            "cumple_rnf_002": max(paginadas) < UMBRAL_P95_S * 1000 and not errores, "por_consulta": resultado}


def main():
    parser = argparse.ArgumentParser(description="Prueba de carga de la API (RNF-002)")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--usuarios", type=int, default=10)
    parser.add_argument("--duracion", type=float, default=60)
    parser.add_argument("--salida", default="reportes/carga_api.json")
    args = parser.parse_args()
    nombre = f"carga-{uuid.uuid4().hex[:8]}"
    with db.motor_destino().begin() as conn:
        clave = seguridad.crear(conn, nombre, "operador", "pruebas/carga_api.py")
    try:
        informe = asyncio.run(ejecutar(args.url, clave, args.usuarios, args.duracion))
    finally:
        with db.motor_destino().begin() as conn:
            seguridad.revocar(conn, nombre)
    Path(args.salida).parent.mkdir(parents=True, exist_ok=True)
    Path(args.salida).write_text(json.dumps(informe, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(informe, ensure_ascii=False))
    sys.exit(0 if informe["cumple_rnf_002"] else 1)


if __name__ == "__main__":
    main()
