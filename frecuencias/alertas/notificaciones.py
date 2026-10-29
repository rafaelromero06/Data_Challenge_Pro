import os
import smtplib
import ssl
from email.message import EmailMessage

import httpx
from sqlalchemy import insert
from sqlalchemy.engine import Connection

from frecuencias.observabilidad import NOTIFICACIONES, log
from frecuencias.persistencia.tablas import notificacion
from frecuencias.resiliencia import Interruptor, reintentar
from frecuencias.tiempo import ahora_utc

TIEMPO_ESPERA_S = float(os.environ.get("NOTIFICACION_TIMEOUT_S", "10"))
INTENTOS = int(os.environ.get("NOTIFICACION_INTENTOS", "3"))
URL_WHATSAPP = "https://graph.facebook.com/{version}/{telefono}/messages"
PUERTO_TLS_IMPLICITO = 465
LARGO_MAXIMO_WHATSAPP = 4096


class Adaptador:
    canal = ""
    variable_destino = ""
    errores_transitorios: tuple = (OSError,)

    def __init__(self):
        self.interruptor = Interruptor(f"notificacion_{self.canal}")

    def destinatario(self) -> str | None:
        return os.environ.get(self.variable_destino) or None

    def configurado(self) -> bool:
        return False

    def enviar_real(self, destinatario: str, asunto: str, mensaje: str) -> None:
        raise NotImplementedError

    def enviar(self, destinatario: str, asunto: str, mensaje: str) -> tuple[str, int]:
        if not self.configurado():
            log("notificaciones").info("envio_simulado", canal=self.canal, destinatario=destinatario, asunto=asunto)
            return "simulada", 1
        _, intentos = reintentar(lambda: self.interruptor.llamar(self.enviar_real, destinatario, asunto, mensaje),
                                 intentos=INTENTOS, excepciones=self.errores_transitorios)
        return "enviada", intentos


class AdaptadorCorreo(Adaptador):
    canal = "correo"
    variable_destino = "NOTIFICACION_CORREO"
    errores_transitorios = (OSError, smtplib.SMTPException)

    def configurado(self) -> bool:
        return bool(os.environ.get("NOTIFICACION_SMTP_HOST") and os.environ.get("NOTIFICACION_SMTP_REMITENTE"))

    def enviar_real(self, destinatario: str, asunto: str, mensaje: str) -> None:
        correo = EmailMessage()
        correo["From"] = os.environ["NOTIFICACION_SMTP_REMITENTE"]
        correo["To"] = destinatario
        correo["Subject"] = asunto
        correo.set_content(mensaje)
        puerto = int(os.environ.get("NOTIFICACION_SMTP_PUERTO", "587"))
        contexto = ssl.create_default_context()
        servidor = os.environ["NOTIFICACION_SMTP_HOST"]
        if puerto == PUERTO_TLS_IMPLICITO:
            with smtplib.SMTP_SSL(servidor, puerto, timeout=TIEMPO_ESPERA_S, context=contexto) as smtp:
                self._entregar(smtp, correo)
        else:
            with smtplib.SMTP(servidor, puerto, timeout=TIEMPO_ESPERA_S) as smtp:
                smtp.starttls(context=contexto)
                self._entregar(smtp, correo)

    @staticmethod
    def _entregar(smtp: smtplib.SMTP, correo: EmailMessage) -> None:
        usuario = os.environ.get("NOTIFICACION_SMTP_USUARIO")
        if usuario:
            smtp.login(usuario, os.environ.get("NOTIFICACION_SMTP_CLAVE", ""))
        smtp.send_message(correo)


class AdaptadorWhatsApp(Adaptador):
    canal = "whatsapp"
    variable_destino = "NOTIFICACION_WHATSAPP"
    errores_transitorios = (OSError, httpx.TransportError, httpx.HTTPStatusError)

    def configurado(self) -> bool:
        return bool(os.environ.get("NOTIFICACION_WHATSAPP_TOKEN") and os.environ.get("NOTIFICACION_WHATSAPP_TELEFONO_ID"))

    def cuerpo(self, destinatario: str, asunto: str, mensaje: str) -> dict:
        plantilla = os.environ.get("NOTIFICACION_WHATSAPP_PLANTILLA")
        if plantilla:
            return {"messaging_product": "whatsapp", "to": destinatario, "type": "template",
                    "template": {"name": plantilla, "language": {"code": os.environ.get("NOTIFICACION_WHATSAPP_IDIOMA", "es")},
                                 "components": [{"type": "body", "parameters": [
                                     {"type": "text", "text": asunto}, {"type": "text", "text": mensaje[:1000]}]}]}}
        return {"messaging_product": "whatsapp", "to": destinatario, "type": "text",
                "text": {"body": f"{asunto}\n{mensaje}"[:LARGO_MAXIMO_WHATSAPP]}}

    def enviar_real(self, destinatario: str, asunto: str, mensaje: str) -> None:
        url = URL_WHATSAPP.format(version=os.environ.get("NOTIFICACION_WHATSAPP_VERSION", "v21.0"),
                                  telefono=os.environ["NOTIFICACION_WHATSAPP_TELEFONO_ID"])
        respuesta = httpx.post(url, json=self.cuerpo(destinatario, asunto, mensaje), timeout=TIEMPO_ESPERA_S,
                               headers={"Authorization": f"Bearer {os.environ['NOTIFICACION_WHATSAPP_TOKEN']}"})
        if respuesta.status_code == 429 or respuesta.status_code >= 500:
            respuesta.raise_for_status()
        if respuesta.is_error:
            raise ValueError(f"WhatsApp rechazo el mensaje (HTTP {respuesta.status_code}): {respuesta.text[:200]}")


ADAPTADORES = {"correo": AdaptadorCorreo(), "whatsapp": AdaptadorWhatsApp()}


def canales_activos() -> list[Adaptador]:
    nombres = os.environ.get("NOTIFICACION_CANALES", "correo,whatsapp").split(",")
    return [ADAPTADORES[n.strip()] for n in nombres if n.strip() in ADAPTADORES]


def severidades_notificadas() -> set[str]:
    return {s.strip() for s in os.environ.get("NOTIFICACION_SEVERIDADES", "critica").split(",") if s.strip()}


def notificar(conn: Connection, id_alerta, alerta: dict) -> int:
    if alerta["severidad"] not in severidades_notificadas():
        return 0
    asunto = f"[{alerta['severidad'].upper()}] {alerta['codigo']} ruta {alerta.get('id_ruta') or 'todas'}"
    mensaje = f"{alerta['explicacion']} Recomendacion: {alerta['recomendacion']}"
    enviadas = 0
    for adaptador in canales_activos():
        destinatario = adaptador.destinatario()
        if not destinatario:
            continue
        try:
            (estado, intentos), error = adaptador.enviar(destinatario, asunto, mensaje), None
        except Exception as fallo:
            estado, intentos, error = "fallida", INTENTOS, f"{type(fallo).__name__}: {fallo}"[:500]
            log("notificaciones").warning("envio_fallido", canal=adaptador.canal, error=type(fallo).__name__)
        NOTIFICACIONES.labels(adaptador.canal, estado).inc()
        conn.execute(insert(notificacion).values(
            alert_id=id_alerta, canal=adaptador.canal, destinatario=destinatario, asunto=asunto, mensaje=mensaje,
            estado=estado, intentos=intentos, error=error, enviada_en=ahora_utc() if estado == "enviada" else None))
        enviadas += 1
    return enviadas
