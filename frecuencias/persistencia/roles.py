import argparse
import base64
import hashlib
import hmac
import os
import re
import secrets

from psycopg2 import sql

from frecuencias import db

ROL_APLICACION = "ai_frecuencias_app"
ITERACIONES_SCRAM = 4096
NOMBRE_VALIDO = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
LONGITUD_MINIMA_CLAVE = 16
ATRIBUTOS = "LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
CREAR_USUARIO = sql.SQL(f"CREATE ROLE {{}} WITH {ATRIBUTOS} PASSWORD {{}}")
ALTERAR_USUARIO = sql.SQL(f"ALTER ROLE {{}} WITH {ATRIBUTOS} PASSWORD {{}}")


def _b64(datos: bytes) -> str:
    return base64.b64encode(datos).decode("ascii")


def verificador_scram(clave: str, sal: bytes | None = None, iteraciones: int = ITERACIONES_SCRAM) -> str:
    sal = sal or secrets.token_bytes(16)
    salada = hashlib.pbkdf2_hmac("sha256", clave.encode("utf-8"), sal, iteraciones)
    cliente = hmac.new(salada, b"Client Key", hashlib.sha256).digest()
    servidor = hmac.new(salada, b"Server Key", hashlib.sha256).digest()
    return f"SCRAM-SHA-256${iteraciones}:{_b64(sal)}${_b64(hashlib.sha256(cliente).digest())}:{_b64(servidor)}"


def validar(usuario: str, clave: str) -> None:
    if not NOMBRE_VALIDO.match(usuario or ""):
        raise ValueError("El usuario de la aplicacion debe tener solo minusculas, digitos o guion bajo.")
    if len(clave or "") < LONGITUD_MINIMA_CLAVE:
        raise ValueError(f"La clave del usuario de la aplicacion debe tener al menos {LONGITUD_MINIMA_CLAVE} caracteres.")


def asegurar_usuario(conexion_dbapi, usuario: str, clave: str) -> str:
    validar(usuario, clave)
    with conexion_dbapi.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ROL_APLICACION,))
        if cursor.fetchone() is None:
            raise RuntimeError(f"No existe el rol {ROL_APLICACION}: aplique antes las migraciones (alembic upgrade head).")
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (usuario,))
        existe = cursor.fetchone() is not None
        plantilla = ALTERAR_USUARIO if existe else CREAR_USUARIO
        cursor.execute(plantilla.format(sql.Identifier(usuario), sql.Literal(verificador_scram(clave))))
        cursor.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(ROL_APLICACION), sql.Identifier(usuario)))
    return "actualizado" if existe else "creado"


def main():
    parser = argparse.ArgumentParser(description="Crea o actualiza el usuario de la aplicacion con permisos minimos")
    parser.add_argument("--usuario", default=os.environ.get("AI_DB_APP_USER", "frecuencias_app"))
    args = parser.parse_args()
    clave = os.environ.get("AI_DB_APP_PASSWORD", "")
    motor = db.motor_destino()
    conexion = motor.raw_connection()
    try:
        resultado = asegurar_usuario(conexion, args.usuario, clave)
        conexion.commit()
    finally:
        conexion.close()
        motor.dispose()
    print(f"usuario {args.usuario} {resultado} como miembro de {ROL_APLICACION}")


if __name__ == "__main__":
    main()
