import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

ESQUEMA = "ai_frecuencias"
AHORA = sa.text("now()")
DIMENSIONES = ("completitud", "validez", "unicidad", "consistencia", "oportunidad", "relacional")
TABLAS_NUEVAS = ("feature_snapshot", "notification", "data_quality_issue", "trip_history", "demand_history")
COLUMNAS_RECOMENDACION = (
    ("cuantil_demanda", sa.Numeric(4, 3)),
    ("carga_maxima_esperada", sa.Numeric(10, 2)),
    ("ocupacion_esperada", sa.Numeric(6, 4)),
    ("buses_por_hora_actual", sa.Numeric(6, 2)),
    ("ocupacion_actual", sa.Numeric(6, 4)),
    ("flota_disponible", sa.Integer),
    ("tiempo_ciclo_min", sa.Numeric(7, 2)),
)


def _lista(valores) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def _fk(tabla: str, **opciones) -> sa.ForeignKey:
    return sa.ForeignKey(f"{ESQUEMA}.{tabla}.id", **opciones)


def _momento(nombre: str, **opciones) -> sa.Column:
    return sa.Column(nombre, sa.DateTime(timezone=True), **opciones)


def _crear_historicos() -> None:
    op.create_table(
        "demand_history",
        sa.Column("id_ruta", sa.Integer, primary_key=True),
        _momento("ts", primary_key=True),
        sa.Column("subidas", sa.Numeric(10, 2)),
        sa.Column("bajadas", sa.Numeric(10, 2)),
        sa.Column("nro_viajes", sa.Integer),
        sa.Column("nro_vehiculos", sa.Integer),
        sa.Column("lecturas", sa.Integer),
        sa.Column("lecturas_negativas", sa.Integer),
        sa.Column("max_subida_lectura", sa.Numeric(10, 2)),
        sa.Column("valido", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("motivo_invalido", sa.Text),
        sa.Column("execution_id", sa.BigInteger, _fk("execution_log")),
        _momento("cargado_en", nullable=False, server_default=AHORA),
        sa.CheckConstraint("subidas IS NULL OR subidas >= 0", name="ck_demanda_subidas"),
        sa.CheckConstraint("valido OR motivo_invalido IS NOT NULL", name="ck_demanda_motivo"),
        schema=ESQUEMA,
    )
    op.create_index("ix_demanda_ts", "demand_history", ["ts"], schema=ESQUEMA)

    op.create_table(
        "trip_history",
        sa.Column("idprogramacion", sa.BigInteger, primary_key=True),
        sa.Column("id_ruta", sa.Integer, nullable=False),
        sa.Column("idvehiculo", sa.Text),
        _momento("fechasalida", nullable=False),
        _momento("fecharegistro"),
        _momento("fechainicio"),
        _momento("fechafin"),
        sa.Column("cancelada", sa.Boolean, nullable=False),
        sa.Column("finalizado", sa.Boolean, nullable=False),
        sa.Column("excede_plazo_salida", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("numeropasajeros", sa.Numeric(10, 2)),
        sa.Column("numerobajadas", sa.Numeric(10, 2)),
        sa.Column("distancia_recorrida", sa.Numeric(10, 2)),
        sa.Column("execution_id", sa.BigInteger, _fk("execution_log")),
        _momento("cargado_en", nullable=False, server_default=AHORA),
        schema=ESQUEMA,
    )
    op.create_index("ix_viaje_ruta_salida", "trip_history", ["id_ruta", "fechasalida"], schema=ESQUEMA)


def _crear_calidad_y_trazabilidad() -> None:
    op.create_table(
        "data_quality_issue",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("execution_id", sa.BigInteger, _fk("execution_log", ondelete="CASCADE"), nullable=False),
        sa.Column("fuente", sa.Text, nullable=False),
        sa.Column("regla", sa.Text, nullable=False),
        sa.Column("dimension", sa.Text, nullable=False),
        sa.Column("severidad", sa.Text, nullable=False),
        sa.Column("tratamiento", sa.Text, nullable=False),
        sa.Column("clave", postgresql.JSONB, nullable=False),
        sa.Column("valor", sa.Text),
        sa.Column("detalle", sa.Text, nullable=False),
        _momento("detectado_en", nullable=False, server_default=AHORA),
        sa.CheckConstraint(f"dimension IN ({_lista(DIMENSIONES)})", name="ck_calidad_dimension"),
        sa.CheckConstraint("severidad IN ('advertencia', 'error')", name="ck_calidad_severidad"),
        sa.CheckConstraint("tratamiento IN ('cuarentena', 'excluido', 'marcado', 'reportado')",
                           name="ck_calidad_tratamiento"),
        schema=ESQUEMA,
    )
    op.create_index("ix_calidad_ejecucion", "data_quality_issue", ["execution_id", "regla"], schema=ESQUEMA)

    op.create_table(
        "notification",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("alert_id", postgresql.UUID(as_uuid=True), _fk("alert", ondelete="CASCADE"), nullable=False),
        sa.Column("canal", sa.Text, nullable=False),
        sa.Column("destinatario", sa.Text, nullable=False),
        sa.Column("asunto", sa.Text, nullable=False),
        sa.Column("mensaje", sa.Text, nullable=False),
        sa.Column("estado", sa.Text, nullable=False),
        sa.Column("intentos", sa.SmallInteger, nullable=False, server_default=sa.text("1")),
        sa.Column("error", sa.Text),
        _momento("creada_en", nullable=False, server_default=AHORA),
        _momento("enviada_en"),
        sa.CheckConstraint("canal IN ('correo', 'whatsapp')", name="ck_notificacion_canal"),
        sa.CheckConstraint("estado IN ('simulada', 'enviada', 'fallida')", name="ck_notificacion_estado"),
        schema=ESQUEMA,
    )
    op.create_index("ix_notificacion_alerta", "notification", ["alert_id"], schema=ESQUEMA)

    op.create_table(
        "feature_snapshot",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("prediction_id", postgresql.UUID(as_uuid=True), _fk("prediction", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("execution_id", sa.BigInteger, _fk("execution_log")),
        sa.Column("feature_version", sa.Text, nullable=False),
        _momento("data_cutoff", nullable=False),
        sa.Column("variables", postgresql.JSONB, nullable=False),
        _momento("creada_en", nullable=False, server_default=AHORA),
        sa.UniqueConstraint("prediction_id", name="uq_snapshot_prediccion"),
        schema=ESQUEMA,
    )


def upgrade() -> None:
    _crear_historicos()
    _crear_calidad_y_trazabilidad()
    op.add_column("model_registry", sa.Column("familia", sa.Text), schema=ESQUEMA)
    for nombre, tipo in COLUMNAS_RECOMENDACION:
        op.add_column("recommendation", sa.Column(nombre, tipo), schema=ESQUEMA)


def downgrade() -> None:
    for nombre, _ in reversed(COLUMNAS_RECOMENDACION):
        op.drop_column("recommendation", nombre, schema=ESQUEMA)
    op.drop_column("model_registry", "familia", schema=ESQUEMA)
    for tabla in TABLAS_NUEVAS:
        op.drop_table(tabla, schema=ESQUEMA)
