import argparse
import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection

from frecuencias.persistencia.tablas import clave_api
from frecuencias.tiempo import ahora_utc

ROLES = {"lector": 1, "operador": 2, "administrador": 3}
PREFIJO_CLAVE = "fq_"
CABECERA = "X-API-Key"
REGISTRO_USO = timedelta(minutes=5)


def huella(clave: str) -> str:
    return hashlib.sha256(clave.encode("utf-8")).hexdigest()


def generar() -> str:
    return PREFIJO_CLAVE + secrets.token_urlsafe(32)


def autorizado(rol: str, rol_minimo: str) -> bool:
    return ROLES.get(rol, 0) >= ROLES[rol_minimo]


def crear(conn: Connection, nombre: str, rol: str, creada_por: str) -> str:
    if rol not in ROLES:
        raise ValueError(f"Rol desconocido '{rol}': use {', '.join(ROLES)}.")
    clave = generar()
    conn.execute(insert(clave_api).values(nombre=nombre, rol=rol, huella=huella(clave), prefijo=clave[:10],
                                          activa=True, creada_por=creada_por))
    return clave


def validar(conn: Connection, clave: str | None) -> dict | None:
    if not clave:
        return None
    fila = conn.execute(select(clave_api.c.id, clave_api.c.nombre, clave_api.c.rol, clave_api.c.ultimo_uso)
                        .where(clave_api.c.huella == huella(clave), clave_api.c.activa)).mappings().first()
    if fila is None:
        return None
    ahora = ahora_utc()
    if fila["ultimo_uso"] is None or ahora - fila["ultimo_uso"] > REGISTRO_USO:
        conn.execute(update(clave_api).where(clave_api.c.id == fila["id"]).values(ultimo_uso=ahora))
    return {"id": fila["id"], "nombre": fila["nombre"], "rol": fila["rol"]}


def revocar(conn: Connection, nombre: str) -> bool:
    resultado = conn.execute(update(clave_api).where(clave_api.c.nombre == nombre, clave_api.c.activa)
                             .values(activa=False, revocada_en=ahora_utc()))
    return resultado.rowcount > 0


def listar(conn: Connection) -> list[dict]:
    columnas = [c for c in clave_api.c if c.name != "huella"]
    return [dict(f) for f in conn.execute(select(*columnas).order_by(clave_api.c.id)).mappings()]


def main():
    from frecuencias import db

    parser = argparse.ArgumentParser(description="Administra las claves de acceso a la API")
    sub = parser.add_subparsers(dest="accion", required=True)
    nueva = sub.add_parser("crear", help="Crea una clave y la muestra una sola vez")
    nueva.add_argument("--nombre", required=True)
    nueva.add_argument("--rol", required=True, choices=list(ROLES))
    nueva.add_argument("--creada-por", default="consola")
    baja = sub.add_parser("revocar", help="Revoca la clave activa con ese nombre")
    baja.add_argument("--nombre", required=True)
    sub.add_parser("listar", help="Lista las claves sin mostrar su valor")
    args = parser.parse_args()
    with db.motor_destino().begin() as conn:
        if args.accion == "crear":
            print(crear(conn, args.nombre, args.rol, args.creada_por))
        elif args.accion == "revocar":
            print("revocada" if revocar(conn, args.nombre) else "no habia una clave activa con ese nombre")
        else:
            for fila in listar(conn):
                print(fila)


if __name__ == "__main__":
    main()
