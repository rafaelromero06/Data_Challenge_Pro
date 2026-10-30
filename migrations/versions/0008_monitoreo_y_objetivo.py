import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

ESQUEMA = "ai_frecuencias"
CREADOR = "monitoreo y funcion objetivo oct 2026"
METRICAS = ("deriva_psi", "error_movil_wape", "cobertura_intervalo", "volumen_predicciones", "alertas_descartadas")
PARAMETROS = [
    ("peso_costo_operativo", 1.0, "por vehiculo-hora",
     "Peso alfa de la funcion objetivo: costo de operar un vehiculo durante una hora (unidad de referencia)"),
    ("peso_tiempo_espera", 0.1, "por pasajero-hora",
     "Peso beta de la funcion objetivo: valor de una hora de espera de un pasajero frente a una hora-vehiculo"),
    ("peso_sobreocupacion", 1.0, "por pasajero sobre la capacidad objetivo",
     "Peso gamma de la funcion objetivo: penalizacion por cada pasajero esperado por encima de la ocupacion "
     "objetivo en el tramo mas cargado"),
    ("peso_desviacion_frecuencia", 0.5, "por bus/h de cambio",
     "Peso delta de la funcion objetivo: costo de cambiar la frecuencia frente a la que se opera hoy"),
    ("despachos_fallidos_min", 2.0, "despachos",
     "Despachos programados en una hora que salieron cancelados, sin iniciar o fuera de plazo a partir de los "
     "cuales se genera ALT-006"),
    ("despachos_fallidos_pct", 25.0, "%",
     "Porcentaje de los despachos programados de la hora que no salieron dentro de tolerancia a partir del cual "
     "se genera ALT-006"),
    ("deriva_psi_max", 0.25, "PSI",
     "Indice de estabilidad poblacional de las subidas frente a la ventana de entrenamiento que se considera "
     "deriva de datos y dispara el reentrenamiento"),
    ("cobertura_intervalo_min", 65.0, "%",
     "Porcentaje minimo de franjas observadas dentro del intervalo p10-p90 (nominal 80 %)"),
    ("volumen_predicciones_min", 50.0, "% del habitual",
     "Volumen diario de predicciones, frente a la mediana de los dias previos, por debajo del cual se avisa"),
    ("alertas_descartadas_max", 30.0, "%",
     "Porcentaje de alertas gestionadas que terminan descartadas por encima del cual se revisa la regla"),
]


def _lista(valores) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def upgrade() -> None:
    op.create_table(
        "monitoring_metric",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("execution_id", sa.BigInteger, sa.ForeignKey(f"{ESQUEMA}.execution_log.id")),
        sa.Column("metrica", sa.Text, nullable=False),
        sa.Column("alcance", sa.Text, nullable=False),
        sa.Column("id_ruta", sa.Integer),
        sa.Column("horizonte_min", sa.SmallInteger),
        sa.Column("model_registry_id", sa.BigInteger, sa.ForeignKey(f"{ESQUEMA}.model_registry.id")),
        sa.Column("ventana_desde", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ventana_hasta", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valor", sa.Numeric(12, 4)),
        sa.Column("referencia", sa.Numeric(12, 4)),
        sa.Column("umbral", sa.Numeric(12, 4)),
        sa.Column("estado", sa.Text, nullable=False),
        sa.Column("muestras", sa.Integer),
        sa.Column("detalle", postgresql.JSONB),
        sa.Column("calculado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(f"metrica IN ({_lista(METRICAS)})", name="ck_monitoreo_metrica"),
        sa.CheckConstraint("estado IN ('ok', 'alerta', 'sin_datos')", name="ck_monitoreo_estado"),
        sa.CheckConstraint("ventana_hasta >= ventana_desde", name="ck_monitoreo_ventana"),
        schema=ESQUEMA,
    )
    op.create_index("ix_monitoreo_metrica_alcance", "monitoring_metric",
                    ["metrica", "alcance", sa.text("calculado_en DESC")], schema=ESQUEMA)
    op.create_index("ix_monitoreo_ejecucion", "monitoring_metric", ["execution_id"], schema=ESQUEMA)
    op.create_index("ix_monitoreo_modelo", "monitoring_metric", ["model_registry_id"], schema=ESQUEMA,
                    postgresql_where=sa.text("model_registry_id IS NOT NULL"))
    parametro = sa.table(
        "operational_parameter",
        sa.column("clave", sa.Text), sa.column("id_ruta", sa.Integer), sa.column("valor", sa.Numeric),
        sa.column("unidad", sa.Text), sa.column("descripcion", sa.Text), sa.column("creado_por", sa.Text),
        schema=ESQUEMA,
    )
    op.bulk_insert(parametro, [
        {"clave": c, "id_ruta": None, "valor": v, "unidad": u, "descripcion": d, "creado_por": CREADOR}
        for c, v, u, d in PARAMETROS
    ])


def downgrade() -> None:
    op.execute(sa.text(f"DELETE FROM {ESQUEMA}.operational_parameter WHERE creado_por = '{CREADOR}'"))
    op.drop_index("ix_monitoreo_modelo", "monitoring_metric", schema=ESQUEMA)
    op.drop_index("ix_monitoreo_ejecucion", "monitoring_metric", schema=ESQUEMA)
    op.drop_index("ix_monitoreo_metrica_alcance", "monitoring_metric", schema=ESQUEMA)
    op.drop_table("monitoring_metric", schema=ESQUEMA)
