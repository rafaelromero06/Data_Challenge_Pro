# SOBUSA · Frecuencias inteligentes

Prototipo para el reto de frecuencias de SOBUSA y Extreme Technologies. Predice cuántos pasajeros subirán por ruta en franjas de 15 minutos, a 30, 60 y 120 minutos. Con eso recomienda cada cuánto despachar y cuántos vehículos se necesitan, genera alertas operativas explicables y se actualiza solo con los datos nuevos. Lee la base del operador en modo de solo lectura y guarda todo en su propio esquema `ai_frecuencias`.

**Equipo:** Roger Daza, Matteo Del Sole y Rafael Romero (Universidad del Norte).

## Resultados principales

- **Error del modelo.** En prueba (mayo a noviembre de 2025), el modelo LightGBM baja el error (WAPE) de 14,9 %, con el promedio de las 4 semanas previas, a **10,1 %, 10,5 % y 10,8 %** a 30, 60 y 120 minutos.
- **Intervalo del 80 %.** Cubre entre 81,6 % y 82,5 % de las observaciones.
- **Recomendación de frecuencia.** Minimiza costo de operación, espera, sobreocupación y cambio de frecuencia (§10.2), con restricciones de servicio, capacidad, headway y flota. Marca como *no viable* lo que no se puede cumplir.
- **Diez tipos de alerta (ALT-001 a ALT-010).** Tienen umbrales versionados en PostgreSQL, deduplicación, bitácora y notificación por correo o WhatsApp. En el tablero se agrupan por ruta, código y día (775 pendientes caben en 178 grupos) y se gestionan en lote.
- **Mapa de Barranquilla.** Muestra el recorrido de las rutas sobre OpenStreetMap, con cada tramo de 500 m coloreado por la ocupación p90 a la hora y tipo de día elegidos y el tramo más cargado de cada ruta marcado.
- **Pantalla «Hoy».** Muestra, para las tres rutas a la vez, qué hacer en la hora actual y la siguiente en lenguaje operativo («Agregar 2 buses entre 06:15 y 07:15»), la franja del día y las alertas pendientes. Las horas después de la última salida programada aparecen como fuera de servicio y no piden buses.
- **Ciclo incremental.** Se ejecuta como trabajo con avance, latido, reanudación tras reinicios y reintentos. Monitorea deriva, error y cobertura, y reentrena con comparación campeón/retador.
- **Calidad del software.**
  - 412 pruebas, con 80 % de cobertura sobre la base cargada.
  - SonarQube sin bugs, vulnerabilidades ni code smells.
  - p95 de las consultas paginadas de 146 ms con 10 usuarios concurrentes.
  - Ingesta de 808.345 registros en 149 s, sin duplicados al repetir.

## Inicio rápido

```bash
cp .env.example .env          # complete las credenciales; nunca las suba al repositorio
docker compose up -d --build  # base, migraciones y usuario restringido, API
curl http://localhost:8000/api/v1/salud
```

Un clon limpio no trae datos ni modelos: los primeros son del operador y los segundos se generan con ellos. La API arranca igual, `/salud` responde `"estado": "sin_preparar"` y explica qué falta. Para dejarla operando hay dos caminos:

- **Con el paquete de demostración**, que el equipo entrega por un canal privado y no requiere acceso a la base del operador:

  ```bash
  python pruebas/paquete_demo.py importar --archivo paquete_demo.tar.gz
  docker compose exec api python -m frecuencias.seguridad crear --nombre demo --rol administrador
  ```

