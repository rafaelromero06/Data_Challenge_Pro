from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.types import Date, UserDefinedType

from frecuencias.config import ESQUEMA_IA

metadata = MetaData(schema=ESQUEMA_IA)
UUID_ALEATORIO = text("gen_random_uuid()")


class Geometria(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **opciones) -> str:
        return "geometry(LineString, 4326)"


def _momento(nombre: str, **opciones) -> Column:
    return Column(nombre, DateTime(timezone=True), **opciones)


def _fk(tabla: str) -> ForeignKey:
    return ForeignKey(f"{ESQUEMA_IA}.{tabla}.id")


parametro = Table(
    "operational_parameter", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("clave", Text, nullable=False),
    Column("id_ruta", Integer),
    Column("valor", Numeric),
    Column("unidad", Text, nullable=False),
    Column("descripcion", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("vigente", Boolean, nullable=False),
    Column("validado", Boolean, nullable=False),
    _momento("creado_en", nullable=False),
    Column("creado_por", Text, nullable=False),
)

modelo = Table(
    "model_registry", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("model_id", Text, nullable=False),
    Column("version", Text, nullable=False),
    Column("horizonte_min", SmallInteger, nullable=False),
    Column("feature_version", Text, nullable=False),
    Column("features", JSONB, nullable=False),
    Column("hiperparametros", JSONB),
    Column("semilla", Integer),
    Column("datos_entrenamiento", JSONB),
    _momento("data_cutoff"),
    _momento("corte_validacion"),
    _momento("corte_prueba"),
    Column("metricas", JSONB),
    Column("artefacto", Text, nullable=False),
    Column("artefacto_sha256", CHAR(64), nullable=False),
    Column("familia", Text),
    Column("estado", Text, nullable=False),
    _momento("entrenado_en"),
    _momento("registrado_en", nullable=False),
    _momento("activado_en"),
    Column("aprobado_por", Text),
)

ejecucion = Table(
    "execution_log", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("tipo", Text, nullable=False),
    Column("estado", Text, nullable=False),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    _momento("iniciada_en", nullable=False),
    _momento("finalizada_en"),
    _momento("marca_agua_desde"),
    _momento("marca_agua_hasta"),
    Column("registros_leidos", Integer),
    Column("registros_rechazados", Integer),
    Column("detalle", JSONB),
    Column("error", Text),
)

prediccion = Table(
    "prediction", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=UUID_ALEATORIO),
    Column("execution_id", BigInteger, _fk("execution_log")),
    Column("model_registry_id", BigInteger, _fk("model_registry"), nullable=False),
    Column("id_ruta", Integer, nullable=False),
    Column("sentido", SmallInteger),
    Column("id_segmento", BigInteger),
    _momento("referencia", nullable=False),
    _momento("franja_objetivo", nullable=False),
    Column("horizonte_min", SmallInteger, nullable=False),
    Column("pasajeros_predichos", Numeric(10, 2), nullable=False),
    Column("limite_inferior", Numeric(10, 2)),
    Column("limite_superior", Numeric(10, 2)),
    Column("nivel_confianza", Numeric(4, 3)),
    Column("feature_version", Text, nullable=False),
    _momento("data_cutoff"),
    Column("explicacion", JSONB),
    _momento("generada_en", nullable=False),
    Column("latencia_ms", Numeric(10, 2)),
)

recomendacion = Table(
    "recommendation", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=UUID_ALEATORIO),
    Column("execution_id", BigInteger, _fk("execution_log")),
    Column("prediction_id", UUID(as_uuid=True), _fk("prediction")),
    Column("id_ruta", Integer, nullable=False),
    Column("sentido", SmallInteger),
    _momento("franja_inicio", nullable=False),
    _momento("franja_fin", nullable=False),
    Column("demanda_esperada", Numeric(10, 2)),
    Column("capacidad_efectiva", Numeric(10, 2)),
    Column("buses_por_hora", Integer),
    Column("headway_min", Numeric(6, 2)),
    Column("vehiculos_requeridos", Integer),
    Column("headway_actual_min", Numeric(6, 2)),
    Column("viable", Boolean, nullable=False),
    Column("motivo_no_viable", Text),
    Column("parametros", JSONB),
    _momento("generada_en", nullable=False),
    Column("cuantil_demanda", Numeric(4, 3)),
    Column("carga_maxima_esperada", Numeric(10, 2)),
    Column("ocupacion_esperada", Numeric(6, 4)),
    Column("buses_por_hora_actual", Numeric(6, 2)),
    Column("ocupacion_actual", Numeric(6, 4)),
    Column("flota_disponible", Integer),
    Column("tiempo_ciclo_min", Numeric(7, 2)),
)

