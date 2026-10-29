import pytest

from frecuencias.observabilidad import describir_error
from frecuencias.resiliencia import CircuitoAbierto, Interruptor, reintentar


class Reloj:
    def __init__(self):
        self.ahora = 0.0

    def __call__(self):
        return self.ahora


def _falla():
    raise OSError("sin conexion")


def test_circuito_se_abre_y_se_recupera():
    reloj = Reloj()
    interruptor = Interruptor("prueba", fallos_maximos=2, espera_s=30, reloj=reloj)
    for _ in range(2):
        with pytest.raises(OSError):
            interruptor.llamar(_falla)
    assert interruptor.estado == "abierto"
    with pytest.raises(CircuitoAbierto):
        interruptor.llamar(lambda: "no se llama")
    reloj.ahora = 31
    assert interruptor.estado == "semiabierto"
    assert interruptor.llamar(lambda: "ok") == "ok"
    assert interruptor.estado == "cerrado" and interruptor.fallos == 0


def test_circuito_semiabierto_vuelve_a_abrirse_si_falla():
    reloj = Reloj()
    interruptor = Interruptor("prueba2", fallos_maximos=1, espera_s=10, reloj=reloj)
    with pytest.raises(OSError):
        interruptor.llamar(_falla)
    reloj.ahora = 11
    with pytest.raises(OSError):
        interruptor.llamar(_falla)
    assert interruptor.estado == "abierto"


def test_reintentar_con_espera_exponencial():
    esperas, llamadas = [], []

    def inestable():
        llamadas.append(1)
        if len(llamadas) < 3:
            raise OSError("transitorio")
        return "listo"

    assert reintentar(inestable, intentos=3, espera_inicial_s=1, excepciones=(OSError,), dormir=esperas.append) == ("listo", 3)
    assert esperas == [1, 2]


def test_reintentar_no_reintenta_errores_no_transitorios():
    esperas = []
    with pytest.raises(ValueError):
        reintentar(lambda: (_ for _ in ()).throw(ValueError("permanente")), intentos=3, excepciones=(OSError,),
                   dormir=esperas.append)
    assert esperas == []


def test_reintentar_agota_los_intentos():
    with pytest.raises(OSError):
        reintentar(_falla, intentos=2, excepciones=(OSError,), dormir=lambda s: None)


def test_describir_error_oculta_datos_de_conexion():
    error = RuntimeError('connection to server at "db.ejemplo.co" (10.20.30.40), port 5432 failed: '
                        'FATAL: password authentication failed for user "usuario_reto"\n[SQL: SELECT 1]')
    texto = describir_error(error)
    assert "db.ejemplo.co" not in texto and "10.20.30.40" not in texto and "usuario_reto" not in texto
    assert "SELECT" not in texto and texto.startswith("RuntimeError")
    assert "host=***" in describir_error(RuntimeError("host=servidor user=alguien password=secreto"))
