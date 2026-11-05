import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

ESQUEMA = "ai_frecuencias"
CREADOR = "recaudo estimado nov 2026"


def upgrade() -> None:
    parametro = sa.table(
        "operational_parameter",
        sa.column("clave", sa.Text), sa.column("id_ruta", sa.Integer), sa.column("valor", sa.Numeric),
        sa.column("unidad", sa.Text), sa.column("descripcion", sa.Text), sa.column("creado_por", sa.Text),
        schema=ESQUEMA,
    )
    op.bulk_insert(parametro, [{
        "clave": "tarifa_pasajero", "id_ruta": None, "valor": None, "unidad": "COP por pasajero",
        "descripcion": "Tarifa media por pasaje para estimar el recaudo; sin valor hasta que el operador la entregue",
        "creado_por": CREADOR,
    }])


def downgrade() -> None:
    op.execute(sa.text(f"DELETE FROM {ESQUEMA}.operational_parameter WHERE creado_por = '{CREADOR}'"))
