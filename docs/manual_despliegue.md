# Manual de despliegue

Pasos para levantar la solución en una máquina con Docker, configurarla, operarla y actualizarla. Está pensado para el equipo técnico de Extreme Technologies o de SOBUSA.

## 1. Requisitos

- Docker Engine 24 o superior con Docker Compose v2. En Windows, Docker Desktop.
- 4 GB de RAM libres para la base y la API; 4 GB más si se levanta SonarQube.
- Para leer datos nuevos: acceso de red a la base del operador (PostgreSQL 14) con un usuario de solo lectura. La demostración con el paquete no lo necesita.
- Para importar el paquete de demostración: Python 3.12 o superior en la máquina, sin dependencias adicionales.
- Para desarrollo local, opcional: Python 3.12 y `pip install -r requirements-dev.txt`.

## 2. Configuración

Copie la plantilla y complete los valores. Ningún secreto va en el repositorio.

```bash
cp .env.example .env
```

| Variable | Obligatoria | Uso |
|---|---|---|
| `SOBUSA_DB_HOST`, `SOBUSA_DB_PORT`, `SOBUSA_DB_NAME`, `SOBUSA_DB_USER` | Sí, para leer datos nuevos | Base del operador (solo lectura) |
| `SOBUSA_DB_PASSWORD` o `PGPASSWORD` | Sí | Clave del usuario de solo lectura; se recomienda dejarla fuera de `.env`, como variable del sistema |
| `AI_DB_NAME`, `AI_DB_USER`, `AI_DB_PASSWORD` | Sí | Dueño del esquema `ai_frecuencias`; solo lo usan las migraciones |
| `AI_DB_APP_USER`, `AI_DB_APP_PASSWORD` | Sí | Usuario restringido con el que corren la API y el programador (clave de 16 caracteres o más) |
| `AI_DB_HOST`, `AI_DB_PORT` | Para desarrollo local | `localhost` y `5433`; dentro de Docker se usa `db:5432` |
| `API_PORT` | No | Puerto publicado de la API (8000) |
| `ENTORNO`, `API_DOCS` | No | En `produccion` Swagger UI queda apagado, salvo `API_DOCS=activa` |
| `API_AUTENTICACION` | No | `activa` exige `X-API-Key` |
| `CICLO_CRON`, `CICLO_ORIGEN`, `CICLO_REENTRENAR` | No | Frecuencia del ciclo (`15 * * * *`), origen (`base`) y modo de reentrenamiento (`auto`) |
| `CICLO_INTENTOS`, `CICLO_REANUDAR` | No | Reintentos por ciclo (3) y reanudación automática de ciclos interrumpidos (`activa`) |
| `REENTRENO_CADA_DIAS`, `REENTRENO_MIN_HORAS` | No | Periodicidad del reentrenamiento (7 días) y espera mínima entre intentos (24 h) |
| `EJECUCION_LATIDO_S`, `EJECUCION_SIN_LATIDO_S` | No | Latido de los trabajos (30 s) y tiempo sin latido para declararlos interrumpidos (180 s) |
| `NOTIFICACION_*` | No | Canales, destinatarios y credenciales SMTP y WhatsApp; sin credenciales los envíos quedan simulados |
| `OTEL_TRACES_EXPORTER`, `OTEL_EXPORTER_OTLP_ENDPOINT` | No | `none`, `console` u `otlp`, y destino OTLP/HTTP (`http://jaeger:4318`) |

## 3. Primer despliegue

```bash
docker compose up -d --build
```

Docker Compose levanta tres servicios, en este orden:

1. `db`: PostgreSQL 14 con PostGIS. Los datos quedan en el volumen `datos_ai`.
2. `migraciones`: corre una vez con el usuario dueño y termina. Aplica `alembic upgrade head`, que crea el esquema `ai_frecuencias`, el rol `ai_frecuencias_app` y los parámetros semilla, y luego `python -m frecuencias.persistencia.roles`, que crea o actualiza el usuario `AI_DB_APP_USER`.
3. `api`: FastAPI en `http://localhost:8000`, conectada con el usuario restringido. Arranca solo si las migraciones terminaron bien.

Compruebe el estado:

```bash
docker compose ps
curl http://localhost:8000/api/v1/salud
```

La base empieza vacía y el repositorio no trae datos ni modelos, así que `/salud` responde `"estado": "sin_preparar"` con el motivo y las instrucciones, y el tablero muestra el mismo aviso. La API no se reinicia en bucle: los endpoints que necesitan modelos responden `503` hasta que los cargue por una de las dos opciones siguientes.

### Opción A: importar el paquete de demostración

El paquete trae una copia del esquema `ai_frecuencias`, los modelos activos y los archivos de `datos/`. No incluye claves de acceso.

```bash
python pruebas/paquete_demo.py importar --archivo paquete_demo.tar.gz
docker compose exec api python -m frecuencias.seguridad crear --nombre demo --rol administrador
```

