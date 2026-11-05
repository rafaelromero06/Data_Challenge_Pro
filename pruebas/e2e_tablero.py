import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import expect, sync_playwright

from frecuencias import db, seguridad

CONTRASTE_MINIMO = 4.5
PARES_CONTRASTE = [("body", "body"), (".sub", "body"), ("th", ".tabla-envoltura"), ("button.primario", "button.primario"),
                   (".pestanas button[aria-selected='true']", ".pestanas"), (".kpi span", ".kpi")]


def luminancia(rgb: list[float]) -> float:
    canales = [c / 255 for c in rgb[:3]]
    lineales = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canales]
    return 0.2126 * lineales[0] + 0.7152 * lineales[1] + 0.0722 * lineales[2]


def contraste(a: list[float], b: list[float]) -> float:
    claro, oscuro = sorted((luminancia(a), luminancia(b)), reverse=True)
    return (claro + 0.05) / (oscuro + 0.05)


COLORES = """([texto, fondo]) => {
  const rgb = (v) => v.match(/[\\d.]+/g).map(Number);
  const color = getComputedStyle(document.querySelector(texto)).color;
  let nodo = document.querySelector(fondo), fondoColor = getComputedStyle(nodo).backgroundColor;
  while (rgb(fondoColor).length === 4 && rgb(fondoColor)[3] === 0 && nodo.parentElement) {
    nodo = nodo.parentElement; fondoColor = getComputedStyle(nodo).backgroundColor;
  }
  return [rgb(color), rgb(fondoColor)];
}"""


def medir_contrastes(pagina) -> dict:
    resultado = {}
    for texto, fondo in PARES_CONTRASTE:
        if pagina.locator(texto).count() and pagina.locator(fondo).count():
            color, fondo_color = pagina.evaluate(COLORES, [texto, fondo])
            resultado[texto] = round(contraste(color, fondo_color), 2)
    return resultado


