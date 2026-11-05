import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

ESQUEMA = "ai_frecuencias"
ROL = "ai_frecuencias_app"


def _cambiar_permiso(sentencia: str) -> None:
    op.execute(sa.text(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROL}') THEN
                {sentencia};
            END IF;
        END $$
    """))


def upgrade() -> None:
    _cambiar_permiso(f"GRANT DELETE ON {ESQUEMA}.segment_profile TO {ROL}")


def downgrade() -> None:
    _cambiar_permiso(f"REVOKE DELETE ON {ESQUEMA}.segment_profile FROM {ROL}")
