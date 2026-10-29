import threading
import time
from collections.abc import Callable

from frecuencias.observabilidad import CIRCUITOS, log


class CircuitoAbierto(RuntimeError):
    pass


class Interruptor:
    def __init__(self, nombre: str, fallos_maximos: int = 3, espera_s: float = 60.0,
                 reloj: Callable[[], float] = time.monotonic):
        self.nombre = nombre
        self.fallos_maximos = fallos_maximos
        self.espera_s = espera_s
        self.reloj = reloj
        self.fallos = 0
        self.abierto_desde: float | None = None
        self._candado = threading.Lock()
        CIRCUITOS.labels(nombre).set(0)

    @property
    def estado(self) -> str:
        with self._candado:
            if self.abierto_desde is None:
                return "cerrado"
            return "semiabierto" if self.reloj() - self.abierto_desde >= self.espera_s else "abierto"

    def llamar(self, funcion: Callable, *args, **kwargs):
        if self.estado == "abierto":
            raise CircuitoAbierto(f"{self.nombre}: circuito abierto tras {self.fallos} fallos seguidos; "
                                  f"se vuelve a intentar despues de {self.espera_s:.0f} s.")
        try:
            resultado = funcion(*args, **kwargs)
        except Exception:
            self._registrar_fallo()
            raise
        self._registrar_exito()
        return resultado

    def _registrar_fallo(self) -> None:
        with self._candado:
            self.fallos += 1
            if self.fallos >= self.fallos_maximos:
                self.abierto_desde = self.reloj()
                CIRCUITOS.labels(self.nombre).set(1)
                log("resiliencia").warning("circuito_abierto", integracion=self.nombre, fallos=self.fallos)

    def _registrar_exito(self) -> None:
        with self._candado:
            if self.abierto_desde is not None:
                log("resiliencia").info("circuito_cerrado", integracion=self.nombre)
            self.fallos = 0
            self.abierto_desde = None
            CIRCUITOS.labels(self.nombre).set(0)


def reintentar(funcion: Callable, intentos: int = 3, espera_inicial_s: float = 1.0, factor: float = 2.0,
               excepciones: tuple = (Exception,), dormir: Callable[[float], None] = time.sleep) -> tuple:
    espera = espera_inicial_s
    for intento in range(1, intentos + 1):
        try:
            return funcion(), intento
        except excepciones as error:
            if intento == intentos:
                raise
            log("resiliencia").warning("reintento", intento=intento, error=type(error).__name__, espera_s=espera)
            dormir(espera)
            espera *= factor
    raise ValueError("intentos debe ser mayor que cero")