alerta = Table(
    "alert", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=UUID_ALEATORIO),
    Column("codigo", Text, nullable=False),
    Column("severidad", Text, nullable=False),
    Column("estado", Text, nullable=False),
    Column("clave_deduplicacion", Text, nullable=False),
    Column("id_empresa", Integer),
    Column("id_ruta", Integer),
    Column("sentido", SmallInteger),
    Column("id_viaje", BigInteger),
    Column("id_vehiculo", Text),
    Column("conductor", Text),
    _momento("detectada_en", nullable=False),
    _momento("ventana_inicio"),
    _momento("ventana_fin"),
    Column("horizonte_min", SmallInteger),
    Column("valor_observado", Numeric),
    Column("valor_esperado", Numeric),
    Column("umbral", Numeric),
    Column("puntuacion_anomalia", Numeric),
    Column("explicacion", Text, nullable=False),
    Column("variables_influyentes", JSONB),
    Column("recomendacion", Text),
    Column("usuario_asignado", Text),
    Column("execution_id", BigInteger, _fk("execution_log")),
    Column("prediction_id", UUID(as_uuid=True), _fk("prediction")),
    Column("recommendation_id", UUID(as_uuid=True), _fk("recommendation")),
    Column("model_registry_id", BigInteger, _fk("model_registry")),
    _momento("actualizada_en", nullable=False),
)

historial_alerta = Table(
    "alert_history", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("alert_id", UUID(as_uuid=True), _fk("alert"), nullable=False),
    Column("estado_anterior", Text),
    Column("estado_nuevo", Text, nullable=False),
    Column("usuario", Text),
    Column("comentario", Text),
    _momento("registrado_en", nullable=False),
)

demanda = Table(
    "demand_history", metadata,
    Column("id_ruta", Integer, primary_key=True),
    _momento("ts", primary_key=True),
    Column("subidas", Numeric(10, 2)),
    Column("bajadas", Numeric(10, 2)),
    Column("nro_viajes", Integer),
    Column("nro_vehiculos", Integer),
    Column("lecturas", Integer),
    Column("lecturas_negativas", Integer),
    Column("max_subida_lectura", Numeric(10, 2)),
    Column("valido", Boolean, nullable=False),
    Column("motivo_invalido", Text),
    Column("execution_id", BigInteger, _fk("execution_log")),
    _momento("cargado_en", nullable=False),
)

viaje = Table(
    "trip_history", metadata,
    Column("idprogramacion", BigInteger, primary_key=True, autoincrement=False),
    Column("id_ruta", Integer, nullable=False),
    Column("idvehiculo", Text),
    _momento("fechasalida", nullable=False),
    _momento("fecharegistro"),
    _momento("fechainicio"),
    _momento("fechafin"),
    Column("cancelada", Boolean, nullable=False),
    Column("finalizado", Boolean, nullable=False),
    Column("excede_plazo_salida", Boolean, nullable=False),
    Column("numeropasajeros", Numeric(10, 2)),
    Column("numerobajadas", Numeric(10, 2)),
    Column("distancia_recorrida", Numeric(10, 2)),
    Column("execution_id", BigInteger, _fk("execution_log")),
    _momento("cargado_en", nullable=False),
)

problema_calidad = Table(
    "data_quality_issue", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("execution_id", BigInteger, _fk("execution_log"), nullable=False),
    Column("fuente", Text, nullable=False),
    Column("regla", Text, nullable=False),
    Column("dimension", Text, nullable=False),
    Column("severidad", Text, nullable=False),
    Column("tratamiento", Text, nullable=False),
    Column("clave", JSONB, nullable=False),
    Column("valor", Text),
    Column("detalle", Text, nullable=False),
    _momento("detectado_en", nullable=False),
)

