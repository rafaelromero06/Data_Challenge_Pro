import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

ESQUEMA = "ai_frecuencias"
AHORA = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "api_key",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("nombre", sa.Text, nullable=False),
        sa.Column("rol", sa.Text, nullable=False),
        sa.Column("huella", sa.CHAR(64), nullable=False),
        sa.Column("prefijo", sa.Text, nullable=False),
        sa.Column("activa", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("creada_por", sa.Text, nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False, server_default=AHORA),
        sa.Column("revocada_en", sa.DateTime(timezone=True)),
        sa.Column("ultimo_uso", sa.DateTime(timezone=True)),
        sa.CheckConstraint("rol IN ('lector', 'operador', 'administrador')", name="ck_api_key_rol"),
        sa.CheckConstraint("activa OR revocada_en IS NOT NULL", name="ck_api_key_revocada"),
        sa.UniqueConstraint("huella", name="uq_api_key_huella"),
        schema=ESQUEMA,
    )
    op.create_index("uq_api_key_nombre_activa", "api_key", ["nombre"], unique=True, schema=ESQUEMA,
                    postgresql_where=sa.text("activa"))


def downgrade() -> None:
    op.drop_table("api_key", schema=ESQUEMA)
