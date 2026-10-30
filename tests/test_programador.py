from unittest import mock

from sqlalchemy.exc import OperationalError

from frecuencias.operacion import ciclo, programador

CAIDA = OperationalError("select", {}, Exception("la fuente no responde"))


def test_ciclo_programado_exitoso():
    agenda = mock.MagicMock()
    with mock.patch.object(ciclo, "iniciar", return_value={"id": 5, "intento": 1}), \
            mock.patch.object(ciclo, "ejecutar_ciclo", return_value={"execution_id": 5, "operacion": {}}) as correr:
        programador.tarea(agenda, "motor", "base", "auto")
    correr.assert_called_once_with("motor", "base", 1, "auto", {"id": 5, "intento": 1})
    agenda.add_job.assert_not_called()


def test_ciclo_en_curso_se_omite_sin_reintento():
    agenda = mock.MagicMock()
    with mock.patch.object(ciclo, "iniciar", side_effect=ciclo.CicloEnCurso("otro")), \
            mock.patch.object(ciclo, "ejecutar_ciclo") as correr:
        programador.tarea(agenda, "motor", "base", "auto")
    correr.assert_not_called()
    agenda.add_job.assert_not_called()


def test_fallo_transitorio_programa_reintento_con_espera():
    agenda = mock.MagicMock()
    with mock.patch.object(ciclo, "iniciar", return_value={"id": 5, "intento": 1}), \
            mock.patch.object(ciclo, "ejecutar_ciclo", side_effect=CAIDA):
        programador.tarea(agenda, "motor", "base", "auto")
    agenda.add_job.assert_called_once()
    assert agenda.add_job.call_args.kwargs["id"] == "reintento-5"
    assert agenda.add_job.call_args.kwargs["args"][-1] == 5


def test_fallo_permanente_o_ultimo_intento_no_se_reintenta():
    agenda = mock.MagicMock()
    with mock.patch.object(ciclo, "iniciar", return_value={"id": 5, "intento": 1}), \
            mock.patch.object(ciclo, "ejecutar_ciclo", side_effect=ValueError("dato")):
        programador.tarea(agenda, "motor", "base", "auto")
    with mock.patch.object(ciclo, "preparar_reintento",
                           return_value=({"id": 6, "intento": ciclo.MAX_INTENTOS}, {"origen": "base", "reentrenar": "auto"})), \
            mock.patch.object(ciclo, "ejecutar_ciclo", side_effect=CAIDA):
        programador.tarea(agenda, "motor", "base", "auto", reintento_de=5)
    agenda.add_job.assert_not_called()


def test_reintento_usa_los_parametros_del_ciclo_original():
    agenda = mock.MagicMock()
    with mock.patch.object(ciclo, "preparar_reintento",
                           return_value=({"id": 6, "intento": 2}, {"origen": "archivos", "reentrenar": "nunca"})), \
            mock.patch.object(ciclo, "ejecutar_ciclo", return_value={"execution_id": 6}) as correr:
        programador.tarea(agenda, "motor", "base", "auto", reintento_de=5)
    correr.assert_called_once_with("motor", "archivos", 1, "nunca", {"id": 6, "intento": 2})


def test_no_iniciar_el_reintento_no_detiene_el_programador():
    with mock.patch.object(ciclo, "preparar_reintento", side_effect=ValueError("ya exitosa")), \
            mock.patch.object(ciclo, "ejecutar_ciclo") as correr:
        programador.tarea(mock.MagicMock(), "motor", "base", "auto", reintento_de=5)
    correr.assert_not_called()


def test_vigilancia_reanuda_ciclos_interrumpidos():
    agenda = mock.MagicMock()
    recuperadas = [{"id": 9, "tipo": "ciclo", "intento": 1}, {"id": 10, "tipo": "ingesta", "intento": 1}]
    with mock.patch.object(programador.trabajos, "recuperar", return_value=recuperadas):
        programador.vigilar(agenda, "motor", "base", "auto")
    agenda.add_job.assert_called_once()
    assert agenda.add_job.call_args.kwargs["id"] == "reintento-9"
    with mock.patch.object(programador.trabajos, "recuperar", side_effect=CAIDA):
        programador.vigilar(agenda, "motor", "base", "auto")
