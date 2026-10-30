import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

ESQUEMA = "ai_frecuencias"
ROL = "ai_frecuencias_app"


def upgrade() -> None:
    op.execute(sa.text(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROL}') THEN
                IF NOT (SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user) THEN
                    RAISE NOTICE 'Sin permiso CREATEROLE: el administrador de la base debe crear el rol {ROL}';
                    RETURN;
                END IF;
                CREATE ROLE {ROL} NOLOGIN;
            END IF;
            REVOKE CREATE ON SCHEMA public FROM PUBLIC;
            GRANT USAGE ON SCHEMA {ESQUEMA} TO {ROL};
            GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA {ESQUEMA} TO {ROL};
            REVOKE INSERT, UPDATE ON {ESQUEMA}.alembic_version FROM {ROL};
            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {ESQUEMA} TO {ROL};
            ALTER DEFAULT PRIVILEGES IN SCHEMA {ESQUEMA} GRANT SELECT, INSERT, UPDATE ON TABLES TO {ROL};
            ALTER DEFAULT PRIVILEGES IN SCHEMA {ESQUEMA} GRANT USAGE, SELECT ON SEQUENCES TO {ROL};
            IF to_regclass('public.spatial_ref_sys') IS NOT NULL THEN
                GRANT SELECT ON public.spatial_ref_sys TO {ROL};
            END IF;
        END $$
    """))


def downgrade() -> None:
    op.execute(sa.text(f"""
        DO $$
        BEGIN
            GRANT CREATE ON SCHEMA public TO PUBLIC;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROL}') THEN
                ALTER DEFAULT PRIVILEGES IN SCHEMA {ESQUEMA} REVOKE ALL ON TABLES FROM {ROL};
                ALTER DEFAULT PRIVILEGES IN SCHEMA {ESQUEMA} REVOKE ALL ON SEQUENCES FROM {ROL};
                REVOKE ALL ON ALL TABLES IN SCHEMA {ESQUEMA} FROM {ROL};
                REVOKE ALL ON ALL SEQUENCES IN SCHEMA {ESQUEMA} FROM {ROL};
                REVOKE USAGE ON SCHEMA {ESQUEMA} FROM {ROL};
                IF to_regclass('public.spatial_ref_sys') IS NOT NULL THEN
                    REVOKE SELECT ON public.spatial_ref_sys FROM {ROL};
                END IF;
            END IF;
        END $$
    """))
