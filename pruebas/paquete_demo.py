import argparse
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ARCHIVO_POR_DEFECTO = RAIZ / "paquete_demo" / "paquete_demo.tar.gz"
VOLCADO = "base.dump"
DATOS = ("demanda_15min.parquet", "viajes.parquet", "rutas.parquet", "estacionalidades.parquet", "novedades.parquet")
VOLCAR = 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -n ai_frecuencias -Fc --exclude-table-data=ai_frecuencias.api_key'
RESTAURAR = 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --exit-on-error'
CONTAR = ('psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "select (select count(*) from ai_frecuencias.demand_history), '
          '(select count(*) from ai_frecuencias.model_registry where estado = \'activo\')"')


def compose(*argumentos: str, **opciones) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *argumentos], cwd=RAIZ, check=True, **opciones)


def exportar(archivo: Path) -> None:
    activos = json.loads((RAIZ / "modelos" / "activos.json").read_text(encoding="utf-8"))
    archivo.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as carpeta:
        volcado = Path(carpeta) / VOLCADO
        with volcado.open("wb") as salida:
            compose("exec", "-T", "db", "sh", "-c", VOLCAR, stdout=salida)
        with tarfile.open(archivo, "w:gz") as paquete:
            paquete.add(volcado, VOLCADO)
            paquete.add(RAIZ / "modelos" / "activos.json", "modelos/activos.json")
            for activo in activos.values():
                paquete.add(RAIZ / "modelos" / activo["artefacto"], f"modelos/{activo['artefacto']}")
            for nombre in DATOS:
                paquete.add(RAIZ / "datos" / nombre, f"datos/{nombre}")
    print(f"Paquete creado: {archivo} ({archivo.stat().st_size / 1_000_000:.0f} MB)")
    print("Contiene datos del operador: compartalo solo por un canal privado, nunca en el repositorio.")


def _miembros(paquete: tarfile.TarFile) -> list[tarfile.TarInfo]:
    return [m for m in paquete.getmembers() if m.name.startswith(("modelos/", "datos/"))]


def contar() -> tuple[int, int]:
    conteo = compose("exec", "-T", "db", "sh", "-c", CONTAR, capture_output=True, text=True).stdout.strip()
    franjas, modelos = conteo.split("|")
    return int(franjas), int(modelos)


def importar(archivo: Path, reemplazar: bool = False) -> int:
    franjas, _ = contar()
    if franjas and not reemplazar:
        print(f"La base ya tiene {franjas} franjas de demanda y restaurar borraria esos datos y las claves de acceso.")
        print("Si de verdad quiere sustituirla, repita el comando con --reemplazar.")
        return 1
    with tempfile.TemporaryDirectory() as carpeta, tarfile.open(archivo, "r:gz") as paquete:
        paquete.extractall(RAIZ, members=_miembros(paquete), filter="data")
        paquete.extract(VOLCADO, carpeta, filter="data")
        with (Path(carpeta) / VOLCADO).open("rb") as volcado:
            compose("exec", "-T", "db", "sh", "-c", RESTAURAR, stdin=volcado)
    compose("run", "--rm", "migraciones")
    compose("restart", "api")
    franjas, modelos = contar()
    print(f"Base restaurada: {franjas} franjas de demanda y {modelos} modelos activos.")
    print("Cree una clave para el tablero y la API:")
    print("  docker compose exec api python -m frecuencias.seguridad crear --nombre demo --rol administrador")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Empaqueta o restaura datos y modelos para una demostracion "
                                                 "reproducible, sin acceso a la base del operador")
    parser.add_argument("accion", choices=["exportar", "importar"])
    parser.add_argument("--archivo", type=Path, default=ARCHIVO_POR_DEFECTO)
    parser.add_argument("--reemplazar", action="store_true", help="Al importar, sustituye una base que ya tiene datos")
    args = parser.parse_args()
    if args.accion == "exportar":
        exportar(args.archivo)
        return 0
    if not args.archivo.exists():
        print(f"No existe {args.archivo}. Pidalo al equipo; por contener datos del operador no va en git.")
        return 1
    return importar(args.archivo, args.reemplazar)


if __name__ == "__main__":
    sys.exit(main())