- **Qué hace.** Copia `modelos/` y `datos/`, restaura la base, aplica las migraciones pendientes y reinicia la API.
- **Seguro.** Se niega a restaurar sobre una base con datos, porque los borraría. Para sustituirla a propósito, agregue `--reemplazar`.
- **Crear el paquete.** En un despliegue ya cargado: `python pruebas/paquete_demo.py exportar`. Queda en `paquete_demo/`, que está en `.gitignore`.
- **Confidencialidad.** Contiene datos del operador. Compártalo solo por un canal privado, nunca en el repositorio.

Para demostrar el ciclo completo con los días de `datos/` que aún no están en la base:

```bash
python pruebas/demo_ciclo.py --clave <clave de operador o administrador> --dias 3
```

### Opción B: cargar el histórico y entrenar (una sola vez)

Haga primero una copia local de la base del operador, cargue con ella el histórico y entrene los modelos. Requiere Python local con `.env` apuntando a `localhost:5433` y el usuario dueño:

```bash
python -m frecuencias.ingesta.extraccion                 # copia de solo lectura de la base del operador en datos/
python -m frecuencias.ingesta.historico                  # carga datos/ en el histórico analítico de ai_frecuencias
python -m frecuencias.inferencia.entrenamiento --trials 15   # 3 horizontes; tomó 37 min en el equipo de desarrollo
```

Sobre la carpeta `datos/`:

- **Qué contiene.** La extracción deja allí unos 26 MB: viajes, calendario de estacionalidades y novedades, rutas, un archivo de conteos por mes (`conteo_mensual/`) y la demanda agregada por franja de 15 minutos (`demanda_15min.parquet`).
- **Si se corta.** Al repetirla, la extracción solo consulta los meses que faltan.
- **No va en git.** Es una copia de datos del operador y está en `.gitignore`. Para llevarla a otra máquina, copie la carpeta o repita la extracción.
- **Entrenamiento.** Lee `datos/demanda_15min.parquet`. **Si el archivo no existe, entrena con datos sintéticos** (solo sirven para probar el flujo) y lo avisa en la consola. Verifique que la extracción terminó antes de entrenar.
- **API.** Una vez cargado el histórico, trabaja con la base `ai_frecuencias`. Usa el calendario de `datos/` y, si no está, lo lee de la base del operador al arrancar.

Los paquetes quedan en `modelos/paquetes/`, que se monta en la API, y en `model_registry`. Reinicie la API para cargarlos: `docker compose restart api`. Los modelos no se versionan en git, porque son artefactos generados con su propia huella. Para mover un despliegue a otra máquina, copie `modelos/` junto con un respaldo de la base.

### Crear claves de acceso

```bash
python -m frecuencias.seguridad crear --nombre operador-turno-1 --rol operador
python -m frecuencias.seguridad listar
python -m frecuencias.seguridad revocar --nombre operador-turno-1
```

Dentro de Docker, antepóngales `docker compose exec api`. La clave se muestra una sola vez; en la base se guarda solo su huella SHA-256. Roles:

- `lector`: consulta.
- `operador`: predicciones, ciclos, reintentos y gestión de alertas.
- `administrador`: activar modelos y publicar parámetros.

## 4. Servicios opcionales (perfiles)

| Perfil | Comando | Para qué |
|---|---|---|
| `programador` | `docker compose --profile programador up -d` | Ciclo incremental según `CICLO_CRON`, reanudación de ciclos interrumpidos y reintentos |
| `observabilidad` | `OTEL_TRACES_EXPORTER=otlp docker compose --profile observabilidad up -d` | Jaeger para ver trazas en `http://localhost:16686` |
| `calidad` | `docker compose --profile calidad up -d sonarqube` | Servidor SonarQube local en `http://localhost:9000` |

Use un solo programador por base: el índice único de ciclos en curso impide que dos ciclos corran a la vez, pero dos programadores duplicarían intentos. La API corre con un solo proceso de uvicorn, porque la recuperación al arrancar asume una instancia de API por host.

### Red para el mapa

El mapa del tablero se dibuja en el navegador del usuario, no en el servidor. El equipo de quien abre el tablero necesita salida a `https://unpkg.com` (Leaflet) y a `https://tile.openstreetmap.org` (teselas). Si una política de red lo impide, el resto del tablero funciona y el mapa muestra un aviso. Para un uso intensivo conviene un servidor de teselas propio o contratado, según la política de uso de OpenStreetMap.

## 5. Operación diaria