def _pasos(url: str, clave: str, salida: Path, ruta: int, informe: dict) -> None:
    with sync_playwright() as p:
        navegador = p.chromium.launch(channel="msedge", headless=True)
        contexto = navegador.new_context(viewport={"width": 1366, "height": 900}, accept_downloads=True)
        pagina = contexto.new_page()
        pagina.on("console", lambda m: m.type == "error" and informe["errores_consola"].append(m.text))
        pagina.on("response", lambda r: r.status >= 400 and r.status != 401
                  and informe.setdefault("respuestas_fallidas", []).append(f"{r.status} {r.url}"))
        pagina.goto(f"{url}/tablero/")
        expect(pagina.locator("#salud-estado")).to_contain_text("en línea", timeout=15000)
        expect(pagina.locator("#aviso")).to_contain_text("clave de acceso")
        informe["errores_consola"] = [e for e in informe["errores_consola"] if "401" not in e]
        informe["pasos"]["sin_clave_pide_clave"] = True

        pagina.fill("#clave-api", clave)
        pagina.click("#form-clave button")
        expect(pagina.locator("#clave-estado")).to_have_text("Clave aceptada.", timeout=15000)
        expect(pagina.locator("#hoy-rutas .ruta-hoy").first).to_be_visible(timeout=30000)
        informe["pasos"]["hoy"] = {
            "rutas": pagina.locator("#hoy-rutas .ruta-hoy").count(),
            "horas": pagina.locator("#hoy-rutas .franja-horas li.h").count(),
            "acciones": pagina.locator("#hoy-rutas .accion").count(),
            "resumen": pagina.locator("#hoy-resumen").inner_text(),
        }
        pagina.screenshot(path=str(salida / "0-hoy.png"), full_page=True)
        pagina.locator("#hoy-rutas [data-mapa]").nth(1).click()
        expect(pagina.locator("#tab-mapa")).to_have_attribute("aria-selected", "true")
        expect(pagina.locator("#mapa img.leaflet-tile-loaded").first).to_be_visible(timeout=30000)
        expect(pagina.locator("#mapa path.leaflet-interactive").nth(100)).to_be_attached(timeout=30000)
        pagina.wait_for_timeout(1500)
        informe["pasos"]["mapa_ruta"] = {
            "ruta": pagina.locator("#mapa-ruta").input_value(), "tipo_dia": pagina.locator("#mapa-tipo").input_value(),
            "hora": pagina.locator("#mapa-hora").input_value(),
            "tramos": pagina.locator("#mapa path.leaflet-interactive").count(),
            "teselas": pagina.locator("#mapa img.leaflet-tile-loaded").count(),
            "filas_tabla": pagina.locator("#mapa-tabla tbody tr[data-i]").count(),
        }
        pagina.screenshot(path=str(salida / "6b-mapa-ruta.png"), full_page=True)
        pagina.select_option("#mapa-ruta", "")
        pagina.click("#form-mapa button[type=submit]")
        expect(pagina.locator("#mapa path.leaflet-interactive").nth(300)).to_be_attached(timeout=30000)
        pagina.wait_for_timeout(1500)
        informe["pasos"]["mapa_todas"] = {"tramos": pagina.locator("#mapa path.leaflet-interactive").count(),
                                          "leyenda": pagina.locator("#mapa-leyenda li").count()}
        pagina.screenshot(path=str(salida / "6-mapa.png"), full_page=True)
        pagina.locator("#mapa-tabla tbody tr[data-i]").first.focus()
        pagina.keyboard.press("Enter")
        pagina.click("#tab-hoy")
        expect(pagina.locator("#hoy-rutas .ruta-hoy").first).to_be_visible(timeout=30000)
        pagina.locator("#hoy-rutas [data-alertas]").first.click()
        expect(pagina.locator("#tab-alertas")).to_have_attribute("aria-selected", "true")
        expect(pagina.locator("#al-grupos tbody tr[data-i]").first).to_be_visible(timeout=15000)
        informe["pasos"]["hoy_a_alertas_de_la_ruta"] = pagina.locator("#al-ruta").input_value()
        pagina.click("#tab-prediccion")
        pagina.select_option("#pred-ruta", str(ruta))
        pagina.uncheck("#pred-guardar")
        pagina.click("#form-prediccion button[type=submit]")
        expect(pagina.locator("#pred-resultado")).to_be_visible(timeout=60000)
        informe["pasos"]["prediccion"] = {
            "puntos_grafico": pagina.locator("#pred-grafico circle").count(),
            "filas_tabla": pagina.locator("#pred-tabla tbody tr").count(),
            "tarjetas_recomendacion": pagina.locator("#pred-recomendaciones .tarjeta").count(),
            "tramo_critico": pagina.locator("#pred-recomendaciones .nota").count(),
            "funcion_objetivo": pagina.locator("#pred-recomendaciones .tarjeta",
                                               has_text="Costo ponderado (función objetivo)").count(),
            "recaudo": pagina.locator("#pred-recomendaciones .tarjeta", has_text="Pasajeros en riesgo de no abordar").count(),
        }
        pagina.screenshot(path=str(salida / "1-prediccion.png"), full_page=True)

        pestanas = pagina.locator('[role="tab"]')
        pestanas.first.focus()
        pagina.keyboard.press("ArrowRight")
        expect(pagina.locator("#tab-mapa")).to_have_attribute("aria-selected", "true")
        expect(pagina.locator("#panel-mapa")).to_be_visible()
        pagina.keyboard.press("End")
        expect(pagina.locator("#tab-exportar")).to_have_attribute("aria-selected", "true")
        pagina.keyboard.press("Home")
        expect(pagina.locator("#tab-hoy")).to_have_attribute("aria-selected", "true")
        informe["pasos"]["teclado_pestanas"] = True

        pagina.click("#tab-alertas")
        pagina.select_option("#al-ruta", "")
        with pagina.expect_response(lambda r: "/alerts/groups" in r.url and "routeId" not in r.url, timeout=30000):
            pagina.click("#form-alertas button[type=submit]")
        expect(pagina.locator("#al-grupos tbody tr[data-i]").first).to_be_visible(timeout=15000)
        pagina.wait_for_timeout(300)
        pagina.locator("#al-grupos input[data-grupo]").first.check()
        expect(pagina.locator("#al-seleccion")).to_contain_text("seleccionadas")
        expect(pagina.locator("#al-lote-aplicar")).to_be_enabled()
        informe["pasos"]["alertas_agrupadas"] = {
            "grupos_en_pagina": pagina.locator("#al-grupos tbody tr[data-i]").count(),
            "seleccion": pagina.locator("#al-seleccion").inner_text(),
        }
        pagina.screenshot(path=str(salida / "2b-alertas-agrupadas.png"), full_page=True)
        pagina.locator("#al-grupos input[data-grupo]").first.uncheck()
        pagina.locator("#al-grupos tbody tr[data-i]").first.focus()
        pagina.keyboard.press("Enter")
        expect(pagina.locator("#al-filtro-grupo")).to_be_visible(timeout=15000)
        expect(pagina.locator("#al-tabla tbody tr[data-i]").first).to_be_visible(timeout=15000)
        pagina.locator("#al-tabla tbody tr").first.focus()
        pagina.keyboard.press("Enter")
        expect(pagina.locator("#al-detalle")).to_be_visible(timeout=15000)
        informe["pasos"]["alertas"] = {"filas": pagina.locator("#al-tabla tbody tr").count(),
                                       "historial": pagina.locator("#al-historial li").count()}
        pagina.screenshot(path=str(salida / "2-alertas.png"), full_page=True)

        for pestana, tabla in (("ejecuciones", "#ej-tabla"), ("modelos", "#mo-tabla"), ("parametros", "#pa-tabla")):
            pagina.click(f"#tab-{pestana}")
            expect(pagina.locator(f"{tabla} tbody")).not_to_contain_text("Sin datos", timeout=15000)
            informe["pasos"][pestana] = pagina.locator(f"{tabla} tbody tr").count()
        informe["pasos"]["modelos_en_uso"] = pagina.locator("#mo-tabla tbody tr", has_text="en uso").count()
        expect(pagina.locator("#mon-tabla tbody")).not_to_contain_text("Sin datos", timeout=15000)
        informe["pasos"]["monitoreo"] = pagina.locator("#mon-tabla tbody tr").count()
        pagina.click("#tab-ejecuciones")
        expect(pagina.locator("#ej-tabla tbody")).not_to_contain_text("Sin datos", timeout=15000)
        informe["pasos"]["botones_reintento"] = pagina.locator("#ej-tabla [data-reintentar]").count()
        pagina.click("#tab-modelos")
        expect(pagina.locator("#mon-tabla tbody")).not_to_contain_text("Sin datos", timeout=15000)
        pagina.screenshot(path=str(salida / "3-modelos.png"), full_page=True)

        pagina.click("#tab-exportar")
        pagina.select_option("#ex-recurso", "alerts")
        with pagina.expect_download(timeout=30000) as descarga:
            pagina.click("#form-exportar button[type=submit]")
        archivo = salida / descarga.value.suggested_filename
        descarga.value.save_as(str(archivo))
        informe["pasos"]["exportacion"] = {"archivo": archivo.name, "bytes": archivo.stat().st_size}

        informe["contraste_claro"] = medir_contrastes(pagina)
        oscuro = navegador.new_context(viewport={"width": 1366, "height": 900}, color_scheme="dark")
        pagina_oscura = oscuro.new_page()
        pagina_oscura.goto(f"{url}/tablero/")
        expect(pagina_oscura.locator("#salud-estado")).to_contain_text("en línea", timeout=15000)
        informe["contraste_oscuro"] = medir_contrastes(pagina_oscura)
        pagina_oscura.screenshot(path=str(salida / "4-oscuro.png"))

        movil = navegador.new_context(viewport={"width": 375, "height": 812}, is_mobile=True, has_touch=True)
        pagina_movil = movil.new_page()
        pagina_movil.goto(f"{url}/tablero/")
        expect(pagina_movil.locator("#salud-estado")).to_contain_text("en línea", timeout=15000)
        informe["movil_sin_desborde"] = pagina_movil.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth")
        pagina_movil.screenshot(path=str(salida / "5-movil.png"), full_page=True)
        informe["completado"] = True


