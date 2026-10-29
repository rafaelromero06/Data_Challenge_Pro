import pandas as pd

from frecuencias.optimizacion.parametros import ParametrosHora

REGLAS_CONSOLIDADO = {"CAL-003", "CAL-007", "CAL-008"}
REGLAS_INCOMPLETOS = {"CAL-004"}
TOLERANCIA_DESPACHO = 2.0


def _hora(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M")


def _alerta(codigo: str, severidad: str, id_ruta: int | None, inicio, fin, explicacion: str, recomendacion: str,
            **extra) -> dict:
    clave = f"{codigo}:{id_ruta}:{_hora(inicio)}" if inicio is not None else f"{codigo}:{id_ruta}"
    return {"codigo": codigo, "severidad": severidad, "id_ruta": id_ruta, "ventana_inicio": inicio,
            "ventana_fin": fin, "clave_deduplicacion": extra.pop("clave", clave), "explicacion": explicacion,
            "recomendacion": recomendacion, **extra}


def oferta_minima_aceptable(recomendados: float, p: ParametrosHora) -> float:
    return max(recomendados - p.tolerancia_buses, recomendados * (1 - p.tolerancia_pct))


def por_recomendacion(rec: dict, p: ParametrosHora, variables: list | None = None) -> list[dict]:
    alertas = []
    actual = rec.get("buses_por_hora_actual")
    inicio, fin, ruta = rec["franja_inicio"], rec["franja_fin"], rec["id_ruta"]
    comunes = {"variables_influyentes": variables, "prediction_id": rec.get("prediction_id"),
               "recommendation_id": rec.get("recommendation_id"), "horizonte_min": rec.get("horizonte_min")}
    if actual:
        ocupacion_alta = rec["carga_maxima_esperada"] / (actual * p.capacidad)
        if ocupacion_alta >= p.umbral_alta_demanda:
            alertas.append(_alerta(
                "ALT-001", "preventiva" if rec["viable"] else "critica", ruta, inicio, fin,
                f"La carga maxima prevista ({rec['carga_maxima_esperada']:.0f} pasajeros por hora en el escenario alto) "
                f"supera la capacidad disponible: llenaria al {ocupacion_alta:.0%} los {actual:g} buses por hora "
                f"actuales.",
                f"Subir a {rec['buses_por_hora']} buses por hora (headway {rec['headway_min']:g} min)."
                + ("" if rec["viable"] else f" No es viable con la operacion actual: {rec['motivo_no_viable']}."),
                valor_observado=round(ocupacion_alta * 100, 1), valor_esperado=round(p.ocupacion_objetivo * 100, 1),
                umbral=round(p.umbral_alta_demanda * 100, 1), puntuacion_anomalia=round(ocupacion_alta, 3), **comunes))
        ocupacion = rec.get("ocupacion_actual")
        if ocupacion is not None and ocupacion >= p.ocupacion_critica:
            alertas.append(_alerta(
                "ALT-002", "critica", ruta, inicio, fin,
                f"Con la frecuencia actual la ocupacion esperada en el tramo mas cargado es {ocupacion:.0%}, "
                f"por encima del umbral critico de {p.ocupacion_critica:.0%}.",
                "Programar despachos adicionales y hacer seguimiento a la carga.",
                valor_observado=round(ocupacion * 100, 1), valor_esperado=round(p.ocupacion_objetivo * 100, 1),
                umbral=round(p.ocupacion_critica * 100, 1), puntuacion_anomalia=round(ocupacion, 3), **comunes))
        minimo_aceptable = oferta_minima_aceptable(rec["buses_por_hora"], p)
        if actual < rec["buses_por_hora"] and actual <= minimo_aceptable:
            alertas.append(_alerta(
                "ALT-003", "preventiva", ruta, inicio, fin,
                f"Se operan {actual:g} buses por hora y la demanda prevista pide {rec['buses_por_hora']} "
                f"para mantener la ocupacion objetivo de {p.ocupacion_objetivo:.0%}: faltan "
                f"{rec['buses_por_hora'] - actual:g}, mas que la tolerancia de {p.tolerancia_buses:g} buses o "
                f"{p.tolerancia_pct:.0%}.",
                f"Aplicar el intervalo recomendado de {rec['headway_min']:g} min.",
                valor_observado=actual, valor_esperado=rec["buses_por_hora"], umbral=round(minimo_aceptable, 2),
                **comunes))
        if ocupacion is not None and ocupacion < p.baja_utilizacion and actual > rec["buses_por_hora"]:
            alertas.append(_alerta(
                "ALT-004", "informativa", ruta, inicio, fin,
                f"Con {actual:g} buses por hora la ocupacion esperada es solo {ocupacion:.0%}, "
                f"por debajo del minimo de {p.baja_utilizacion:.0%}.",
                f"Evaluar ampliar el intervalo a {rec['headway_min']:g} min ({rec['buses_por_hora']} buses por hora).",
                valor_observado=round(ocupacion * 100, 1), valor_esperado=round(p.ocupacion_objetivo * 100, 1),
                umbral=round(p.baja_utilizacion * 100, 1), **comunes))
    return alertas


def por_observacion(obs: dict, error_pronostico: float) -> list[dict]:
    real, predicho = obs["observado"], obs["predicho"]
    if real is None or predicho is None or predicho <= 0:
        return []
    desvio = (real - predicho) / predicho
    comunes = {"valor_observado": round(real, 1), "valor_esperado": round(predicho, 1),
               "puntuacion_anomalia": round(abs(desvio), 3), "prediction_id": obs.get("prediction_id"),
               "model_registry_id": obs.get("model_registry_id"), "horizonte_min": obs.get("horizonte_min")}
    inferior, superior = obs.get("limite_inferior"), obs.get("limite_superior")
    if inferior is not None and real < inferior and -desvio >= error_pronostico:
        return [_alerta("ALT-005", "preventiva", obs["id_ruta"], obs["inicio"], obs["fin"],
                        f"Subieron {real:.0f} pasajeros y se esperaban {predicho:.0f} (minimo esperado {inferior:.0f}): "
                        f"una caida de {-desvio:.0%} frente al patron.",
                        "Validar si hubo un evento, un desvio de la ruta o una falla de captura.",
                        umbral=round(inferior, 1), **comunes)]
    if superior is not None and real > superior and desvio >= error_pronostico:
        return [_alerta("ALT-007", "preventiva", obs["id_ruta"], obs["inicio"], obs["fin"],
                        f"Subieron {real:.0f} pasajeros, por encima del maximo esperado de {superior:.0f} "
                        f"(prediccion {predicho:.0f}, desvio de {desvio:.0%}).",
                        "Revisar la frecuencia y recalibrar el escenario.",
                        umbral=round(superior, 1), **comunes)]
    return []


def por_despachos(salidas: pd.Series, id_ruta: int, headway_programado: float, desde, hasta) -> list[dict]:
    salidas = salidas.sort_values()
    salidas = salidas[(salidas >= desde) & (salidas < hasta)]
    if len(salidas) < 2:
        return []
    huecos = salidas.diff().dt.total_seconds().div(60)
    limite = TOLERANCIA_DESPACHO * headway_programado
    alertas = []
    for posicion in huecos.index[huecos > limite]:
        fin = salidas[posicion]
        inicio = fin - pd.Timedelta(minutes=float(huecos[posicion]))
        alertas.append(_alerta(
            "ALT-006", "critica", id_ruta, inicio, fin,
            f"Pasaron {huecos[posicion]:.0f} min sin despachos entre {inicio:%H:%M} y {fin:%H:%M}; "
            f"el intervalo habitual es {headway_programado:.0f} min.",
            "Verificar disponibilidad de vehiculos y la operacion en terminal.",
            valor_observado=round(float(huecos[posicion]), 1), valor_esperado=round(headway_programado, 1),
            umbral=round(limite, 1)))
    return alertas


def por_calidad(problemas: list[dict], execution_id: int | None) -> list[dict]:
    agrupados = {}
    for p in problemas:
        if p["regla"] in REGLAS_CONSOLIDADO | REGLAS_INCOMPLETOS:
            ruta = p["clave"].get("id_ruta")
            fecha = p["clave"].get("fecha") or str(p["clave"].get("ts", ""))[:10]
            agrupados.setdefault((p["regla"], ruta, fecha), []).append(p)
    alertas = []
    for (regla, ruta, fecha), lista in agrupados.items():
        inicio = pd.Timestamp(fecha) if fecha else None
        fin = inicio + pd.Timedelta(days=1) if inicio is not None else None
        if regla in REGLAS_INCOMPLETOS:
            alertas.append(_alerta(
                "ALT-009", "critica", ruta, inicio, fin,
                f"La ruta {ruta} no tiene lecturas del sensor el {fecha}: {lista[0]['detalle']}.",
                "Reprocesar el lote o restablecer la fuente; las franjas quedan como faltantes, no como cero.",
                clave=f"ALT-009:{ruta}:{fecha}", execution_id=execution_id))
        else:
            alertas.append(_alerta(
                "ALT-008", "preventiva", ruta, inicio, fin,
                f"{len(lista)} registro(s) atipico(s) en el consolidado de la ruta {ruta} el {fecha} ({regla}): "
                f"{lista[0]['detalle']}.",
                "Validar la fuente, el trigger y el periodo afectado.",
                clave=f"ALT-008:{ruta}:{fecha}:{regla}", valor_observado=len(lista), execution_id=execution_id))
    return alertas


def por_retraso(ultima_franja, ahora, periodos_tolerados: float, horas_por_periodo: float = 24) -> list[dict]:
    if ultima_franja is None:
        return []
    atraso = (pd.Timestamp(ahora) - pd.Timestamp(ultima_franja)).total_seconds() / 3600
    limite = periodos_tolerados * horas_por_periodo
    if atraso <= limite:
        return []
    return [_alerta("ALT-009", "critica", None, ultima_franja, ahora,
                    f"La ultima franja recibida es de {pd.Timestamp(ultima_franja):%Y-%m-%d %H:%M}: "
                    f"{atraso:.0f} h sin datos nuevos (tolerancia {limite:.0f} h).",
                    "Revisar la lectura incremental y la disponibilidad de la base del operador.",
                    clave=f"ALT-009:retraso:{pd.Timestamp(ahora):%Y-%m-%d}", valor_observado=round(atraso, 1),
                    umbral=round(limite, 1))]


def por_fuente_no_disponible(error: str, ahora) -> list[dict]:
    inicio = pd.Timestamp(ahora).floor("h")
    return [_alerta("ALT-009", "critica", None, inicio, inicio + pd.Timedelta(hours=1),
                    f"No se pudo leer la base del operador despues de los reintentos: {error}",
                    "Restablecer la conexion con la fuente y volver a ejecutar el ciclo; la marca de agua no avanzo.",
                    clave=f"ALT-009:fuente:{inicio:%Y-%m-%d}")]


def por_modelo(wape_movil: float | None, tolerancia: float, horizonte_min: int, model_registry_id: int | None,
               desde, hasta, franjas: int) -> list[dict]:
    if wape_movil is None or wape_movil <= tolerancia:
        return []
    return [_alerta("ALT-010", "critica", None, desde, hasta,
                    f"El error movil (WAPE) del modelo a {horizonte_min} min fue {wape_movil:.1%} en {franjas} franjas, "
                    f"por encima de la tolerancia de {tolerancia:.1%}.",
                    "Suspender la version o reentrenar un modelo candidato y compararlo con el activo.",
                    clave=f"ALT-010:h{horizonte_min}:{pd.Timestamp(hasta):%Y-%m-%d}", horizonte_min=horizonte_min,
                    valor_observado=round(wape_movil * 100, 2), umbral=round(tolerancia * 100, 2),
                    model_registry_id=model_registry_id)]