- **Salud:** `GET /api/v1/salud`, que muestra modelos en memoria, último dato, ciclo en curso y revisión del esquema; métricas en `GET /metrics`.
- **Ciclos:** el tablero, pestaña *Ejecuciones*, muestra etapa, avance, intento y error. Un ciclo `fallida` o `interrumpida` se reintenta con el botón o con `POST /api/v1/frequency/executions/{id}/retry`.
- **Logs:** `docker compose logs -f api programador`. Son JSON con `correlation_id` y, si hay trazas, `trace_id`.
- **Monitoreo del modelo:** tablero, pestaña *Modelos*, o `GET /api/v1/monitoring`. Si el monitoreo muestra deriva o degradación, el ciclo reentrena por su cuenta, con al menos 24 h entre intentos.
- **Activar o revertir un modelo:** pestaña *Modelos*, o `POST /api/v1/models/{id}/activate`, con rol administrador. Verifica la huella del artefacto antes de activarlo.
- **Cambiar un umbral o un peso:** `PUT /api/v1/parameters/{clave}`, con rol administrador. Crea una versión nueva, conserva la anterior y no requiere desplegar. Así se define también `tarifa_pasajero`, que activa la estimación del recaudo.
- **Recargar un periodo corregido:** el ciclo no relee periodos ya cargados. Si el operador corrige datos pasados, use `python -m frecuencias.ingesta.historico --desde AAAA-MM-DD --hasta AAAA-MM-DD --origen base`. Es idempotente y no mueve la marca de agua del ciclo.
- **Recalcular el perfil por segmento:** el ciclo lo hace cuando hay datos nuevos. Para hacerlo a mano, sin leer la base del operador: `python -m frecuencias.segmentos.carga --solo-perfil --hasta AAAA-MM-DD`.

## 6. Integración continua y SonarQube

El workflow `.github/workflows/ci.yml` corre en cada push a `main` y en cada pull request:

1. **pruebas**:
   - Levanta PostGIS 14 como servicio.
   - Aplica, revierte y reaplica las migraciones.
   - Crea el usuario de aplicación.
   - Corre `pytest -m "not lenta"` con cobertura y publica `coverage.xml` y `reporte-pruebas.xml`.
2. **calidad**:
   - Levanta `sonarqube:community` como servicio del propio job.
   - Cambia la clave inicial del administrador por una aleatoria y genera un token de análisis.
   - Analiza con `SonarSource/sonarqube-scan-action` usando la cobertura del paso anterior.
   - Ejecuta `pruebas/informe_sonar.py`, que falla si hay hallazgos críticos o bloqueantes (RNF-013).
   - Publica `reportes/sonarqube.json` como artefacto.

   No necesita servidor externo ni secretos.
3. **imagen**: construye la imagen Docker.

Para analizar en local:

```bash
docker compose --profile calidad up -d sonarqube
# cree un token en http://localhost:9000 (My Account > Security) y expórtelo como SONAR_TOKEN
python -m pytest --cov=frecuencias --cov-report=xml --junitxml=reporte-pruebas.xml
docker run --rm -e SONAR_HOST_URL=http://host.docker.internal:9000 -e SONAR_TOKEN \
  -v "$PWD:/usr/src" sonarsource/sonar-scanner-cli
python pruebas/informe_sonar.py --salida reportes/sonarqube.json
```

## 7. Actualizar a una versión nueva

```bash
git pull
docker compose up -d --build     # las migraciones nuevas se aplican solas antes de arrancar la API
```

Las migraciones son reversibles (`alembic downgrade <revision>`). Antes de actualizar en producción, respalde la base:

```bash
docker compose exec db pg_dump -U "$AI_DB_USER" -d "$AI_DB_NAME" -Fc > respaldo_ai_frecuencias.dump
```

Para restaurar: `pg_restore -c -d <base> respaldo_ai_frecuencias.dump`. Los paquetes de `modelos/` se respaldan aparte: son archivos con huella registrada en `model_registry`.

## 8. Problemas frecuentes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `migraciones` termina con error de clave | `AI_DB_APP_PASSWORD` vacía o de menos de 16 caracteres | Defínala en `.env` y vuelva a levantar |
| La API no arranca: `ArtefactoAlterado` | Un archivo de `modelos/paquetes` cambió (por ejemplo, git convirtió fines de línea) | Restaure el paquete original; `modelos/` debe quedar como binario en `.gitattributes` |
| `409` al lanzar un ciclo | Hay otro ciclo en curso en cualquier proceso | Espere o revise *Ejecuciones*; si el proceso murió, a los 180 s queda `interrumpida` y se puede reintentar |
| Alertas ALT-009 de datos atrasados | La fuente no tiene datos recientes o no responde | Revise la conectividad; el ciclo reintenta solo si la falla es transitoria |
| `/salud` responde `sin_preparar` | La API no encontró histórico o modelos activos | Siga la opción A o B de la [sección 3](#3-primer-despliegue) y reinicie la API |
| `permission denied` desde la API | Una tabla nueva no dio permisos al rol | Las migraciones nuevas heredan permisos por `ALTER DEFAULT PRIVILEGES`; si la tabla la creó otro usuario, ejecute `GRANT SELECT, INSERT, UPDATE ON ... TO ai_frecuencias_app` |
