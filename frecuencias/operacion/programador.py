import argparse
import os

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from frecuencias import db
from frecuencias.config import ZONA_HORARIA
from frecuencias.observabilidad import configurar_logs, describir_error, log
from frecuencias.operacion.ciclo import ejecutar_ciclo

CRON_POR_DEFECTO = "15 * * * *"


def tarea(motor, origen: str, reentrenar: str) -> None:
    try:
        resultado = ejecutar_ciclo(motor, origen, 1, reentrenar)
        log("programador").info("ciclo_programado", execution_id=resultado["execution_id"],
                                **resultado.get("operacion", {}))
    except Exception as error:
        log("programador").error("ciclo_programado_fallido", error=describir_error(error))


def main():
    parser = argparse.ArgumentParser(description="Ejecuta el ciclo incremental segun una expresion cron")
    parser.add_argument("--cron", default=os.environ.get("CICLO_CRON", CRON_POR_DEFECTO))
    parser.add_argument("--origen", choices=["base", "archivos"], default=os.environ.get("CICLO_ORIGEN", "base"))
    parser.add_argument("--reentrenar", choices=["auto", "siempre", "nunca"],
                        default=os.environ.get("CICLO_REENTRENAR", "auto"))
    parser.add_argument("--ahora", action="store_true", help="Corre un ciclo al arrancar")
    args = parser.parse_args()
    configurar_logs()
    motor = db.motor_destino()
    programador = BlockingScheduler(timezone=ZONA_HORARIA)
    programador.add_job(tarea, CronTrigger.from_crontab(args.cron, timezone=ZONA_HORARIA), args=[motor, args.origen,
                        args.reentrenar], id="ciclo", max_instances=1, coalesce=True, misfire_grace_time=900)
    if args.ahora:
        tarea(motor, args.origen, args.reentrenar)
    log("programador").info("programador_iniciado", cron=args.cron, origen=args.origen, reentrenar=args.reentrenar)
    programador.start()


if __name__ == "__main__":
    main()
