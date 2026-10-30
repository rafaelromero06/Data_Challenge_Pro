import os
import socket
import threading
import uuid
from contextlib import contextmanager

from sqlalchemy.exc import SQLAlchemyError

from frecuencias.observabilidad import describir_error, log
from frecuencias.persistencia import ejecuciones

SEGUNDOS_LATIDO = float(os.environ.get("EJECUCION_LATIDO_S", "30"))
SEGUNDOS_SIN_LATIDO = float(os.environ.get("EJECUCION_SIN_LATIDO_S", "180"))
SERVICIOS_DE_INSTANCIA_UNICA = {"api", "programador"}

_proceso: dict = {"servicio": "cli", "instancia": None, "latido": None}


def prefijo(servicio: str) -> str:
    return f"{servicio}@{socket.gethostname()}#"


def instancia() -> str:
    if _proceso["instancia"] is None:
        _proceso["instancia"] = prefijo(_proceso["servicio"]) + uuid.uuid4().hex[:8]
    return _proceso["instancia"]


def latir(motor, propietario: str) -> int:
    try:
        with motor.begin() as conn:
            return ejecuciones.latir(conn, propietario)
    except SQLAlchemyError as error:
        log("trabajos").warning("latido_fallido", error=describir_error(error))
        return 0


class Latido(threading.Thread):
    def __init__(self, motor, propietario: str, intervalo_s: float):
        super().__init__(name="latido-ejecuciones", daemon=True)
        self.motor = motor
        self.propietario = propietario
        self.intervalo_s = intervalo_s
        self.parar = threading.Event()

    def run(self) -> None:
        while not self.parar.wait(self.intervalo_s):
            latir(self.motor, self.propietario)

    def detener(self) -> None:
        self.parar.set()
        self.join(timeout=5)


def recuperar(motor, servicio: str | None = None) -> list[dict]:
    servicio = servicio or _proceso["servicio"]
    propio = prefijo(servicio) if servicio in SERVICIOS_DE_INSTANCIA_UNICA else None
    with motor.begin() as conn:
        recuperadas = ejecuciones.recuperar(conn, SEGUNDOS_SIN_LATIDO, propio, instancia())
    for e in recuperadas:
        log("trabajos").warning("ejecucion_interrumpida", execution_id=e["id"], tipo=e["tipo"], etapa=e["etapa"],
                                intento=e["intento"])
    return recuperadas


def iniciar_proceso(motor, servicio: str) -> list[dict]:
    detener_proceso()
    _proceso.update(servicio=servicio, instancia=None)
    recuperadas = recuperar(motor, servicio)
    latido = Latido(motor, instancia(), SEGUNDOS_LATIDO)
    latido.start()
    _proceso["latido"] = latido
    log("trabajos").info("proceso_iniciado", servicio=servicio, instancia=instancia(), recuperadas=len(recuperadas))
    return recuperadas


def detener_proceso() -> None:
    latido = _proceso.get("latido")
    if latido is not None:
        latido.detener()
    _proceso["latido"] = None


@contextmanager
def latiendo(motor):
    if _proceso.get("latido") is not None and _proceso["latido"].is_alive():
        yield
        return
    latido = Latido(motor, instancia(), SEGUNDOS_LATIDO)
    latido.start()
    try:
        yield
    finally:
        latido.detener()


def avanzar(motor, id_ejecucion: int, etapa: str, avance: float) -> None:
    try:
        with motor.begin() as conn:
            ejecuciones.avanzar(conn, id_ejecucion, etapa, avance)
    except SQLAlchemyError as error:
        log("trabajos").warning("avance_no_registrado", execution_id=id_ejecucion, error=describir_error(error))
