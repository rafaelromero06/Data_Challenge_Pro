import argparse
import os
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from frecuencias import db, trazas
from frecuencias.config import ZONA_HORARIA
from frecuencias.observabilidad import configurar_logs, describir_error, log
from frecuencias.operacion import ciclo, trabajos

CRON_POR_DEFECTO = "15 * * * *"
SEGUNDOS_VIGILANCIA = 60


def _programar_reintento(programador, motor, e: dict, origen: str, reentrenar: str, error: Exception) -> None:
    if not ciclo.reintentable(error) or e.get("intento", 1) >= ciclo.MAX_INTENTOS:
        return
    espera = ciclo.espera_reintento(e.get("intento", 1))
    programador.add_job(tarea, "date", run_date=datetime.now(timezone.utc) + espera,
                        args=[programador, motor, origen, reentrenar, e["id"]], id=f"reintento-{e['id']}",
                        replace_existing=True)
    log("programador").warning("reintento_programado", execution_id=e["id"], intento=e.get("intento", 1) + 1,
                               en_minutos=espera.total_seconds() / 60)


def tarea(programador, motor, origen: str, reentrenar: str, reintento_de: int | None = None) -> None:
    try:
        if reintento_de is None:
            e = ciclo.iniciar(motor, origen, 1, reentrenar, solicitado_por="programador")
        else:
            e, argumentos = ciclo.preparar_reintento(motor, reintento_de, "programador")
            origen, reentrenar = argumentos["origen"], argumentos["reentrenar"]
    except ciclo.CicloEnCurso:
        log("programador").info("ciclo_omitido", motivo="otro ciclo en curso", reintento_de=reintento_de)
        return
    except Exception as error:
        log("programador").error("ciclo_no_iniciado", error=describir_error(error), reintento_de=reintento_de)
        return
    try:
        resultado = ciclo.ejecutar_ciclo(motor, origen, 1, reentrenar, e)
        log("programador").info("ciclo_programado", execution_id=resultado["execution_id"],
                                intento=e.get("intento", 1), **resultado.get("operacion", {}))
    except Exception as error:
        log("programador").error("ciclo_programado_fallido", execution_id=e["id"], error=describir_error(error))
        _programar_reintento(programador, motor, e, origen, reentrenar, error)


def reanudar(programador, motor, recuperadas: list[dict], origen: str, reentrenar: str) -> int:
    pendientes = ciclo.reanudables(recuperadas)
    for r in pendientes:
        programador.add_job(tarea, "date", run_date=datetime.now(timezone.utc),
                            args=[programador, motor, origen, reentrenar, r["id"]], id=f"reintento-{r['id']}",
                            replace_existing=True)
        log("programador").warning("ciclo_reanudado", execution_id=r["id"], intento=int(r["intento"]) + 1)
    return len(pendientes)


def vigilar(programador, motor, origen: str, reentrenar: str) -> None:
    try:
        reanudar(programador, motor, trabajos.recuperar(motor), origen, reentrenar)
    except Exception as error:
        log("programador").warning("vigilancia_fallida", error=describir_error(error))


def main():
    parser = argparse.ArgumentParser(description="Ejecuta el ciclo incremental segun una expresion cron")
    parser.add_argument("--cron", default=os.environ.get("CICLO_CRON", CRON_POR_DEFECTO))
    parser.add_argument("--origen", choices=["base", "archivos"], default=os.environ.get("CICLO_ORIGEN", "base"))
    parser.add_argument("--reentrenar", choices=["auto", "siempre", "nunca"],
                        default=os.environ.get("CICLO_REENTRENAR", "auto"))
    parser.add_argument("--ahora", action="store_true", help="Corre un ciclo al arrancar")
    args = parser.parse_args()
    configurar_logs()
    trazas.configurar("programador")
    motor = db.motor_destino()
    programador = BlockingScheduler(timezone=ZONA_HORARIA)
    recuperadas = trabajos.iniciar_proceso(motor, "programador")
    programador.add_job(tarea, CronTrigger.from_crontab(args.cron, timezone=ZONA_HORARIA),
                        args=[programador, motor, args.origen, args.reentrenar], id="ciclo", max_instances=1,
                        coalesce=True, misfire_grace_time=900)
    programador.add_job(vigilar, "interval", seconds=SEGUNDOS_VIGILANCIA,
                        args=[programador, motor, args.origen, args.reentrenar], id="vigilancia", max_instances=1,
                        coalesce=True)
    reanudados = reanudar(programador, motor, recuperadas, args.origen, args.reentrenar)
    if args.ahora and not reanudados:
        programador.add_job(tarea, "date", run_date=datetime.now(timezone.utc),
                            args=[programador, motor, args.origen, args.reentrenar], id="ciclo-inicial")
    log("programador").info("programador_iniciado", cron=args.cron, origen=args.origen, reentrenar=args.reentrenar,
                            reanudados=reanudados)
    try:
        programador.start()
    finally:
        trabajos.detener_proceso()
        trazas.apagar()


if __name__ == "__main__":
    main()
