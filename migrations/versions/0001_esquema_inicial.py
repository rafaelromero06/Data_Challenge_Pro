import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ESQUEMA = "ai_frecuencias"
AHORA = sa.text("now()")
UUID_ALEATORIO = sa.text("gen_random_uuid()")
ESTADOS_ALERTA = ("abierta", "reconocida", "asignada", "cerrada", "descartada")
CODIGOS_ALERTA = tuple(f"ALT-{n:03d}" for n in range(1, 11))
TABLAS = (
    "operational_parameter",
    "model_registry",
    "execution_log",
    "prediction",
    "recommendation",
    "alert",
    "alert_history",
)

PARAMETROS_INICIALES = [
    ("ocupacion_objetivo_min", 65, "%", "Limite inferior del rango deseado de utilizacion de capacidad"),
    ("ocupacion_objetivo_max", 85, "%", "Limite superior del rango deseado de utilizacion de capacidad"),
    ("ocupacion_critica", 90, "%", "Ocupacion igual o mayor a este valor: riesgo de sobreocupacion"),
    ("baja_utilizacion", 30, "%", "Ocupacion menor a este valor: despacho potencialmente ineficiente"),
    ("espera_maxima", 5, "min", "Tiempo maximo objetivo entre despachos"),
    ("headway_minimo", None, "min", "Separacion minima permitida entre despachos, definida por la operacion"),
    ("error_pronostico", 15, "%", "Desviacion entre demanda pronosticada y consolidada que activa alerta"),
    ("datos_atrasados", 2, "periodos de ingesta", "Tiempo sin recepcion de datos antes de alertar"),
]


def _lista(valores) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def _fk(tabla: str, **opciones) -> sa.ForeignKey:
    return sa.ForeignKey(f"{ESQUEMA}.{tabla}.id", **opciones)


def _momento(nombre: str, **opciones) -> sa.Column:
    return sa.Column(nombre, sa.DateTime(timezone=True), **opciones)


