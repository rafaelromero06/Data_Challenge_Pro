# Validaciones pendientes con Extreme Technologies

El prototipo del reto de frecuencias funciona con supuestos que solo Extreme Technologies y SOBUSA pueden confirmar. Este documento los reúne para que cada uno se pueda aprobar o corregir. Ninguna respuesta exige cambiar la arquitectura: los parámetros se ajustan sin desplegar y las fuentes se cambian en un solo módulo.

## Mensaje para enviar

> **Asunto:** Reto de frecuencias: validaciones que necesitamos de Extreme Technologies
>
> Hola:
>
> Estamos cerrando el prototipo del reto de frecuencias. Para entregarlo con respaldo operativo necesitamos su confirmación sobre seis decisiones de datos y sobre los parámetros de operación, además de cuatro datos que aún no tenemos.
>
> Lo más urgente es confirmar si podemos seguir usando los conteos de los sensores como fuente de demanda, porque la guía propone los consolidados y los que recibimos cubren solo ocho semanas.
>
> En el documento adjunto basta responder *aprobado* o indicar el valor correcto en cada punto. ¿Podemos revisarlo en una reunión corta o prefieren responder por escrito?
>
> Gracias.

## 1. Decisiones de datos

| # | Lo que pide la guía | Lo que hace el prototipo | Por qué | Qué necesitamos |
|---|---|---|---|---|
| 1 | Usar `timbradas_consolidado` y `timbradas_segmentos_consolidado` como fuente (§8.2) | Reconstruye la demanda desde `conteo_pasajeros_destino` | `timbradas_segmentos` solo cubre del 1 de enero al 27 de febrero de 2023 y sus horas están desplazadas dos horas. Los sensores cubren 2023 a 2025 y su suma por viaje tiene correlación 0,9999 con `numeropasajeros` | Aprobar la fuente, o entregar los consolidados completos |
| 2 | Ejecutar los scripts de segmentación sin modificarlos (§6.1, §11.1) | Replica su lógica en el esquema propio | Escriben en `timbradas_segmentos` y el acceso entregado es de solo lectura. La réplica coincide en 99,7 % con la salida del operador en los viajes que ambos cubren | Aprobar la réplica, o ejecutarlos de su lado |
| 3 | Lectura incremental por `ultima_actualizacion` (RF-013, §8.3) | La marca de agua es la franja temporal de las lecturas | Los conteos crudos no tienen esa columna. Los periodos corregidos se releen con una recarga por rango | Indicar cómo saber qué periodos cambiaron |
| 4 | Predecir por sentido (§3.1, §9.1) | Predice por ruta y reparte por segmento | La guía no define una columna de sentido, y las tres rutas son circuitos cerrados: el inicio y el fin quedan a menos de 60 m | Aprobar, o definir cómo separar ida y vuelta |
| 5 | Comparar contra `frecuenciadespachos` y usar `cap_min_autorizada` y `cap_max_autorizada` (§6.1) | Mide la frecuencia con los despachos reales de `progvehiculos` y usa una capacidad parametrizada | Refleja lo que de verdad se opera | Confirmar si esos valores configurados deben prevalecer |
| 6 | Variables por zona tarifaria (§6.2) | No las usa | Solo existen en las tablas `timbradas_*_zonas`, que dependen de los consolidados | Indicar si la zona es necesaria para la operación |

## 2. Parámetros de operación

La guía pide que el área operativa valide estos valores (§5.1). Los 116 parámetros vigentes siguen sin validar. Estos son los que más influyen en la recomendación:

| Parámetro | Valor actual | Origen | ¿Aprobado? |
|---|---|---|---|
| Capacidad nominal | 50 pasajeros en las tres rutas | Mediana por viaje en 2025 | |
| Ocupación objetivo | 80 %, en un rango de 65 % a 85 % | Guía, §5.1 | |
| Ocupación crítica | 90 % | Guía, §5.1 | |
| Baja utilización | 30 % | Guía, §5.1 | |
| Espera máxima | 5 min | Guía, §5.1 | |
| Headway mínimo | 2 min | Supuesto del equipo; la guía lo deja a la operación | |
| Headway máximo | 10 min de día y 15 min de noche | Supuesto del equipo | |
| Flota disponible | SILENCIO 55, CRA 54 92, CL 72 49 vehículos | Datos de 2025 | |
| Tiempo de ciclo | SILENCIO 214–318 min, CRA 54 187–291 min, CL 72 214–324 min, según franja y tipo de día | Viajes observados | |
| Factor de carga | 0,33 a 0,48, según ruta y franja | Carga máxima a bordo sobre subidas del viaje, sensores de 2025 | |
| Error de pronóstico tolerado | 15 % | Guía, §5.1 | |
| Datos atrasados | 2 periodos de ingesta | Guía, §5.1 | |
| Pesos de la función objetivo | Costo 1,0; espera 0,1; sobreocupación 1,0; cambio de frecuencia 0,5 | Referencia del equipo | |

Cada valor se cambia sin desplegar con `PUT /api/v1/parameters/{clave}`, con rol administrador. Al aprobarlo se publica con `"validado": true` y queda versionado con el usuario que lo cambió.

## 3. Datos que aún faltan

| Dato | Para qué | Efecto mientras falte |
|---|---|---|
| Tarifa por pasajero | Expresar el recaudo en pesos, como pide la pregunta final de la guía (§20) | El sistema estima pasajeros esperados y en riesgo de no abordar, pero no pesos |
| Conjunto de prueba reservado (§19) | Evaluar el modelo con datos que el equipo nunca vio | La evaluación usa una partición cronológica propia, de mayo a noviembre de 2025 |
| Credenciales de SMTP y WhatsApp | Enviar notificaciones reales (RF-012) | Los envíos quedan simulados, como la guía permite |
| Responsables técnicos y operativos (§19) | Validar resultados y resolver dudas | Las validaciones de este documento no tienen dueño asignado |