def recorrer(url: str, clave: str, salida: Path, ruta: int) -> dict:
    informe = {"errores_consola": [], "pasos": {}}
    try:
        _pasos(url, clave, salida, ruta, informe)
    except Exception as error:
        if not informe.get("completado"):
            raise
        informe["cierre_navegador"] = str(error).splitlines()[0]
    minimo = min(list(informe["contraste_claro"].values()) + list(informe["contraste_oscuro"].values()))
    informe["contraste_minimo"] = minimo
    informe["aprobado"] = (not informe["errores_consola"] and minimo >= CONTRASTE_MINIMO and informe["movil_sin_desborde"]
                           and informe["pasos"]["prediccion"]["filas_tabla"] == 8)
    return informe


def main():
    parser = argparse.ArgumentParser(description="Prueba del tablero en Microsoft Edge (Playwright)")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--salida", default="reportes/e2e")
    parser.add_argument("--ruta", type=int, default=16)
    args = parser.parse_args()
    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    nombre = f"e2e-{uuid.uuid4().hex[:8]}"
    with db.motor_destino().begin() as conn:
        clave = seguridad.crear(conn, nombre, "operador", "pruebas/e2e_tablero.py")
    try:
        informe = recorrer(args.url, clave, salida, args.ruta)
    finally:
        with db.motor_destino().begin() as conn:
            seguridad.revocar(conn, nombre)
    (salida / "informe.json").write_text(json.dumps(informe, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(informe, ensure_ascii=False))
    sys.exit(0 if informe["aprobado"] else 1)


if __name__ == "__main__":
    main()
