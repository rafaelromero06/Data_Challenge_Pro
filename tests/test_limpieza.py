import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
CARPETAS = ["frecuencias", "migrations", "tests", "pruebas"]
ARCHIVOS_PY = sorted(p for c in CARPETAS for p in (RAIZ / c).rglob("*.py"))
ARCHIVOS_WEB = sorted((RAIZ / "frecuencias" / "tablero").glob("*.*"))
COMENTARIO_WEB = re.compile(r"<!--|/\*|^\s*//", re.MULTILINE)


def _relativa(ruta: Path) -> str:
    return ruta.relative_to(RAIZ).as_posix()


@pytest.mark.parametrize("ruta", ARCHIVOS_PY, ids=_relativa)
def test_python_sin_comentarios_ni_docstrings(ruta):
    fuente = ruta.read_text(encoding="utf-8")
    comentarios = [t.start[0] for t in tokenize.generate_tokens(io.StringIO(fuente).readline)
                   if t.type == tokenize.COMMENT]
    assert comentarios == [], f"Comentarios en las lineas {comentarios}"
    nodos = [n for n in ast.walk(ast.parse(fuente))
             if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    con_docstring = [getattr(n, "name", "modulo") for n in nodos if ast.get_docstring(n) is not None]
    assert con_docstring == [], f"Docstrings en {con_docstring}"


@pytest.mark.parametrize("ruta", ARCHIVOS_WEB, ids=_relativa)
def test_tablero_sin_comentarios(ruta):
    assert not COMENTARIO_WEB.search(ruta.read_text(encoding="utf-8"))