notificacion = Table(
    "notification", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("alert_id", UUID(as_uuid=True), _fk("alert"), nullable=False),
    Column("canal", Text, nullable=False),
    Column("destinatario", Text, nullable=False),
    Column("asunto", Text, nullable=False),
    Column("mensaje", Text, nullable=False),
    Column("estado", Text, nullable=False),
    Column("intentos", SmallInteger, nullable=False),
    Column("error", Text),
    _momento("creada_en", nullable=False),
    _momento("enviada_en"),
)

segmento = Table(
    "route_segment", metadata,
    Column("id_segmento", BigInteger, primary_key=True, autoincrement=False),
    Column("id_config", BigInteger, nullable=False),
    Column("id_ruta", Integer, nullable=False),
    Column("nro_segmento", Integer, nullable=False),
    Column("longitud_m", Numeric(12, 3), nullable=False),
    Column("inicio_m", Numeric(12, 3), nullable=False),
    _momento("cargado_en", nullable=False),
    Column("geom", Geometria, nullable=False),
)

observacion_segmento = Table(
    "segment_observation", metadata,
    Column("idprogramacion", BigInteger, primary_key=True, autoincrement=False),
    Column("id_segmento", BigInteger, ForeignKey(f"{ESQUEMA_IA}.route_segment.id_segmento"), primary_key=True),
    _momento("franja", primary_key=True),
    Column("id_ruta", Integer, nullable=False),
    Column("subidas", Integer, nullable=False),
    Column("bajadas", Integer, nullable=False),
    Column("pasajeros", BigInteger, nullable=False),
    Column("carga_media", Numeric(8, 2)),
    Column("carga_maxima", Numeric(8, 2)),
    Column("lecturas", Integer, nullable=False),
    Column("distancia_max_m", Numeric(8, 1), nullable=False),
    Column("execution_id", BigInteger, _fk("execution_log")),
    _momento("cargado_en", nullable=False),
)

perfil_segmento = Table(
    "segment_profile", metadata,
    Column("id_segmento", BigInteger, ForeignKey(f"{ESQUEMA_IA}.route_segment.id_segmento"), primary_key=True),
    Column("tipo_dia", Text, primary_key=True),
    Column("hora", SmallInteger, primary_key=True),
    Column("id_ruta", Integer, nullable=False),
    Column("nro_viajes", Integer, nullable=False),
    Column("subidas_prom", Numeric(8, 3), nullable=False),
    Column("subidas_min", Integer, nullable=False),
    Column("subidas_max", Integer, nullable=False),
    Column("bajadas_prom", Numeric(8, 3), nullable=False),
    Column("bajadas_min", Integer, nullable=False),
    Column("bajadas_max", Integer, nullable=False),
    Column("pasajeros_prom", Numeric(14, 3), nullable=False),
    Column("pasajeros_min", BigInteger, nullable=False),
    Column("pasajeros_max", BigInteger, nullable=False),
    Column("carga_prom", Numeric(8, 3)),
    Column("carga_p90", Numeric(8, 3)),
    Column("carga_max", Numeric(8, 3)),
    Column("cuota_subidas", Numeric(8, 6), nullable=False),
    Column("periodo_desde", Date, nullable=False),
    Column("periodo_hasta", Date, nullable=False),
    Column("execution_id", BigInteger, _fk("execution_log")),
    _momento("calculado_en", nullable=False),
)

clave_api = Table(
    "api_key", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("nombre", Text, nullable=False),
    Column("rol", Text, nullable=False),
    Column("huella", CHAR(64), nullable=False),
    Column("prefijo", Text, nullable=False),
    Column("activa", Boolean, nullable=False),
    Column("creada_por", Text, nullable=False),
    _momento("creada_en", nullable=False),
    _momento("revocada_en"),
    _momento("ultimo_uso"),
)

instantanea = Table(
    "feature_snapshot", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("prediction_id", UUID(as_uuid=True), _fk("prediction"), nullable=False),
    Column("execution_id", BigInteger, _fk("execution_log")),
    Column("feature_version", Text, nullable=False),
    _momento("data_cutoff", nullable=False),
    Column("variables", JSONB, nullable=False),
    _momento("creada_en", nullable=False),
)
