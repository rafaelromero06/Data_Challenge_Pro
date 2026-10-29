"use strict";

const API = "/api/v1";
const ZONA = "America/Bogota";
const formatoFecha = new Intl.DateTimeFormat("es-CO", {
  timeZone: ZONA, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
});
const $ = (id) => document.getElementById(id);
const estado = { rutas: [], alertaActual: null, paginaAlertas: 1, sondeo: null };
const LLAVE_SESION = "frecuencias.clave";

function leerClave() {
  try {
    return sessionStorage.getItem(LLAVE_SESION) || "";
  } catch (error) {
    return estado.clave || "";
  }
}

function guardarClave(valor) {
  estado.clave = valor;
  try {
    sessionStorage.setItem(LLAVE_SESION, valor);
  } catch (error) {
    estado.clave = valor;
  }
}

function cabeceras(extra = {}) {
  const clave = leerClave();
  return clave ? { "X-API-Key": clave, ...extra } : { ...extra };
}

function escapar(valor) {
  return String(valor ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function fecha(valor) {
  if (!valor) return "–";
  const texto = String(valor);
  if (/(?:[zZ]|[+-]\d\d:\d\d)$/.test(texto)) return formatoFecha.format(new Date(texto)).replace(",", "");
  return texto.replace("T", " ").slice(0, 16);
}

function hora(valor) {
  return fecha(valor).slice(-5);
}

function numero(valor, decimales = 1) {
  if (valor === null || valor === undefined || Number.isNaN(Number(valor))) return "–";
  return Number(valor).toLocaleString("es-CO", { maximumFractionDigits: decimales, minimumFractionDigits: 0 });
}

function porcentaje(valor) {
  return valor === null || valor === undefined ? "–" : `${numero(Number(valor) * 100, 0)} %`;
}

function avisar(texto, error = false) {
  const aviso = $("aviso");
  aviso.textContent = texto;
  aviso.classList.toggle("error", error);
  aviso.hidden = !texto;
}

function mensajeError(respuesta, cuerpo) {
  if (respuesta.status === 401) return "Ingrese una clave de acceso válida en la parte superior.";
  if (respuesta.status === 403) return cuerpo?.error?.mensaje || "Su clave no tiene permiso para esta operación.";
  return cuerpo?.error?.mensaje || `Error ${respuesta.status}`;
}

async function api(ruta, opciones = {}) {
  const respuesta = await fetch(API + ruta, {
    ...opciones,
    headers: cabeceras({ "Content-Type": "application/json", ...(opciones.headers || {}) }),
  });
  const cuerpo = await respuesta.json().catch(() => null);
  if (!respuesta.ok) throw new Error(mensajeError(respuesta, cuerpo));
  return cuerpo;
}

async function conBoton(boton, tarea) {
  boton.disabled = true;
  avisar("");
  try {
    await tarea();
  } catch (error) {
    avisar(error.message, true);
  } finally {
    boton.disabled = false;
  }
}

function tabla(elemento, columnas, filas, alSeleccionar) {
  const encabezado = `<thead><tr>${columnas.map((c) => `<th scope="col">${escapar(c.titulo)}</th>`).join("")}</tr></thead>`;
  const cuerpo = filas.length
    ? filas.map((f, i) => `<tr data-i="${i}"${alSeleccionar ? ' tabindex="0"' : ""}>${columnas
        .map((c) => `<td class="${c.num ? "num" : ""}">${c.html ? c.valor(f) : escapar(c.valor(f))}</td>`).join("")}</tr>`).join("")
    : `<tr><td colspan="${columnas.length}">Sin registros</td></tr>`;
  elemento.innerHTML = encabezado + `<tbody>${cuerpo}</tbody>`;
  if (!alSeleccionar) return;
  elemento.querySelectorAll("tbody tr[data-i]").forEach((tr) => {
    const elegir = () => {
      elemento.querySelectorAll("tr.seleccionada").forEach((x) => x.classList.remove("seleccionada"));
      tr.classList.add("seleccionada");
      alSeleccionar(filas[Number(tr.dataset.i)]);
    };
    tr.addEventListener("click", elegir);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); elegir(); } });
  });
}

function etiqueta(severidad) {
  return `<span class="etiqueta sev-${escapar(severidad)}">${escapar(severidad)}</span>`;
}

