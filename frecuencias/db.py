import os

from sqlalchemy import Column, MetaData, String, Table, create_engine, select
from sqlalchemy.engine import URL, Engine

from frecuencias.config import ESQUEMA_IA, RAIZ

try:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / ".env")
except ImportError:
    pass

VERSION_ESQUEMA_IA = Table("alembic_version", MetaData(schema=ESQUEMA_IA), Column("version_num", String(32)))


def motor_fuente() -> Engine:
    password = os.environ.get("SOBUSA_DB_PASSWORD") or os.environ.get("PGPASSWORD")
    if not password:
        raise RuntimeError(
            "Falta la contrasena: defina SOBUSA_DB_PASSWORD o PGPASSWORD "
            "(si la guardo con setx, reinicie la terminal o VS Code)."
        )
    url = URL.create(
        "postgresql+psycopg2",
        username=os.environ["SOBUSA_DB_USER"],
        password=password,
        host=os.environ["SOBUSA_DB_HOST"],
        port=int(os.environ.get("SOBUSA_DB_PORT", "5432")),
        database=os.environ.get("SOBUSA_DB_NAME", "dataset"),
    )
    return create_engine(
        url,
        connect_args={
            "options": "-c default_transaction_read_only=on -c statement_timeout=300000",
            "sslmode": os.environ.get("SOBUSA_DB_SSLMODE", "prefer"),
        },
    )


def url_destino() -> URL | None:
    if not os.environ.get("AI_DB_HOST"):
        return None
    return URL.create(
        "postgresql+psycopg2",
        username=os.environ["AI_DB_USER"],
        password=os.environ["AI_DB_PASSWORD"],
        host=os.environ["AI_DB_HOST"],
        port=int(os.environ.get("AI_DB_PORT", "5432")),
        database=os.environ["AI_DB_NAME"],
    )


def motor_destino() -> Engine:
    url = url_destino()
    if url is None:
        raise RuntimeError(
            "Falta configurar la base ai_frecuencias: defina AI_DB_HOST, AI_DB_PORT, "
            "AI_DB_NAME, AI_DB_USER y AI_DB_PASSWORD."
        )
    return create_engine(url, pool_pre_ping=True, connect_args={"options": "-c statement_timeout=30000"})


def revision_esquema_ia(motor: Engine) -> str | None:
    with motor.connect() as conn:
        return conn.execute(select(VERSION_ESQUEMA_IA.c.version_num)).scalar_one_or_none()
