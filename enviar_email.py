# -*- coding: utf-8 -*-
"""Envía salida/informe.html por correo, con la gráfica dentro del mensaje.

Configuración (GitHub → Settings → Secrets and variables → Actions → Secrets):
  SMTP_USUARIO     cuenta que envía, p. ej. tuamigoinversor@gmail.com
  SMTP_CLAVE       contraseña de aplicación de Gmail (no la contraseña normal)
  EMAIL_DESTINO    destinatarios separados por comas; van en copia oculta (CCO)
Opcionales: SMTP_SERVIDOR (smtp.gmail.com) y SMTP_PUERTO (465).
Si falta alguno de los tres obligatorios, no envía nada y no da error.
"""
import os
import smtplib
import sys
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

SALIDA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "salida")

usuario = os.environ.get("SMTP_USUARIO", "").strip()
clave = os.environ.get("SMTP_CLAVE", "").strip()
destino = [d.strip() for d in os.environ.get("EMAIL_DESTINO", "").split(",") if d.strip()]
if not (usuario and clave and destino):
    print("ℹ️ Correo no configurado (faltan SMTP_USUARIO, SMTP_CLAVE o EMAIL_DESTINO): no se envía.")
    sys.exit(0)

with open(os.path.join(SALIDA, "informe.html"), encoding="utf-8") as f:
    html = f.read()
with open(os.path.join(SALIDA, "asunto.txt"), encoding="utf-8") as f:
    asunto = f.read().strip()

msg = EmailMessage()
msg["Subject"] = asunto
msg["From"] = usuario
msg["To"] = usuario                      # los destinatarios van en CCO: no se ven entre ellos
msg["Bcc"] = ", ".join(destino)
msg["Date"] = formatdate(localtime=True)
msg["Message-ID"] = make_msgid()
msg.set_content("Tu programa de correo no muestra HTML. Abre el informe en GitHub.")
msg.add_alternative(html, subtype="html")

png = os.path.join(SALIDA, "geda_mes_actual.png")
if os.path.exists(png):
    with open(png, "rb") as f:
        msg.get_payload()[1].add_related(f.read(), maintype="image", subtype="png", cid="<grafica>")

servidor = os.environ.get("SMTP_SERVIDOR", "smtp.gmail.com")
puerto = int(os.environ.get("SMTP_PUERTO", "465"))
with smtplib.SMTP_SSL(servidor, puerto, timeout=60) as s:
    s.login(usuario, clave)
    s.send_message(msg)
print(f"📧 Enviado a {len(destino)} destinatario(s): {asunto}")
