from logging.config import fileConfig

from alembic import context
from sqlalchemy.schema import CreateSchema

from frecuencias import db
from frecuencias.config import ESQUEMA_IA

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)


def _migrar(**opciones) -> None:
    context.configure(version_table_schema=ESQUEMA_IA, **opciones)
    with context.begin_transaction():
        context.execute(CreateSchema(ESQUEMA_IA, if_not_exists=True))
        context.run_migrations()


if context.is_offline_mode():
    _migrar(dialect_name="postgresql", literal_binds=True)
else:
    with db.motor_destino().connect() as conexion:
        _migrar(connection=conexion)
