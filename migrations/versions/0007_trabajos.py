import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

ESQUEMA = "ai_frecuencias"
TABLA = "execution_log"


def upgrade() -> None:
    op.add_column(TABLA, sa.Column("propietario", sa.Text), schema=ESQUEMA)
    op.add_column(TABLA, sa.Column("latido_en", sa.DateTime(timezone=True)), schema=ESQUEMA)
    op.add_column(TABLA, sa.Column("etapa", sa.Text), schema=ESQUEMA)
    op.add_column(TABLA, sa.Column("avance", sa.Numeric(4, 1)), schema=ESQUEMA)
    op.add_column(TABLA, sa.Column("intento", sa.SmallInteger, nullable=False, server_default="1"), schema=ESQUEMA)
    op.add_column(TABLA, sa.Column("reintento_de", sa.BigInteger, sa.ForeignKey(f"{ESQUEMA}.{TABLA}.id")),
                  schema=ESQUEMA)
    op.drop_constraint("ck_ejecucion_estado", TABLA, schema=ESQUEMA)
    op.create_check_constraint("ck_ejecucion_estado", TABLA,
                               "estado IN ('en_curso', 'exitosa', 'fallida', 'interrumpida')", schema=ESQUEMA)
    op.create_check_constraint("ck_ejecucion_avance", TABLA, "avance IS NULL OR avance BETWEEN 0 AND 100",
                               schema=ESQUEMA)
    op.create_check_constraint("ck_ejecucion_intento", TABLA, "intento >= 1", schema=ESQUEMA)
    op.execute(sa.text(f"""
        UPDATE {ESQUEMA}.{TABLA}
           SET estado = 'interrumpida', finalizada_en = clock_timestamp(),
               error = 'Marcada como interrumpida al aplicar la migracion 0007: habia otro ciclo en curso'
         WHERE tipo = 'ciclo' AND estado = 'en_curso'
           AND id <> (SELECT max(id) FROM {ESQUEMA}.{TABLA} WHERE tipo = 'ciclo' AND estado = 'en_curso')
    """))
    op.create_index("ux_ejecucion_ciclo_en_curso", TABLA, ["tipo"], unique=True, schema=ESQUEMA,
                    postgresql_where=sa.text("tipo = 'ciclo' AND estado = 'en_curso'"))
    op.create_index("ix_ejecucion_en_curso_latido", TABLA, ["latido_en"], schema=ESQUEMA,
                    postgresql_where=sa.text("estado = 'en_curso'"))
    op.create_index("ix_ejecucion_reintento_de", TABLA, ["reintento_de"], schema=ESQUEMA,
                    postgresql_where=sa.text("reintento_de IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_ejecucion_reintento_de", TABLA, schema=ESQUEMA)
    op.drop_index("ix_ejecucion_en_curso_latido", TABLA, schema=ESQUEMA)
    op.drop_index("ux_ejecucion_ciclo_en_curso", TABLA, schema=ESQUEMA)
    op.execute(sa.text(f"UPDATE {ESQUEMA}.{TABLA} SET estado = 'fallida' WHERE estado = 'interrumpida'"))
    op.drop_constraint("ck_ejecucion_intento", TABLA, schema=ESQUEMA)
    op.drop_constraint("ck_ejecucion_avance", TABLA, schema=ESQUEMA)
    op.drop_constraint("ck_ejecucion_estado", TABLA, schema=ESQUEMA)
    op.create_check_constraint("ck_ejecucion_estado", TABLA, "estado IN ('en_curso', 'exitosa', 'fallida')",
                               schema=ESQUEMA)
    for columna in ("reintento_de", "intento", "avance", "etapa", "latido_en", "propietario"):
        op.drop_column(TABLA, columna, schema=ESQUEMA)