function iniciarPestanas() {
  const pestanas = [...document.querySelectorAll('[role="tab"]')];
  const activar = (tab) => {
    pestanas.forEach((t) => {
      const elegida = t === tab;
      t.setAttribute("aria-selected", String(elegida));
      t.tabIndex = elegida ? 0 : -1;
      $(t.getAttribute("aria-controls")).hidden = !elegida;
    });
    tab.focus();
    const cargas = { "tab-alertas": cargarAlertas, "tab-ejecuciones": cargarEjecuciones, "tab-modelos": cargarModelos, "tab-parametros": cargarParametros };
    if (cargas[tab.id]) cargas[tab.id]().catch((e) => avisar(e.message, true));
  };
  pestanas.forEach((tab, i) => {
    tab.addEventListener("click", () => activar(tab));
    tab.addEventListener("keydown", (e) => {
      const paso = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (paso) activar(pestanas[(i + paso + pestanas.length) % pestanas.length]);
      if (e.key === "Home") activar(pestanas[0]);
      if (e.key === "End") activar(pestanas[pestanas.length - 1]);
    });
  });
}

async function cargarSalud() {
  try {
    const s = await api("/salud");
    $("salud-estado").textContent = s.base_ai_frecuencias.estado === "ok" ? "en línea" : `en línea (base ${s.base_ai_frecuencias.estado})`;
    $("salud-dato").textContent = fecha(s.ultima_franja_con_datos);
    $("salud-modelos").textContent = s.horizontes_min.map((h) => `${h} min`).join(" · ");
    $("salud-ciclo").textContent = s.ciclo_en_curso ? "en curso" : "inactivo";
  } catch (error) {
    $("salud-estado").textContent = "sin conexión";
  }
}

async function cargarRutas() {
  estado.rutas = await api("/rutas");
  const opciones = estado.rutas.map((r) => `<option value="${r.id_ruta}">${escapar(r.id_ruta)} · ${escapar(r.nombre || "")}</option>`).join("");
  $("pred-ruta").innerHTML = opciones;
  ["al-ruta", "ex-ruta"].forEach((id) => { $(id).innerHTML = '<option value="">todas</option>' + opciones; });
  const ultima = estado.rutas.map((r) => String(r.ultima_franja)).sort((a, b) => a.localeCompare(b)).pop();
  if (ultima) $("pred-referencia").value = `${String(ultima).slice(0, 10)}T06:00`;
}