def _crear_parametros() -> None:
    parametro = op.create_table(
        "operational_parameter",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("clave", sa.Text, nullable=False),
        sa.Column("id_ruta", sa.Integer),
        sa.Column("valor", sa.Numeric),
        sa.Column("unidad", sa.Text, nullable=False),
        sa.Column("descripcion", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("vigente", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("validado", sa.Boolean, nullable=False, server_default=sa.false()),
        _momento("creado_en", nullable=False, server_default=AHORA),
        sa.Column("creado_por", sa.Text, nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_parametro_version"),
        schema=ESQUEMA,
    )
    op.create_index(
        "uq_parametro_version", "operational_parameter",
        ["clave", sa.text("COALESCE(id_ruta, -1)"), "version"], unique=True, schema=ESQUEMA,
    )
    op.create_index(
        "uq_parametro_vigente", "operational_parameter",
        ["clave", sa.text("COALESCE(id_ruta, -1)")], unique=True, schema=ESQUEMA,
        postgresql_where=sa.text("vigente"),
    )
    op.bulk_insert(parametro, [
        {"clave": clave, "valor": valor, "unidad": unidad, "descripcion": descripcion,
         "creado_por": "semilla especificacion v1.5"}
        for clave, valor, unidad, descripcion in PARAMETROS_INICIALES
    ])


def _crear_modelos_y_ejecuciones() -> None:
    op.create_table(
        "model_registry",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("model_id", sa.Text, nullable=False),
        sa.Column("version", sa.Text, nullable=False),
        sa.Column("horizonte_min", sa.SmallInteger, nullable=False),
        sa.Column("feature_version", sa.Text, nullable=False),
        sa.Column("features", postgresql.JSONB, nullable=False),
        sa.Column("hiperparametros", postgresql.JSONB),
        sa.Column("semilla", sa.Integer),
        sa.Column("datos_entrenamiento", postgresql.JSONB),
        _momento("data_cutoff"),
        _momento("corte_validacion"),
        _momento("corte_prueba"),
        sa.Column("metricas", postgresql.JSONB),
        sa.Column("artefacto", sa.Text, nullable=False),
        sa.Column("artefacto_sha256", sa.CHAR(64), nullable=False),
        sa.Column("estado", sa.Text, nullable=False, server_default="candidato"),
        _momento("entrenado_en"),
        _momento("registrado_en", nullable=False, server_default=AHORA),
        _momento("activado_en"),
        sa.Column("aprobado_por", sa.Text),
        sa.UniqueConstraint("model_id", "version", name="uq_modelo_version"),
        sa.CheckConstraint("horizonte_min > 0", name="ck_modelo_horizonte"),
        sa.CheckConstraint("estado IN ('candidato', 'activo', 'retirado', 'rechazado')", name="ck_modelo_estado"),
        sa.CheckConstraint("artefacto_sha256 ~ '^[0-9a-f]{64}$'", name="ck_modelo_sha256"),
        schema=ESQUEMA,
    )
    op.create_index(
        "uq_modelo_activo_por_horizonte", "model_registry", ["horizonte_min"], unique=True,
        schema=ESQUEMA, postgresql_where=sa.text("estado = 'activo'"),
    )

    op.create_table(
        "execution_log",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("tipo", sa.Text, nullable=False),
        sa.Column("estado", sa.Text, nullable=False, server_default="en_curso"),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=UUID_ALEATORIO),
        _momento("iniciada_en", nullable=False, server_default=AHORA),
        _momento("finalizada_en"),
        _momento("marca_agua_desde"),
        _momento("marca_agua_hasta"),
        sa.Column("registros_leidos", sa.Integer),
        sa.Column("registros_rechazados", sa.Integer),
        sa.Column("detalle", postgresql.JSONB),
        sa.Column("error", sa.Text),
        sa.CheckConstraint(
            "tipo IN ('ingesta', 'prediccion', 'recomendacion', 'alertas', 'entrenamiento', 'ciclo')",
            name="ck_ejecucion_tipo",
        ),
        sa.CheckConstraint("estado IN ('en_curso', 'exitosa', 'fallida')", name="ck_ejecucion_estado"),
        sa.CheckConstraint("finalizada_en IS NULL OR finalizada_en >= iniciada_en", name="ck_ejecucion_fechas"),
        schema=ESQUEMA,
    )
    op.create_index(
        "ix_ejecucion_tipo_estado", "execution_log", ["tipo", "estado", sa.text("iniciada_en DESC")],
        schema=ESQUEMA,
    )


def _crear_predicciones_y_recomendaciones() -> None:
    op.create_table(
        "prediction",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=UUID_ALEATORIO),
        sa.Column("execution_id", sa.BigInteger, _fk("execution_log")),
        sa.Column("model_registry_id", sa.BigInteger, _fk("model_registry"), nullable=False),
        sa.Column("id_ruta", sa.Integer, nullable=False),
        sa.Column("sentido", sa.SmallInteger),
        sa.Column("id_segmento", sa.BigInteger),
        _momento("referencia", nullable=False),
        _momento("franja_objetivo", nullable=False),
        sa.Column("horizonte_min", sa.SmallInteger, nullable=False),
        sa.Column("pasajeros_predichos", sa.Numeric(10, 2), nullable=False),
        sa.Column("limite_inferior", sa.Numeric(10, 2)),
        sa.Column("limite_superior", sa.Numeric(10, 2)),
        sa.Column("nivel_confianza", sa.Numeric(4, 3)),
        sa.Column("feature_version", sa.Text, nullable=False),
        _momento("data_cutoff"),
        sa.Column("explicacion", postgresql.JSONB),
        _momento("generada_en", nullable=False, server_default=AHORA),
        sa.Column("latencia_ms", sa.Numeric(10, 2)),
        sa.CheckConstraint("horizonte_min > 0", name="ck_prediccion_horizonte"),
        sa.CheckConstraint("franja_objetivo > referencia", name="ck_prediccion_futuro"),
        sa.CheckConstraint("pasajeros_predichos >= 0", name="ck_prediccion_no_negativa"),
        sa.CheckConstraint(
            "limite_inferior IS NULL OR limite_superior IS NULL OR limite_inferior <= limite_superior",
            name="ck_prediccion_intervalo",
        ),
        sa.CheckConstraint(
            "nivel_confianza IS NULL OR (nivel_confianza > 0 AND nivel_confianza < 1)",
            name="ck_prediccion_confianza",
        ),
        schema=ESQUEMA,
    )
    op.create_index(
        "uq_prediccion_clave", "prediction",
        ["model_registry_id", "id_ruta", sa.text("COALESCE(sentido, -1)"), sa.text("COALESCE(id_segmento, -1)"),
         "franja_objetivo", "horizonte_min"],
        unique=True, schema=ESQUEMA,
    )
    op.create_index("ix_prediccion_ruta_franja", "prediction", ["id_ruta", "franja_objetivo"], schema=ESQUEMA)

    op.create_table(
        "recommendation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=UUID_ALEATORIO),
        sa.Column("execution_id", sa.BigInteger, _fk("execution_log")),
        sa.Column("prediction_id", postgresql.UUID(as_uuid=True), _fk("prediction")),
        sa.Column("id_ruta", sa.Integer, nullable=False),
        sa.Column("sentido", sa.SmallInteger),
        _momento("franja_inicio", nullable=False),
        _momento("franja_fin", nullable=False),
        sa.Column("demanda_esperada", sa.Numeric(10, 2)),
        sa.Column("capacidad_efectiva", sa.Numeric(10, 2)),
        sa.Column("buses_por_hora", sa.Integer),
        sa.Column("headway_min", sa.Numeric(6, 2)),
        sa.Column("vehiculos_requeridos", sa.Integer),
        sa.Column("headway_actual_min", sa.Numeric(6, 2)),
        sa.Column("viable", sa.Boolean, nullable=False),
        sa.Column("motivo_no_viable", sa.Text),
        sa.Column("parametros", postgresql.JSONB),
        _momento("generada_en", nullable=False, server_default=AHORA),
        sa.CheckConstraint("franja_fin > franja_inicio", name="ck_recomendacion_franja"),
        sa.CheckConstraint("buses_por_hora IS NULL OR buses_por_hora >= 0", name="ck_recomendacion_buses"),
        sa.CheckConstraint("headway_min IS NULL OR headway_min > 0", name="ck_recomendacion_headway"),
        sa.CheckConstraint(
            "vehiculos_requeridos IS NULL OR vehiculos_requeridos >= 0", name="ck_recomendacion_vehiculos"
        ),
        sa.CheckConstraint("viable OR motivo_no_viable IS NOT NULL", name="ck_recomendacion_no_viable"),
        schema=ESQUEMA,
    )
    op.create_index("ix_recomendacion_ruta_franja", "recommendation", ["id_ruta", "franja_inicio"], schema=ESQUEMA)


def _crear_alertas() -> None:
    op.create_table(
        "alert",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=UUID_ALEATORIO),
        sa.Column("codigo", sa.Text, nullable=False),
        sa.Column("severidad", sa.Text, nullable=False),
        sa.Column("estado", sa.Text, nullable=False, server_default="abierta"),
        sa.Column("clave_deduplicacion", sa.Text, nullable=False),
        sa.Column("id_empresa", sa.Integer),
        sa.Column("id_ruta", sa.Integer),
        sa.Column("sentido", sa.SmallInteger),
        sa.Column("id_viaje", sa.BigInteger),
        sa.Column("id_vehiculo", sa.Text),
        sa.Column("conductor", sa.Text),
        _momento("detectada_en", nullable=False, server_default=AHORA),
        _momento("ventana_inicio"),
        _momento("ventana_fin"),
        sa.Column("horizonte_min", sa.SmallInteger),
        sa.Column("valor_observado", sa.Numeric),
        sa.Column("valor_esperado", sa.Numeric),
        sa.Column("umbral", sa.Numeric),
        sa.Column("puntuacion_anomalia", sa.Numeric),
        sa.Column("explicacion", sa.Text, nullable=False),
        sa.Column("variables_influyentes", postgresql.JSONB),
        sa.Column("recomendacion", sa.Text),
        sa.Column("usuario_asignado", sa.Text),
        sa.Column("execution_id", sa.BigInteger, _fk("execution_log")),
        sa.Column("prediction_id", postgresql.UUID(as_uuid=True), _fk("prediction")),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), _fk("recommendation")),
        sa.Column("model_registry_id", sa.BigInteger, _fk("model_registry")),
        _momento("actualizada_en", nullable=False, server_default=AHORA),
        sa.CheckConstraint(f"codigo IN ({_lista(CODIGOS_ALERTA)})", name="ck_alerta_codigo"),
        sa.CheckConstraint("severidad IN ('informativa', 'preventiva', 'critica')", name="ck_alerta_severidad"),
        sa.CheckConstraint(f"estado IN ({_lista(ESTADOS_ALERTA)})", name="ck_alerta_estado"),
        sa.CheckConstraint(
            "ventana_inicio IS NULL OR ventana_fin IS NULL OR ventana_fin >= ventana_inicio",
            name="ck_alerta_ventana",
        ),
        schema=ESQUEMA,
    )
    op.create_index(
        "uq_alerta_abierta", "alert", ["clave_deduplicacion"], unique=True, schema=ESQUEMA,
        postgresql_where=sa.text("estado IN ('abierta', 'reconocida', 'asignada')"),
    )
    op.create_index(
        "ix_alerta_filtros", "alert", ["estado", "severidad", "id_ruta", sa.text("detectada_en DESC")],
        schema=ESQUEMA,
    )

    op.create_table(
        "alert_history",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("alert_id", postgresql.UUID(as_uuid=True), _fk("alert", ondelete="CASCADE"), nullable=False),
        sa.Column("estado_anterior", sa.Text),
        sa.Column("estado_nuevo", sa.Text, nullable=False),
        sa.Column("usuario", sa.Text),
        sa.Column("comentario", sa.Text),
        _momento("registrado_en", nullable=False, server_default=AHORA),
        sa.CheckConstraint(f"estado_nuevo IN ({_lista(ESTADOS_ALERTA)})", name="ck_historial_estado_nuevo"),
        sa.CheckConstraint(
            f"estado_anterior IS NULL OR estado_anterior IN ({_lista(ESTADOS_ALERTA)})",
            name="ck_historial_estado_anterior",
        ),
        schema=ESQUEMA,
    )
    op.create_index("ix_historial_alerta", "alert_history", ["alert_id", "registrado_en"], schema=ESQUEMA)


def upgrade() -> None:
    _crear_parametros()
    _crear_modelos_y_ejecuciones()
    _crear_predicciones_y_recomendaciones()
    _crear_alertas()


def downgrade() -> None:
    for tabla in reversed(TABLAS):
        op.drop_table(tabla, schema=ESQUEMA)
