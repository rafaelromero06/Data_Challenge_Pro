# Guion del video de demostración (E11)

Guion para grabar el video de máximo 5 minutos. Cubre la demostración que pide la guía (§14.2): llegada de información nueva, lectura incremental, evaluación del error, modelo candidato, comparación contra el activo y decisión. Las cifras salen de la corrida de `docs/evidencias/demo_ciclo.json`.

## Antes de grabar

1. Levante el proyecto y compruebe que `http://localhost:8000/api/v1/salud` responde `"estado": "ok"`.

   ```bash
   docker compose up -d
   ```

2. Cree una clave y guárdela para la grabación:

   ```bash
   docker compose exec api python -m frecuencias.seguridad crear --nombre video --rol administrador
   ```

3. Corra el ciclo **antes** de grabar la escena 6, porque tarda unos 35 minutos. Grabe el arranque y el resultado, y corte la espera.

   ```bash
   python pruebas/demo_ciclo.py --clave <clave> --dias 3
   ```

4. Abra `http://localhost:8000/`, escriba la clave y deje el navegador al 100 % de zoom.

El ciclo escribe en su base local y avanza el último dato tres días. Para repetir la toma desde el inicio, vuelva al estado original y cree otra clave, porque el paquete no las incluye:

```bash
python pruebas/paquete_demo.py importar --archivo paquete_demo/paquete_demo.tar.gz --reemplazar
```

## Escenas

| Tiempo | En pantalla | Qué decir |
|---|---|---|
| 0:00–0:30 | Diapositiva del problema | Hoy la frecuencia se fija con la programación y la experiencia. Cuando la demanda cambia, el ajuste llega tarde. Queremos saber con anticipación cada cuánto despachar, cuántos pasajeros vienen y cuántos vehículos hacen falta. |
| 0:30–1:10 | Pestaña **Hoy**, día 18 de diciembre | Esta es la pantalla del despachador: para cada ruta dice qué hacer en la hora actual y en la siguiente, en lenguaje operativo. La decisión sigue siendo del despachador. |
| 1:10–2:00 | Pestaña **Predicción**, CRA 54, 06:00. Abra la primera tarjeta de recomendación | El modelo predice las próximas dos horas en franjas de 15 minutos, con un rango de confianza del 80 %. Con eso propone buses por hora, intervalo y vehículos. Aquí se ve por qué: la restricción que manda, y cuántos pasajeros quedarían en riesgo de no abordar con la oferta actual. |
| 2:00–2:30 | Pestaña **Mapa**, hora 07:00 | Cada tramo de 500 metros muestra la ocupación a esa hora. Esto dice dónde se llena el bus, no solo cuándo. |
| 2:30–3:10 | Pestaña **Alertas**, vista agrupada. Abra una alerta | Hay diez tipos de alerta. Se agrupan para no saturar al operador, y cada una muestra el valor real, el esperado, el umbral y qué hacer. Queda bitácora de quién la gestionó. |
| 3:10–4:20 | Terminal con `demo_ciclo.py`. Corte a la pestaña **Ejecuciones** y luego a **Modelos** | Llegan tres días nuevos. El ciclo lee solo lo nuevo: 703 franjas y 1.347 viajes, sin tocar la base del operador. Valida la calidad, predice, recomienda y mide su propio error. Luego entrena un candidato por horizonte y lo compara con el modelo activo en cinco días que ninguno usó para entrenar. Aquí los tres quedan rechazados y se conserva el modelo activo, porque un modelo solo se activa si demuestra ser mejor. La decisión queda registrada. |
| 4:20–4:45 | Diapositiva de ingeniería | Corre con Docker desde un clon limpio, con 412 pruebas y análisis de SonarQube sin hallazgos. La base del operador solo se lee. |
| 4:45–5:00 | Diapositiva de la recomendación | Proponemos un piloto de cuatro semanas en CL 72 y SILENCIO, tras validar capacidad y pesos con operación. |

## Si algo falla durante la grabación

- **La API dice *sin preparar*.** Faltan datos o modelos: importe el paquete.
- **El ciclo responde `409`.** Ya hay un ciclo en curso: espere a que termine.
- **El mapa no carga.** Necesita internet para las teselas; la tabla de tramos sigue funcionando.