function grafico(predicciones) {
  const ancho = 760, alto = 280, m = { izq: 44, der: 16, sup: 14, inf: 30 };
  const valores = predicciones.flatMap((p) => [p.confidenceInterval.upper, p.actualPassengers ?? 0, p.baselinePassengers ?? 0, p.predictedPassengers]);
  const maximo = Math.max(5, ...valores) * 1.1;
  const n = predicciones.length;
  const x = (i) => m.izq + (n === 1 ? 0.5 : i / (n - 1)) * (ancho - m.izq - m.der);
  const y = (v) => alto - m.inf - (v / maximo) * (alto - m.sup - m.inf);
  const linea = (fn) => predicciones.map((p, i) => [i, fn(p)]).filter(([, v]) => v !== null && v !== undefined)
    .map(([i, v], k) => `${k ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const banda = predicciones.map((p, i) => `${x(i).toFixed(1)},${y(p.confidenceInterval.upper).toFixed(1)}`)
    .concat(predicciones.map((p, i) => `${x(i).toFixed(1)},${y(p.confidenceInterval.lower).toFixed(1)}`).reverse()).join(" ");
  const marcas = [0, 0.25, 0.5, 0.75, 1].map((f) => {
    const v = maximo * f;
    return `<line x1="${m.izq}" x2="${ancho - m.der}" y1="${y(v)}" y2="${y(v)}" stroke="var(--borde)"/>` +
      `<text x="${m.izq - 6}" y="${y(v) + 4}" text-anchor="end">${numero(v, 0)}</text>`;
  }).join("");
  const etiquetas = predicciones.map((p, i) => `<text x="${x(i)}" y="${alto - 10}" text-anchor="middle">${hora(p.targetSlot)}</text>`).join("");
  const puntos = predicciones.map((p, i) => `<circle cx="${x(i)}" cy="${y(p.predictedPassengers)}" r="3.5" fill="var(--pred)"><title>${hora(p.targetSlot)}: ${numero(p.predictedPassengers)} (${numero(p.confidenceInterval.lower)}–${numero(p.confidenceInterval.upper)})</title></circle>`).join("");
  const reales = predicciones.map((p, i) => p.actualPassengers === null ? "" : `<rect x="${x(i) - 4}" y="${y(p.actualPassengers) - 4}" width="8" height="8" fill="var(--real)"><title>Observado ${hora(p.targetSlot)}: ${numero(p.actualPassengers)}</title></rect>`).join("");
  return `<svg viewBox="0 0 ${ancho} ${alto}" xmlns="http://www.w3.org/2000/svg">${marcas}
    <polygon points="${banda}" fill="var(--int)" stroke="none"/>
    <path d="${linea((p) => p.baselinePassengers)}" fill="none" stroke="var(--base)" stroke-width="1.5" stroke-dasharray="5 4"/>
    <path d="${linea((p) => p.predictedPassengers)}" fill="none" stroke="var(--pred)" stroke-width="2.2"/>
    <path d="${linea((p) => p.actualPassengers)}" fill="none" stroke="var(--real)" stroke-width="1.5"/>
    ${puntos}${reales}${etiquetas}</svg>`;
}

function tramoCritico(t) {
  if (!t) return "";
  return `<p class="nota">Tramo más cargado a esta hora: segmento ${escapar(t.segmentNumber)} (km ${numero(t.fromKm)}–${numero(t.toKm)}),
    carga p90 de ${numero(t.loadP90, 0)} pasajeros por bus (${porcentaje(t.occupancyP90)} de la capacidad) en ${escapar(t.tripsObserved)} viajes observados.</p>`;
}

function tarjetaRecomendacion(r) {
  const alertas = r.alertas.length
    ? `<ul>${r.alertas.map((a) => `<li>${etiqueta(a.severidad)} ${escapar(a.codigo)}: ${escapar(a.explicacion)}</li>`).join("")}</ul>` : "";
  return `<article class="tarjeta">
    <h3>${hora(r.franja_inicio)}–${hora(r.franja_fin)} <span class="etiqueta ${r.viable ? "viable" : "no-viable"}">${r.viable ? "viable" : "no viable"}</span></h3>
    <dl>
      <dt>Demanda esperada</dt><dd>${numero(r.demanda_esperada, 0)} (${numero(r.demanda_inferior, 0)}–${numero(r.demanda_superior, 0)})</dd>
      <dt>Carga máxima (P90)</dt><dd>${numero(r.carga_maxima_esperada, 0)}</dd>
      <dt>Buses por hora</dt><dd>${r.buses_por_hora}${r.buses_por_hora_actual !== null ? ` (actual ${numero(r.buses_por_hora_actual, 0)})` : ""}</dd>
      <dt>Headway</dt><dd>${numero(r.headway_min)} min</dd>
      <dt>Vehículos requeridos</dt><dd>${r.vehiculos_requeridos}${r.flota_disponible ? ` / ${r.flota_disponible}` : ""}</dd>
      <dt>Ocupación esperada</dt><dd>${porcentaje(r.ocupacion_esperada)}</dd>
      <dt>Ocupación con oferta actual</dt><dd>${porcentaje(r.ocupacion_actual)}</dd>
      <dt>Limitado por</dt><dd>${escapar(r.limitado_por)}</dd>
    </dl>
    ${r.motivo_no_viable ? `<p class="motivo">${escapar(r.motivo_no_viable)}</p>` : ""}${tramoCritico(r.tramo_critico)}${alertas}
  </article>`;
}

async function calcularPrediccion(evento) {
  evento.preventDefault();
  await conBoton(evento.submitter || evento.target.querySelector("button"), async () => {
    const referencia = $("pred-referencia").value;
    const r = await api("/predictions/run", {
      method: "POST",
      body: JSON.stringify({ id_ruta: Number($("pred-ruta").value), referencia: referencia ? `${referencia}:00` : null, guardar: $("pred-guardar").checked }),
    });
    const p = r.predictions;
    const total = p.reduce((s, x) => s + x.predictedPassengers, 0);
    const reales = p.filter((x) => x.actualPassengers !== null);
    const primera = r.recommendations[0];
    $("pred-kpis").innerHTML = [
      ["Ruta", `${r.routeId} ${r.routeName ? "· " + r.routeName : ""}`],
      ["Referencia", fecha(r.referenceTime)],
      ["Pasajeros próximas 2 h", numero(total, 0)],
      ["Headway recomendado", primera ? `${numero(primera.headway_min)} min` : "–"],
      ["Observado disponible", reales.length ? `${reales.length} de ${p.length} franjas` : "no"],
      ["Alertas", r.saved ? `${r.alerts.nuevas} nuevas · ${r.alerts.actualizadas} actualizadas` : `${r.alerts.detectadas} detectadas (sin guardar)`],
    ].map(([t, v]) => `<div class="kpi"><span>${escapar(t)}</span><strong>${escapar(v)}</strong></div>`).join("");
    $("pred-titulo").textContent = `Pasajeros por franja de 15 min · modelo ${p[0]?.modelVersion ?? ""}`;
    $("pred-grafico").innerHTML = grafico(p);
    $("pred-grafico").setAttribute("aria-label", `Predicción de ${p.length} franjas desde ${fecha(r.referenceTime)}`);
    $("pred-recomendaciones").innerHTML = r.recommendations.map(tarjetaRecomendacion).join("") || "<p>Sin bloques completos de una hora.</p>";
    tabla($("pred-tabla"), [
      { titulo: "Franja", valor: (x) => hora(x.targetSlot) },
      { titulo: "Antelación", valor: (x) => `${x.horizonMinutes} min`, num: true },
      { titulo: "Modelo", valor: (x) => `h${x.modelHorizonMinutes}`, num: true },
      { titulo: "Predicción", valor: (x) => numero(x.predictedPassengers), num: true },
      { titulo: "Intervalo 80 %", valor: (x) => `${numero(x.confidenceInterval.lower)}–${numero(x.confidenceInterval.upper)}`, num: true },
      { titulo: "Observado", valor: (x) => numero(x.actualPassengers), num: true },
      { titulo: "Prom. 4 sem.", valor: (x) => numero(x.baselinePassengers), num: true },
      { titulo: "Explicación", valor: (x) => x.explanation.slice(0, 3).join("; ") },
    ], p);
    $("pred-resultado").hidden = false;
    if (r.saved) avisar(`Guardado en la ejecución ${r.executionId}.`);
  });
}

async function cargarAlertas(pagina = estado.paginaAlertas) {
  estado.paginaAlertas = pagina;
  const filtros = new URLSearchParams({ page: pagina, size: 25 });
  if ($("al-estado").value) filtros.set("status", $("al-estado").value);
  if ($("al-severidad").value) filtros.set("severity", $("al-severidad").value);
  if ($("al-ruta").value) filtros.set("routeId", $("al-ruta").value);
  const r = await api(`/alerts?${filtros}`);
  tabla($("al-tabla"), [
    { titulo: "Detectada", valor: (a) => fecha(a.detectada_en) },
    { titulo: "Código", valor: (a) => a.codigo },
    { titulo: "Severidad", valor: (a) => etiqueta(a.severidad), html: true },
    { titulo: "Ruta", valor: (a) => a.id_ruta ?? "–", num: true },
    { titulo: "Ventana", valor: (a) => `${fecha(a.ventana_inicio)} → ${hora(a.ventana_fin)}` },
    { titulo: "Estado", valor: (a) => a.estado },
    { titulo: "Actualizada", valor: (a) => fecha(a.actualizada_en) },
    { titulo: "Explicación", valor: (a) => a.explicacion },
  ], r.items, mostrarAlerta);
  const paginas = Math.max(1, Math.ceil(r.total / r.size));
  $("al-paginador").innerHTML = `<button type="button" id="al-ant" ${pagina <= 1 ? "disabled" : ""}>Anterior</button>
    <span>Página ${pagina} de ${paginas} · ${r.total} alertas</span>
    <button type="button" id="al-sig" ${pagina >= paginas ? "disabled" : ""}>Siguiente</button>`;
  $("al-ant").onclick = () => cargarAlertas(pagina - 1).catch((e) => avisar(e.message, true));
  $("al-sig").onclick = () => cargarAlertas(pagina + 1).catch((e) => avisar(e.message, true));
}

async function mostrarAlerta(fila) {
  const a = await api(`/alerts/${fila.id}`);
  estado.alertaActual = a.id;
  $("al-detalle").hidden = false;
  $("al-detalle-titulo").innerHTML = `${escapar(a.codigo)} ${etiqueta(a.severidad)} · ruta ${escapar(a.id_ruta ?? "–")} · ${escapar(a.estado)}`;
  $("al-detalle-texto").textContent = `${a.explicacion}${a.recomendacion ? " Recomendación: " + a.recomendacion : ""}`;
  $("al-historial").innerHTML = a.historial.map((h) => `<li>${fecha(h.registrado_en)} · ${escapar(h.usuario)}: ${escapar(h.estado_anterior ?? "–")} → ${escapar(h.estado_nuevo)}${h.comentario ? " · " + escapar(h.comentario) : ""}</li>`).join("")
    + a.notificaciones.map((n) => `<li>${fecha(n.creada_en)} · notificación ${escapar(n.canal)} (${escapar(n.estado)})</li>`).join("");
  $("al-detalle").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function cambiarEstado(evento) {
  evento.preventDefault();
  if (!estado.alertaActual) return;
  await conBoton(evento.submitter || evento.target.querySelector("button"), async () => {
    await api(`/alerts/${estado.alertaActual}/status`, {
      method: "PATCH",
      body: JSON.stringify({
        estado: $("al-nuevo").value || null, usuario: $("al-usuario").value || null,
        comentario: $("al-comentario").value || null, usuario_asignado: $("al-asignado").value || null,
      }),
    });
    $("al-comentario").value = "";
    await cargarAlertas();
    await mostrarAlerta({ id: estado.alertaActual });
    avisar("Alerta actualizada.");
  });
}

async function cargarEjecuciones() {
  const r = await api("/frequency/executions?size=30");
  tabla($("ej-tabla"), [
    { titulo: "Id", valor: (e) => e.id, num: true },
    { titulo: "Tipo", valor: (e) => e.tipo },
    { titulo: "Estado", valor: (e) => e.estado },
    { titulo: "Inicio", valor: (e) => fecha(e.iniciada_en) },
    { titulo: "Fin", valor: (e) => fecha(e.finalizada_en) },
    { titulo: "Datos hasta", valor: (e) => fecha(e.marca_agua_hasta) },
    { titulo: "Leídos", valor: (e) => e.registros_leidos ?? "–", num: true },
    { titulo: "Rechazados", valor: (e) => e.registros_rechazados ?? "–", num: true },
    { titulo: "Error", valor: (e) => e.error ?? "" },
  ], r.items, async (e) => {
    const detalle = await api(`/frequency/executions/${e.id}`);
    $("ej-detalle").textContent = JSON.stringify(detalle, null, 2);
    $("ej-detalle").hidden = false;
  });
  const enCurso = r.items.some((e) => e.estado === "en_curso");
  clearTimeout(estado.sondeo);
  if (enCurso) estado.sondeo = setTimeout(() => cargarEjecuciones().catch(() => {}), 5000);
  void cargarSalud();
}

async function lanzarCiclo(evento) {
  evento.preventDefault();
  await conBoton(evento.submitter || evento.target.querySelector("button"), async () => {
    const r = await api("/frequency/executions", {
      method: "POST",
      body: JSON.stringify({ origen: $("ci-origen").value, dias: Number($("ci-dias").value), reentrenar: $("ci-reentrenar").value }),
    });
    avisar(`Ciclo ${r.executionId} en curso.`);
    await cargarEjecuciones();
  });
}

function wapeModelo(m) {
  const prueba = m.metricas?.prueba ?? m.metricas_prueba ?? {};
  return prueba.LightGBM?.WAPE ?? prueba.ventana_reentrenamiento?.candidato?.WAPE ?? null;
}

async function cargarModelos() {
  const modelos = await api("/models");
  tabla($("mo-tabla"), [
    { titulo: "Id", valor: (m) => m.id ?? "–", num: true },
    { titulo: "Horizonte", valor: (m) => `${m.horizonte_min} min`, num: true },
    { titulo: "Modelo", valor: (m) => `${m.model_id}@${m.version}` },
    { titulo: "Familia", valor: (m) => m.familia ?? "" },
    { titulo: "Estado", valor: (m) => `${m.estado}${m.en_memoria ? " · en uso" : ""}` },
    { titulo: "Datos hasta", valor: (m) => fecha(m.data_cutoff) },
    { titulo: "WAPE evaluación", valor: (m) => porcentaje(wapeModelo(m)), num: true },
    { titulo: "Registrado", valor: (m) => fecha(m.registrado_en) },
    { titulo: "Acción", html: true, valor: (m) => m.estado === "activo" || !m.id ? "" : `<button type="button" data-activar="${m.id}">Activar</button>` },
  ], modelos);
  $("mo-tabla").querySelectorAll("[data-activar]").forEach((b) => b.addEventListener("click", () => conBoton(b, async () => {
    const usuario = $("mo-usuario").value.trim() || null;
    await api(`/models/${b.dataset.activar}/activate`, { method: "POST", body: JSON.stringify({ usuario }) });
    avisar(`Modelo ${b.dataset.activar} activado.`);
    await cargarModelos();
    void cargarSalud();
  })));
}

async function cargarParametros() {
  const filas = await api("/parameters");
  $("pa-claves").innerHTML = [...new Set(filas.map((p) => p.clave))].map((c) => `<option value="${escapar(c)}">`).join("");
  tabla($("pa-tabla"), [
    { titulo: "Clave", valor: (p) => p.clave },
    { titulo: "Ruta", valor: (p) => p.id_ruta ?? "general" },
    { titulo: "Valor", valor: (p) => numero(p.valor, 3), num: true },
    { titulo: "Unidad", valor: (p) => p.unidad },
    { titulo: "Versión", valor: (p) => p.version, num: true },
    { titulo: "Validado", valor: (p) => (p.validado ? "sí" : "no") },
    { titulo: "Creado por", valor: (p) => p.creado_por },
    { titulo: "Descripción", valor: (p) => p.descripcion },
  ], filas, (p) => {
    $("pa-clave").value = p.clave;
    $("pa-valor").value = p.valor ?? "";
    $("pa-ruta").value = p.id_ruta ?? "";
  });
}

async function publicarParametro(evento) {
  evento.preventDefault();
  await conBoton(evento.submitter || evento.target.querySelector("button"), async () => {
    const r = await api(`/parameters/${encodeURIComponent($("pa-clave").value)}`, {
      method: "PUT",
      body: JSON.stringify({
        valor: Number($("pa-valor").value), usuario: $("pa-usuario").value || null,
        id_ruta: $("pa-ruta").value ? Number($("pa-ruta").value) : null, validado: $("pa-validado").checked,
      }),
    });
    avisar(`Parámetro ${r.clave} publicado en la versión ${r.version}.`);
    await cargarParametros();
  });
}

async function exportar(evento) {
  evento.preventDefault();
  const filtros = new URLSearchParams({ format: $("ex-formato").value });
  if ($("ex-ruta").value) filtros.set("routeId", $("ex-ruta").value);
  if ($("ex-desde").value) filtros.set("from", `${$("ex-desde").value}:00`);
  if ($("ex-hasta").value) filtros.set("to", `${$("ex-hasta").value}:00`);
  await conBoton(evento.submitter || evento.target.querySelector("button"), async () => {
    const respuesta = await fetch(`${API}/export/${$("ex-recurso").value}?${filtros}`, { headers: cabeceras() });
    if (!respuesta.ok) throw new Error(mensajeError(respuesta, await respuesta.json().catch(() => null)));
    const disposicion = respuesta.headers.get("Content-Disposition") || "";
    const nombre = (disposicion.match(/filename="([^"]+)"/) || [])[1] || "exportacion";
    const enlace = document.createElement("a");
    enlace.href = URL.createObjectURL(await respuesta.blob());
    enlace.download = nombre;
    document.body.appendChild(enlace);
    enlace.click();
    enlace.remove();
    setTimeout(() => URL.revokeObjectURL(enlace.href), 1000);
    avisar(`Descargado ${nombre}.`);
  });
}

async function usarClave(evento) {
  evento.preventDefault();
  guardarClave($("clave-api").value.trim());
  $("clave-api").value = "";
  await conBoton(evento.submitter || evento.target.querySelector("button"), async () => {
    await cargarRutas();
    $("clave-estado").textContent = "Clave aceptada.";
  });
  if (!estado.rutas.length) $("clave-estado").textContent = "";
}

document.addEventListener("DOMContentLoaded", async () => {
  iniciarPestanas();
  $("form-prediccion").addEventListener("submit", calcularPrediccion);
  $("form-alertas").addEventListener("submit", (e) => { e.preventDefault(); cargarAlertas(1).catch((x) => avisar(x.message, true)); });
  $("form-estado").addEventListener("submit", cambiarEstado);
  $("form-ciclo").addEventListener("submit", lanzarCiclo);
  $("form-parametro").addEventListener("submit", publicarParametro);
  $("form-exportar").addEventListener("submit", exportar);
  $("form-clave").addEventListener("submit", usarClave);
  void cargarSalud();
  setInterval(cargarSalud, 30000);
  try {
    await cargarRutas();
    if (leerClave()) $("clave-estado").textContent = "Clave aceptada.";
  } catch (error) {
    avisar(error.message, true);
  }
});
