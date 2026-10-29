from unittest import mock

import httpx
import pytest

from frecuencias.alertas import notificaciones as n

CORREO = {"NOTIFICACION_SMTP_HOST": "smtp.prueba.local", "NOTIFICACION_SMTP_REMITENTE": "alertas@prueba.local",
          "NOTIFICACION_SMTP_USUARIO": "cuenta", "NOTIFICACION_SMTP_CLAVE": "clave-de-prueba"}
WHATSAPP = {"NOTIFICACION_WHATSAPP_TOKEN": "token-de-prueba", "NOTIFICACION_WHATSAPP_TELEFONO_ID": "123"}
ALERTA = {"codigo": "ALT-002", "severidad": "critica", "id_ruta": 16, "explicacion": "Ocupacion 95 %.",
          "recomendacion": "Despacho adicional."}


@pytest.fixture(autouse=True)
def entorno_limpio():
    claves = [k for k in list(CORREO) + list(WHATSAPP) + ["NOTIFICACION_WHATSAPP_PLANTILLA"]]
    with mock.patch.dict("os.environ", {k: "" for k in claves}):
        for adaptador in n.ADAPTADORES.values():
            adaptador.interruptor.fallos, adaptador.interruptor.abierto_desde = 0, None
        yield


def test_sin_credenciales_el_envio_se_simula():
    assert n.AdaptadorCorreo().enviar("ops@prueba.local", "asunto", "mensaje") == ("simulada", 1)
    assert n.AdaptadorWhatsApp().enviar("573000000000", "asunto", "mensaje") == ("simulada", 1)


def test_correo_real_usa_starttls_y_login():
    with mock.patch.dict("os.environ", CORREO), mock.patch.object(n.smtplib, "SMTP") as smtp:
        estado = n.AdaptadorCorreo().enviar("ops@prueba.local", "[CRITICA] ALT-002", "Ocupacion 95 %.")
    sesion = smtp.return_value.__enter__.return_value
    assert estado == ("enviada", 1)
    assert smtp.call_args.args == ("smtp.prueba.local", 587) and smtp.call_args.kwargs["timeout"] == n.TIEMPO_ESPERA_S
    sesion.starttls.assert_called_once()
    sesion.login.assert_called_once_with("cuenta", "clave-de-prueba")
    enviado = sesion.send_message.call_args.args[0]
    assert enviado["To"] == "ops@prueba.local" and enviado["Subject"] == "[CRITICA] ALT-002"


def test_correo_por_puerto_465_usa_tls_implicito():
    with mock.patch.dict("os.environ", {**CORREO, "NOTIFICACION_SMTP_PUERTO": "465"}), \
            mock.patch.object(n.smtplib, "SMTP_SSL") as smtp_ssl, mock.patch.object(n.smtplib, "SMTP") as smtp:
        assert n.AdaptadorCorreo().enviar("ops@prueba.local", "a", "m") == ("enviada", 1)
    smtp.assert_not_called()
    assert smtp_ssl.call_args.kwargs["context"].verify_mode == n.ssl.CERT_REQUIRED
    smtp_ssl.return_value.__enter__.return_value.starttls.assert_not_called()


def test_correo_reintenta_errores_transitorios():
    with mock.patch.dict("os.environ", CORREO), mock.patch.object(n.smtplib, "SMTP") as smtp, \
            mock.patch("frecuencias.resiliencia.time.sleep"):
        smtp.side_effect = [OSError("caida"), mock.MagicMock()]
        assert n.AdaptadorCorreo().enviar("ops@prueba.local", "a", "m") == ("enviada", 2)


def _respuesta(codigo):
    return httpx.Response(codigo, request=httpx.Request("POST", "https://graph.facebook.com/v21.0/123/messages"),
                          json={"error": {"message": "x"}} if codigo >= 400 else {"messages": [{"id": "1"}]})


def test_whatsapp_real_envia_texto_con_token():
    with mock.patch.dict("os.environ", WHATSAPP), mock.patch.object(n.httpx, "post", return_value=_respuesta(200)) as post:
        assert n.AdaptadorWhatsApp().enviar("573000000000", "[CRITICA] ALT-002", "Ocupacion 95 %.") == ("enviada", 1)
    assert post.call_args.args[0] == "https://graph.facebook.com/v21.0/123/messages"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer token-de-prueba"
    cuerpo = post.call_args.kwargs["json"]
    assert cuerpo["type"] == "text" and cuerpo["to"] == "573000000000" and "ALT-002" in cuerpo["text"]["body"]


def test_whatsapp_con_plantilla():
    with mock.patch.dict("os.environ", {**WHATSAPP, "NOTIFICACION_WHATSAPP_PLANTILLA": "alerta_operativa"}):
        cuerpo = n.AdaptadorWhatsApp().cuerpo("573000000000", "asunto", "mensaje")
    assert cuerpo["type"] == "template" and cuerpo["template"]["name"] == "alerta_operativa"


def test_whatsapp_no_reintenta_un_rechazo_4xx_y_si_un_5xx():
    with mock.patch.dict("os.environ", WHATSAPP), mock.patch("frecuencias.resiliencia.time.sleep"):
        with mock.patch.object(n.httpx, "post", return_value=_respuesta(400)) as post:
            with pytest.raises(ValueError, match="HTTP 400"):
                n.AdaptadorWhatsApp().enviar("573000000000", "a", "m")
            assert post.call_count == 1
        with mock.patch.object(n.httpx, "post", side_effect=[_respuesta(503), _respuesta(200)]) as post:
            assert n.AdaptadorWhatsApp().enviar("573000000000", "a", "m") == ("enviada", 2)


def test_notificar_registra_fallos_sin_interrumpir():
    conn = mock.MagicMock()
    entorno = {**WHATSAPP, "NOTIFICACION_WHATSAPP": "573000000000", "NOTIFICACION_CANALES": "whatsapp"}
    with mock.patch.dict("os.environ", entorno), mock.patch("frecuencias.resiliencia.time.sleep"), \
            mock.patch.object(n.httpx, "post", return_value=_respuesta(401)):
        assert n.notificar(conn, "id-alerta", ALERTA) == 1
    fila = conn.execute.call_args.args[0].compile().params
    assert fila["estado"] == "fallida" and "HTTP 401" in fila["error"] and fila["enviada_en"] is None


def test_solo_se_notifican_las_severidades_configuradas():
    conn = mock.MagicMock()
    assert n.notificar(conn, "id", {**ALERTA, "severidad": "preventiva"}) == 0
    conn.execute.assert_not_called()
