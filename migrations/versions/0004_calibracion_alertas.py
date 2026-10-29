import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

ESQUEMA = "ai_frecuencias"
CREADOR = "calibracion de alertas oct 2026"
PARAMETROS = [
    ("umbral_alta_demanda", 100.0, "%",
     "Ocupacion prevista en el escenario alto, con la oferta actual, a partir de la cual se genera ALT-001"),
    ("tolerancia_frecuencia_buses", 2.0, "buses/h",
     "Deficit de buses por hora frente a la recomendacion a partir del cual se genera ALT-003"),
    ("tolerancia_frecuencia_pct", 15.0, "%",
     "Deficit relativo frente a la recomendacion a partir del cual se genera ALT-003"),
]


def upgrade() -> None:
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
    op.execute(sa.text(
        "DELETE FROM ai_frecuencias.operational_parameter WHERE creado_por = 'calibracion de alertas oct 2026'"
    ))
