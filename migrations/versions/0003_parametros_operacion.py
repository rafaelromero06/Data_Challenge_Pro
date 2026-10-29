import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

ESQUEMA = "ai_frecuencias"
CREADOR = "semilla analisis de flota 2025"
GENERALES = [
    ("ocupacion_objetivo", None, 80.0, "%", "Ocupacion a la que apunta la recomendacion de frecuencia"),
]
POR_RUTA = [
    ('capacidad_nominal', 2, 50.0, 'pasajeros', 'Capacidad nominal (sentados + de pie), mediana por viaje 2025'),
    ('capacidad_conservadora', 2, 46.0, 'pasajeros', 'Capacidad p10 por viaje 2025'),
    ('factor_carga', 2, 0.402, 'fraccion', 'Carga maxima a bordo sobre subidas del viaje (sensores 2025)'),
    ('factor_carga:04-06', 2, 0.407, 'fraccion', 'Carga maxima a bordo sobre subidas del viaje (sensores 2025)'),
    ('factor_carga:06-09', 2, 0.397, 'fraccion', 'Carga maxima a bordo sobre subidas del viaje (sensores 2025)'),
    ('factor_carga:09-12', 2, 0.379, 'fraccion', 'Carga maxima a bordo sobre subidas del viaje (sensores 2025)'),
    ('factor_carga:12-15', 2, 0.405, 'fraccion', 'Carga maxima a bordo sobre subidas del viaje (sensores 2025)'),
    ('factor_carga:15-18', 2, 0.388, 'fraccion', 'Carga maxima a bordo sobre subidas del viaje (sensores 2025)'),
    ('factor_carga:18-21', 2, 0.454, 'fraccion', 'Carga maxima a bordo sobre subidas del viaje (sensores 2025)'),
    ('tiempo_ciclo:dom_fest:04-06', 2, 214.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:dom_fest:06-09', 2, 221.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:dom_fest:09-12', 2, 237.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:dom_fest:12-15', 2, 236.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:dom_fest:15-18', 2, 239.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:dom_fest:18-21', 2, 225.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:habil:04-06', 2, 280.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:habil:06-09', 2, 296.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:habil:09-12', 2, 287.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:habil:12-15', 2, 318.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:habil:15-18', 2, 300.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:habil:18-21', 2, 231.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:sabado:04-06', 2, 243.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:sabado:06-09', 2, 285.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:sabado:09-12', 2, 300.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:sabado:12-15', 2, 283.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:sabado:15-18', 2, 272.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:sabado:18-21', 2, 235.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('flota_disponible', 2, 55.0, 'vehiculos', 'Maximo entre p90 de vehiculos en operacion 2025 y flota programada mas respaldo'),
    ('headway_minimo', 2, 2.0, 'min', 'Separacion minima entre despachos'),
    ('headway_maximo:diurno', 2, 10.0, 'min', 'Headway maximo en dia habil y sabado de 5 a 20 h'),
    ('headway_maximo:nocturno', 2, 15.0, 'min', 'Headway maximo en domingo o festivo y despues de las 20 h'),
    ('capacidad_nominal', 16, 50.0, 'pasajeros', 'Capacidad nominal (sentados + de pie), mediana por viaje 2025'),
    ('capacidad_conservadora', 16, 47.0, 'pasajeros', 'Capacidad p10 por viaje 2025'),
    ('factor_carga', 16, 0.424, 'fraccion', 'Carga maxima a bordo sobre subidas del viaje (sensores 2025)'),
    ('factor_carga:04-06', 16, 0.465, 'fraccion', 'Carga maxima a bordo sobre subidas del viaje (sensores 2025)'),
    ('factor_carga:06-09', 16, 0.428, 'fraccion', 'Carga maxima a bordo sobre subidas del viaje (sensores 2025)'),
    ('factor_carga:09-12', 16, 0.364, 'fraccion', 'Carga maxima a bordo sobre subidas del viaje (sensores 2025)'),
    ('factor_carga:12-15', 16, 0.397, 'fraccion', 'Carga maxima a bordo sobre subidas del viaje (sensores 2025)'),
    ('factor_carga:15-18', 16, 0.427, 'fraccion', 'Carga maxima a bordo sobre subidas del viaje (sensores 2025)'),
    ('factor_carga:18-21', 16, 0.48, 'fraccion', 'Carga maxima a bordo sobre subidas del viaje (sensores 2025)'),
    ('tiempo_ciclo:dom_fest:04-06', 16, 187.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:dom_fest:06-09', 16, 192.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:dom_fest:09-12', 16, 201.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:dom_fest:12-15', 16, 201.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:dom_fest:15-18', 16, 204.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:dom_fest:18-21', 16, 201.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:habil:04-06', 16, 254.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:habil:06-09', 16, 279.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:habil:09-12', 16, 268.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:habil:12-15', 16, 291.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:habil:15-18', 16, 273.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:habil:18-21', 16, 209.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:sabado:04-06', 16, 223.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:sabado:06-09', 16, 268.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:sabado:09-12', 16, 278.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:sabado:12-15', 16, 257.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:sabado:15-18', 16, 241.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:sabado:18-21', 16, 212.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('flota_disponible', 16, 92.0, 'vehiculos', 'Maximo entre p90 de vehiculos en operacion 2025 y flota programada mas respaldo'),
    ('headway_minimo', 16, 2.0, 'min', 'Separacion minima entre despachos'),
    ('headway_maximo:diurno', 16, 10.0, 'min', 'Headway maximo en dia habil y sabado de 5 a 20 h'),
    ('headway_maximo:nocturno', 16, 15.0, 'min', 'Headway maximo en domingo o festivo y despues de las 20 h'),
    ('capacidad_nominal', 21, 50.0, 'pasajeros', 'Capacidad nominal (sentados + de pie), mediana por viaje 2025'),
    ('capacidad_conservadora', 21, 46.0, 'pasajeros', 'Capacidad p10 por viaje 2025'),
    ('factor_carga', 21, 0.391, 'fraccion', 'Carga maxima a bordo sobre subidas del viaje (sensores 2025)'),
    ('factor_carga:04-06', 21, 0.436, 'fraccion', 'Carga maxima a bordo sobre subidas del viaje (sensores 2025)'),
    ('factor_carga:06-09', 21, 0.373, 'fraccion', 'Carga maxima a bordo sobre subidas del viaje (sensores 2025)'),
    ('factor_carga:09-12', 21, 0.331, 'fraccion', 'Carga maxima a bordo sobre subidas del viaje (sensores 2025)'),
    ('factor_carga:12-15', 21, 0.388, 'fraccion', 'Carga maxima a bordo sobre subidas del viaje (sensores 2025)'),
    ('factor_carga:15-18', 21, 0.399, 'fraccion', 'Carga maxima a bordo sobre subidas del viaje (sensores 2025)'),
    ('factor_carga:18-21', 21, 0.449, 'fraccion', 'Carga maxima a bordo sobre subidas del viaje (sensores 2025)'),
    ('tiempo_ciclo:dom_fest:04-06', 21, 214.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:dom_fest:06-09', 21, 221.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:dom_fest:09-12', 21, 236.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:dom_fest:12-15', 21, 238.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:dom_fest:15-18', 21, 244.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:dom_fest:18-21', 21, 237.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:habil:04-06', 21, 281.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:habil:06-09', 21, 303.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:habil:09-12', 21, 294.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:habil:12-15', 21, 324.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:habil:15-18', 21, 312.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:habil:18-21', 21, 241.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:sabado:04-06', 21, 241.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:sabado:06-09', 21, 287.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:sabado:09-12', 21, 306.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:sabado:12-15', 21, 284.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:sabado:15-18', 21, 280.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('tiempo_ciclo:sabado:18-21', 21, 247.0, 'min', 'Duracion p85 del viaje mas tiempo en terminal p15 (2025)'),
    ('flota_disponible', 21, 49.0, 'vehiculos', 'Maximo entre p90 de vehiculos en operacion 2025 y flota programada mas respaldo'),
    ('headway_minimo', 21, 2.0, 'min', 'Separacion minima entre despachos'),
    ('headway_maximo:diurno', 21, 10.0, 'min', 'Headway maximo en dia habil y sabado de 5 a 20 h'),
    ('headway_maximo:nocturno', 21, 15.0, 'min', 'Headway maximo en domingo o festivo y despues de las 20 h'),
]


def upgrade() -> None:
    parametro = sa.table(
        "operational_parameter",
        sa.column("clave", sa.Text), sa.column("id_ruta", sa.Integer), sa.column("valor", sa.Numeric),
        sa.column("unidad", sa.Text), sa.column("descripcion", sa.Text), sa.column("creado_por", sa.Text),
        schema=ESQUEMA,
    )
    op.bulk_insert(parametro, [
        {"clave": c, "id_ruta": r, "valor": v, "unidad": u, "descripcion": d, "creado_por": CREADOR}
        for c, r, v, u, d in GENERALES + POR_RUTA
    ])


def downgrade() -> None:
    op.execute(sa.text(
        "DELETE FROM ai_frecuencias.operational_parameter WHERE creado_por = 'semilla analisis de flota 2025'"
    ))
