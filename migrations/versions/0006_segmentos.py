import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

ESQUEMA = "ai_frecuencias"
AHORA = sa.text("now()")
TIPOS_DIA = "'habil', 'sabado', 'dom_fest'"


def _momento(nombre: str, **opciones) -> sa.Column:
    return sa.Column(nombre, sa.DateTime(timezone=True), **opciones)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table(
        "route_segment",
        sa.Column("id_segmento", sa.BigInteger, primary_key=True, autoincrement=False),
        sa.Column("id_config", sa.BigInteger, nullable=False),
        sa.Column("id_ruta", sa.Integer, nullable=False),
        sa.Column("nro_segmento", sa.Integer, nullable=False),
        sa.Column("longitud_m", sa.Numeric(12, 3), nullable=False),
        sa.Column("inicio_m", sa.Numeric(12, 3), nullable=False),
        _momento("cargado_en", nullable=False, server_default=AHORA),
        sa.UniqueConstraint("id_config", "nro_segmento", name="uq_segmento_config_numero"),
        sa.CheckConstraint("longitud_m > 0", name="ck_segmento_longitud"),
        schema=ESQUEMA,
    )
    op.execute(f"ALTER TABLE {ESQUEMA}.route_segment ADD COLUMN geom geometry(LineString, 4326) NOT NULL")
    op.execute(f"CREATE INDEX ix_segmento_geom ON {ESQUEMA}.route_segment USING gist (geom)")
    op.create_index("ix_segmento_ruta", "route_segment", ["id_ruta", "nro_segmento"], schema=ESQUEMA)

    op.create_table(
        "segment_observation",
        sa.Column("idprogramacion", sa.BigInteger, primary_key=True, autoincrement=False),
        sa.Column("id_segmento", sa.BigInteger, sa.ForeignKey(f"{ESQUEMA}.route_segment.id_segmento"),
                  primary_key=True),
        _momento("franja", primary_key=True),
        sa.Column("id_ruta", sa.Integer, nullable=False),
        sa.Column("subidas", sa.Integer, nullable=False),
        sa.Column("bajadas", sa.Integer, nullable=False),
        sa.Column("pasajeros", sa.BigInteger, nullable=False),
        sa.Column("carga_media", sa.Numeric(8, 2)),
        sa.Column("carga_maxima", sa.Numeric(8, 2)),
        sa.Column("lecturas", sa.Integer, nullable=False),
        sa.Column("distancia_max_m", sa.Numeric(8, 1), nullable=False),
        sa.Column("execution_id", sa.BigInteger, sa.ForeignKey(f"{ESQUEMA}.execution_log.id")),
        _momento("cargado_en", nullable=False, server_default=AHORA),
        sa.CheckConstraint("subidas >= 0 AND bajadas >= 0 AND pasajeros >= 0", name="ck_obs_segmento_conteos"),
        schema=ESQUEMA,
    )
    op.create_index("ix_obs_segmento_ruta_franja", "segment_observation", ["id_ruta", "franja"], schema=ESQUEMA)

    op.create_table(
        "segment_profile",
        sa.Column("id_segmento", sa.BigInteger, sa.ForeignKey(f"{ESQUEMA}.route_segment.id_segmento"),
                  primary_key=True),
        sa.Column("tipo_dia", sa.Text, primary_key=True),
        sa.Column("hora", sa.SmallInteger, primary_key=True),
        sa.Column("id_ruta", sa.Integer, nullable=False),
        sa.Column("nro_viajes", sa.Integer, nullable=False),
        sa.Column("subidas_prom", sa.Numeric(8, 3), nullable=False),
        sa.Column("subidas_min", sa.Integer, nullable=False),
        sa.Column("subidas_max", sa.Integer, nullable=False),
        sa.Column("bajadas_prom", sa.Numeric(8, 3), nullable=False),
        sa.Column("bajadas_min", sa.Integer, nullable=False),
        sa.Column("bajadas_max", sa.Integer, nullable=False),
        sa.Column("pasajeros_prom", sa.Numeric(14, 3), nullable=False),
        sa.Column("pasajeros_min", sa.BigInteger, nullable=False),
        sa.Column("pasajeros_max", sa.BigInteger, nullable=False),
        sa.Column("carga_prom", sa.Numeric(8, 3)),
        sa.Column("carga_p90", sa.Numeric(8, 3)),
        sa.Column("carga_max", sa.Numeric(8, 3)),
        sa.Column("cuota_subidas", sa.Numeric(8, 6), nullable=False),
        sa.Column("periodo_desde", sa.Date, nullable=False),
        sa.Column("periodo_hasta", sa.Date, nullable=False),
        sa.Column("execution_id", sa.BigInteger, sa.ForeignKey(f"{ESQUEMA}.execution_log.id")),
        _momento("calculado_en", nullable=False, server_default=AHORA),
        sa.CheckConstraint(f"tipo_dia IN ({TIPOS_DIA})", name="ck_perfil_tipo_dia"),
        sa.CheckConstraint("hora BETWEEN 0 AND 23", name="ck_perfil_hora"),
        sa.CheckConstraint("cuota_subidas BETWEEN 0 AND 1", name="ck_perfil_cuota"),
        schema=ESQUEMA,
    )
    op.create_index("ix_perfil_ruta", "segment_profile", ["id_ruta", "tipo_dia", "hora"], schema=ESQUEMA)


def downgrade() -> None:
    for tabla in ("segment_profile", "segment_observation", "route_segment"):
        op.drop_table(tabla, schema=ESQUEMA)