- **Con acceso a la base del operador:** extraer, cargar y entrenar según el [manual de despliegue](docs/manual_despliegue.md#3-primer-despliegue).

Con la API operando:

- **Tablero:** `http://localhost:8000/`
- **Documentación interactiva de la API:** `http://localhost:8000/docs`, fuera de producción.
- **Demostración del ciclo completo:** `python pruebas/demo_ciclo.py --clave <clave>`, ver el [guion](docs/guion_demo.md).

Servicios opcionales:

```bash
docker compose --profile programador up -d      # ciclo programado con reintentos
docker compose --profile observabilidad up -d   # Jaeger para trazas OpenTelemetry
docker compose --profile calidad up -d          # servidor SonarQube local
```

## Desarrollo local

```bash
conda create -n frecuencias python=3.12 && conda activate frecuencias
pip install -r requirements-dev.txt
docker compose up -d db && python -m alembic upgrade head
python -m pytest                                  # 412 pruebas, con la base cargada; -m "not lenta" para las rápidas
uvicorn frecuencias.api.main:app --reload
python -m frecuencias.operacion.ciclo --origen base --dias 1
```

## Estructura

```
frecuencias/
  ingesta/         lectura incremental de la base del operador (solo lectura), calidad, carga idempotente
  analitica/       serie de 15 min, variables, reglas de calidad, monitoreo (PSI, cobertura, volumen)
  inferencia/      entrenamiento LightGBM, cuantiles CQR, artefactos con huella, predicción explicable
  optimizacion/    parámetros, oferta actual, función objetivo y línea base
  alertas/         reglas ALT-001 a ALT-010 y notificaciones
  operacion/       ciclo, trabajos (latido, recuperación, reintentos), programador, campeón/retador
  segmentos/       carga por segmento con PostGIS
  persistencia/    tablas y repositorios de ai_frecuencias, rol de base de datos
  api/             FastAPI /api/v1, esquemas, errores, autenticación
  tablero/         tablero web
migrations/        Alembic (0001 a 0011)
tests/             pruebas pytest
pruebas/           carga, volumen, EXPLAIN, humo en Docker, E2E, SonarQube, paquete y demo del ciclo, sesgo, redistribución
docs/              entregables y manuales
paper/             artículo técnico del modelo
```

## Documentación (entregables)

| Código | Documento |
|---|---|
| E01 | [Documento ejecutivo](docs/E01_documento_ejecutivo.pdf) y [recálculo de la redistribución](docs/evidencias/redistribucion.json) |
| E02 | [Especificación técnica](docs/E02_especificacion_tecnica.pdf) |
| E03 | Este repositorio, [`requirements.txt`](requirements.txt), [pruebas](tests/), [reporte de ejecución](docs/evidencias/reporte-pruebas.xml) y [reporte de SonarQube](docs/evidencias/sonarqube.json) |
| E04 | [Migraciones Alembic](migrations/versions/), con índices y datos semilla |
| E05 | [Reporte de entrenamiento](modelos/reporte_entrenamiento.json), [modelos activos](docs/evidencias/modelos_activos.json), [sesgo por segmento](docs/evidencias/sesgo_segmentos.json) y [artículo técnico](paper/main.pdf) |
| E06 | Prototipo: [API](frecuencias/api/main.py) y [tablero](frecuencias/tablero/index.html), con predicción, recomendación y alertas |
| E07 | [Mapa de tablas, consultas de solo lectura, marca de agua y rendimiento](docs/E07_mapa_tablas.md) |
| E08 | [Evidencias de pruebas](docs/evidencias/): unitarias, integración, contrato, ML, carga, volumen, EXPLAIN, humo, E2E, clon limpio y ciclo completo |
| E09 | [`Dockerfile`](Dockerfile), [`docker-compose.yml`](docker-compose.yml) y [`.env.example`](.env.example) |
| E10 | Este README, [OpenAPI](docs/openapi.json), [manual técnico](docs/manual_tecnico.md), [manual de usuario](docs/manual_usuario.md) y [manual de despliegue](docs/manual_despliegue.md) |
| E11 | Demostración: [guion del video](docs/guion_demo.md) y presentación final |
| E12 | [Plan de evolución: integración, escalabilidad, costos y riesgos](docs/E12_plan_evolucion.pdf) |

El código no lleva comentarios ni docstrings, por convención del equipo. La explicación de cada archivo y de cada cambio está en [`comentarios_codigo.pdf`](comentarios_codigo.pdf).

## Seguridad y datos

- Credenciales solo por variables de entorno. `.env` está en `.gitignore`.
- La base del operador se lee con transacciones de solo lectura y timeout. No se modifican tablas, triggers, funciones ni scripts del operador.
- La aplicación escribe con un usuario sin DDL y sin permiso de borrar historia; solo puede reemplazar el perfil derivado por segmento (ver [E07](docs/E07_mapa_tablas.md#1-dos-bases-dos-usuarios-dos-permisos)).
- Las claves de acceso a la API se guardan como huella SHA-256 y tienen roles (lector, operador, administrador).
