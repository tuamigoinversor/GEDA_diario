# -*- coding: utf-8 -*-
"""
ROCA · IMPULSO · CRYPTO — seguimiento diario conjunto (GitHub Actions o local)
==============================================================================
Carteras de pesos fijos: no hay previsión del mes siguiente. Qué saca cada día:
  1. Resumen: los 3 últimos meses cerrados y el mes en curso (provisional) de las tres, frente al MSCI World.
  2. Un gráfico día a día del mes en curso por estrategia (cartera, cada activo y MSCI World), en EUR.
  3. informe_fijas/README.md + gráfico (se ve directamente en GitHub), salida/ con HTML y CSV, y correo.

Cálculo: cada mes parte de los pesos objetivo (como lo publicado) y mantiene sin rebalancear hasta fin de mes.
Rentabilidades en EUR con el tipo de cambio de cada día. Precios de Yahoo (motor_genz.load_prices).

Códigos de salida: 0 = bien · 2 = Yahoo ha fallado (reintenta) · 3 = sin correo · 1 = otro error.
"""
import base64
import io
import os
import smtplib
import sys
import traceback
import warnings
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates          # noqa: E402
import matplotlib.pyplot as plt            # noqa: E402
import matplotlib.ticker as mtick          # noqa: E402
import numpy as np                         # noqa: E402
import pandas as pd                        # noqa: E402

import motor_genz as M                     # noqa: E402

# =========================================================
# CONFIGURACIÓN — lo único que deberías tocar
# =========================================================
ESTRATEGIAS = {
    "ROCA": {"perfil": "Conservador", "pesos": {
        "VIPSX": ("RF Inflación", 0.20), "IAU": ("Oro", 0.15), "MIXAX": ("RF Corto Plazo", 0.25),
        "DODLX": ("RF Global", 0.15), "VSIAX": ("RV USA Small Cap", 0.10), "VHGEX": ("RV Global", 0.15)}},
    "IMPULSO": {"perfil": "Moderado", "pesos": {
        "IAU": ("Oro", 0.40), "VSIAX": ("RV Americana", 0.30), "VHGEX": ("RV Global", 0.30)}},
    "CRYPTO": {"perfil": "Especulativo", "pesos": {
        "BTC-USD": ("Bitcoin", 0.50), "ETH-USD": ("Ethereum", 0.50)}},
}
REFERENCIA = ("MSCI World (URTH, EUR)", "URTH")
MESES_CERRADOS = 3

CARPETA_DATOS = "datos"
CARPETA_SALIDA = "salida"
CARPETA_INFORME = "informe_fijas"
DIAS_DATOS_ATRASADOS = 4

M.VERBOSE = False
M.RUTA_CACHE_DIVISAS = os.path.join(CARPETA_DATOS, "divisas_cache.json")

MESES_LARGO = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
               "septiembre", "octubre", "noviembre", "diciembre"]


class YahooRoto(Exception):
    """Datos incompletos de Yahoo: hay que reintentar más tarde."""


def etiqueta(m):
    return f"{M.MESES_ES[m.month - 1].lower()}-{str(m.year)[2:]}"


def pct(x, dec=2, signo=False):
    if x is None or pd.isna(x):
        return "—"
    s = f"{x*100:+.{dec}f}" if signo else f"{x*100:.{dec}f}"
    return s.replace(".", ",") + " %"


def color(x):
    return "#999999" if x is None or pd.isna(x) else ("#0a7d2c" if x >= 0 else "#c62828")


# =========================================================
# CÁLCULO
# =========================================================
def precios(tickers, desde, hasta):
    p = M.load_prices(list(tickers), desde, hasta, convert_to_eur=True)
    faltan = [t for t in tickers if t not in p.columns or p[t].dropna().empty]
    if faltan:
        raise YahooRoto(f"Sin precios en Yahoo: {faltan}")
    return p[list(tickers)].sort_index().ffill()


def rentab_mes(p, pesos, mes, hasta=None):
    """Compra a pesos objetivo al cierre del mes anterior y mantiene hasta fin de mes (o hasta `hasta`)."""
    ini = mes.replace(day=1)
    antes = p[p.index < ini]
    dentro = p[(p.index >= ini) & (p.index <= (hasta if hasta is not None else mes))]
    if antes.empty or dentro.empty:
        return np.nan, None
    w = pd.Series(pesos)
    base = antes.iloc[-1][w.index]
    rel = dentro[w.index] / base - 1
    rel.loc[antes.index[-1]] = 0.0
    rel = rel.sort_index()
    cartera = (rel * w).sum(axis=1)
    return float(cartera.iloc[-1]), (cartera, rel)


# =========================================================
# GRÁFICO (un panel por estrategia)
# =========================================================
def grafico(curvas, ref, mes, ruta_png):
    n = len(curvas)
    fig, axes = plt.subplots(n, 1, figsize=(11, 4.2 * n))
    axes = np.atleast_1d(axes)
    for ax, (nombre, (cartera, rel, nombres)) in zip(axes, curvas.items()):
        for t in rel.columns:
            ax.plot(rel.index, rel[t] * 100, lw=1, alpha=0.75,
                    label=f"{nombres[t]} ({t})  {pct(rel[t].iloc[-1], signo=True)}")
        ax.plot(cartera.index, cartera * 100, color="black", lw=2.8, label=f"{nombre}  {pct(cartera.iloc[-1], signo=True)}")
        if ref is not None:
            ax.plot(ref.index, ref * 100, color="grey", ls="--", lw=1.4,
                    label=f"{REFERENCIA[0]}  {pct(ref.iloc[-1], signo=True)}")
        ax.axhline(0, color="#999999", lw=0.8)
        dentro = cartera[cartera.index >= mes.replace(day=1)]
        if len(dentro):
            for f, v, arriba in ((dentro.idxmax(), dentro.max(), True), (dentro.idxmin(), dentro.min(), False)):
                ax.scatter([f], [v * 100], color="black", zorder=5, s=18)
                ax.annotate(pct(v, signo=True), (f, v * 100), textcoords="offset points",
                            xytext=(0, 6 if arriba else -12), ha="center", fontsize=9)
        ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda y, _: f"{y:+.0f} %".replace("+0 ", "0 ")))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d"))
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
        ax.set_title(f"{nombre} · {MESES_LARGO[mes.month - 1]} de {mes.year} · día a día (EUR)", loc="left", fontsize=12)
        ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=8, frameon=False)
        ax.grid(alpha=0.25)
    axes[-1].set_xlabel(f"día de {MESES_LARGO[mes.month - 1]} (el primer punto es el cierre del mes anterior = 0 %; "
                        f"cada mes parte de los pesos objetivo)", fontsize=8)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    png = buf.getvalue()
    with open(ruta_png, "wb") as f:
        f.write(png)
    return png


def frase(nombre, cartera, mes):
    c = cartera[cartera.index >= mes.replace(day=1)]
    if c.empty:
        return f"{nombre}: todavía no hay cierres de este mes."
    mx, dmx, mn, dmn = c.max(), c.idxmax(), c.min(), c.idxmin()
    return (f"{nombre}: ha llegado a ir {pct(mx, signo=True)} el {dmx.day} y su peor momento ha sido "
            f"{pct(mn, signo=True)} el {dmn.day}. Va en {pct(c.iloc[-1], signo=True)}.")


# =========================================================
# CORREO
# =========================================================
SECRETOS = {"SMTP_USER": ("SMTP_USER", "SMTP_USUARIO", "EMAIL_USER"),
            "SMTP_PASSWORD": ("SMTP_PASSWORD", "SMTP_CLAVE", "EMAIL_PASS"),
            "MAIL_TO": ("MAIL_TO", "EMAIL_DESTINO", "EMAIL_TO")}


def _secreto(clave):
    return next((os.environ[k] for k in SECRETOS[clave] if os.environ.get(k)), "")


def enviar_correo(asunto, html_cid, png):
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    puerto = int(os.environ.get("SMTP_PORT", "465"))
    usuario, clave, para = _secreto("SMTP_USER"), _secreto("SMTP_PASSWORD"), _secreto("MAIL_TO")
    msg = MIMEMultipart("related")
    msg["Subject"], msg["From"], msg["To"] = asunto, usuario, para
    msg.attach(MIMEText(html_cid, "html", "utf-8"))
    img = MIMEImage(png, "png")
    img.add_header("Content-ID", "<grafico>")
    img.add_header("Content-Disposition", "inline", filename="grafico.png")
    msg.attach(img)
    with smtplib.SMTP_SSL(host, puerto, timeout=60) as s:
        s.login(usuario, clave)
        s.sendmail(usuario, [x.strip() for x in para.split(",")], msg.as_string())


# =========================================================
# PROGRAMA
# =========================================================
def main():
    hoy = M._hoy()
    mes_actual = hoy + pd.offsets.MonthEnd(0)
    ultimo_cerrado = hoy.replace(day=1) - pd.offsets.MonthEnd(1)
    primer_mes = ultimo_cerrado - pd.offsets.MonthEnd(MESES_CERRADOS - 1)
    cerrados = list(pd.date_range(primer_mes, ultimo_cerrado, freq="ME"))
    meses = cerrados + [mes_actual]
    desde = primer_mes.replace(day=1) - pd.Timedelta(days=20)
    hasta = hoy + pd.Timedelta(days=1)
    os.makedirs(os.path.join(CARPETA_SALIDA, "historico"), exist_ok=True)
    os.makedirs(CARPETA_INFORME, exist_ok=True)

    tickers = sorted({t for e in ESTRATEGIAS.values() for t in e["pesos"]} | {REFERENCIA[1]})
    with warnings.catch_warnings(record=True) as capt:
        warnings.simplefilter("always")
        p = precios(tickers, desde, hasta)
    avisos = sorted({str(w.message) for w in capt if issubclass(w.category, UserWarning)})

    tabla, curvas, detalle = {}, {}, {}
    for nombre, e in list(ESTRATEGIAS.items()) + [(REFERENCIA[0], {"pesos": {REFERENCIA[1]: ("", 1.0)}})]:
        pesos = {t: w for t, (_, w) in e["pesos"].items()}
        if abs(sum(pesos.values()) - 1) > 1e-9:
            raise ValueError(f"{nombre}: los pesos suman {sum(pesos.values()):.4f}, no 1.")
        tabla[nombre] = {m: rentab_mes(p, pesos, m, hoy if m == mes_actual else None)[0] for m in meses}
        _, (cartera, rel) = rentab_mes(p, pesos, mes_actual, hoy)
        if nombre in ESTRATEGIAS:
            nombres = {t: n for t, (n, _) in e["pesos"].items()}
            curvas[nombre] = (cartera, rel, nombres)
            detalle[nombre] = [(nombres[t], t, pesos[t], rel[t].iloc[-1]) for t in pesos]
            ultimo = p[list(pesos)].dropna(how="all").index[-1]
            if (hoy - ultimo).days > DIAS_DATOS_ATRASADOS:
                avisos.append(f"{nombre}: último precio del {ultimo.date()}. ¿Festivo o datos atrasados?")
        else:
            ref_curva = cartera
    ultimo_dato = max(c.index[-1] for c, _, _ in curvas.values())

    ruta_png = os.path.join(CARPETA_SALIDA, f"FIJAS_grafico_diario_{mes_actual.strftime('%Y-%m')}.png")
    png = grafico(curvas, ref_curva, mes_actual, ruta_png)
    with open(os.path.join(CARPETA_INFORME, "fijas_mes_actual.png"), "wb") as f:
        f.write(png)

    acum = {n: float(np.prod([1 + tabla[n][m] for m in cerrados]) - 1) for n in tabla}
    nombres_tabla = list(ESTRATEGIAS) + [REFERENCIA[0]]
    est_mes = lambda m: "provisional" if m == mes_actual else "cerrado"   # noqa: E731

    # --- HTML ---
    filas = "".join(
        f"<tr><td><b>{etiqueta(m)}</b> ({est_mes(m)})</td>"
        + "".join(f"<td style='text-align:right;color:{color(tabla[n][m])};font-weight:bold'>{pct(tabla[n][m])}</td>"
                  for n in nombres_tabla) + "</tr>" for m in meses)
    filas += ("<tr style='background:#eef3ff'><td><b>Acumulado meses cerrados</b></td>"
              + "".join(f"<td style='text-align:right'>{pct(acum[n])}</td>" for n in nombres_tabla) + "</tr>")
    html_tabla = ("<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;font-size:13px'>"
                  "<tr style='background:#f2f2f2'><th>Mes</th>" + "".join(f"<th>{n}</th>" for n in nombres_tabla)
                  + f"</tr>{filas}</table>")
    html_det = ""
    for nombre, filas_d in detalle.items():
        html_det += (f"<h3>{nombre} · {ESTRATEGIAS[nombre]['perfil']}</h3><p>{frase(nombre, curvas[nombre][0], mes_actual)}</p>"
                     "<table border='1' cellpadding='5' cellspacing='0' style='border-collapse:collapse;font-size:13px'>"
                     "<tr style='background:#f2f2f2'><th>Activo</th><th>Ticker</th><th>Peso</th><th>Rentab. del mes</th></tr>"
                     + "".join(f"<tr><td>{a}</td><td>{t}</td><td style='text-align:right'>{pct(w, 0)}</td>"
                               f"<td style='text-align:right;color:{color(r)};font-weight:bold'>{pct(r, signo=True)}</td></tr>"
                               for a, t, w, r in filas_d) + "</table>")
    caja = ("" if not avisos else "<div style='border:1px solid #e0a800;background:#fff8e1;padding:8px 12px'>"
            "<b>⚠️ Avisos</b><ul>" + "".join(f"<li>{a}</li>" for a in avisos) + "</ul></div>")
    cabecera = " · ".join(f"{n} {pct(tabla[n][mes_actual], signo=True)}" for n in nombres_tabla)

    def documento(src):
        return f"""<html><head><meta charset="utf-8"><title>ROCA · IMPULSO · CRYPTO {hoy.date()}</title></head>
<body style="font-family:Arial,Helvetica,sans-serif;max-width:1100px;margin:auto;color:#222">
<h1 style="margin-bottom:2px">ROCA · IMPULSO · CRYPTO · seguimiento diario</h1>
<p style="color:#555;margin-top:0">Ejecutado el {pd.Timestamp.now(tz='Europe/Madrid'):%d-%m-%Y %H:%M} (Madrid) ·
último cierre con datos: {ultimo_dato:%d-%m-%Y} · rentabilidades en EUR</p>
<p><b>{etiqueta(mes_actual)} (provisional):</b> {cabecera}</p>{caja}
<h2>1. Resumen</h2>{html_tabla}
<h2>2. {MESES_LARGO[mes_actual.month - 1].capitalize()} de {mes_actual.year}, día a día</h2>
<img src="{src}" style="max-width:100%">{html_det}
<hr><p style="font-size:11px;color:#777">Cada mes parte de los pesos objetivo y no rebalancea dentro del mes.
Precios de Yahoo; puede haber pequeñas diferencias con los valores liquidativos definitivos de los fondos.
Uso interno: no es una recomendación de inversión.</p></body></html>"""

    html = documento(f"data:image/png;base64,{base64.b64encode(png).decode()}")
    for ruta in (os.path.join(CARPETA_SALIDA, "FIJAS_hoy.html"),
                 os.path.join(CARPETA_SALIDA, "historico", f"FIJAS_{hoy.date()}.html")):
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(html)

    # --- README visible en GitHub ---
    L = ["# ROCA · IMPULSO · CRYPTO · seguimiento diario",
         f"Ejecutado el {pd.Timestamp.now(tz='Europe/Madrid'):%d-%m-%Y %H:%M} (Madrid) · último cierre con datos: "
         f"{ultimo_dato:%d-%m-%Y} · rentabilidades en EUR", "", f"**{etiqueta(mes_actual)} (provisional):** {cabecera}", ""]
    if avisos:
        L += ["> ⚠️ **Avisos**"] + [f"> - {a}" for a in avisos] + [""]
    L += ["## 1. Resumen", "", "| Mes | " + " | ".join(nombres_tabla) + " |", "|---|" + "---:|" * len(nombres_tabla)]
    L += [f"| **{etiqueta(m)}** ({est_mes(m)}) | " + " | ".join(f"**{pct(tabla[n][m])}**" for n in nombres_tabla) + " |"
          for m in meses]
    L += ["| **Acumulado meses cerrados** | " + " | ".join(pct(acum[n]) for n in nombres_tabla) + " |", "",
          f"## 2. {MESES_LARGO[mes_actual.month - 1].capitalize()} de {mes_actual.year}, día a día", "",
          "![Gráfico diario](fijas_mes_actual.png)", ""]
    for nombre, filas_d in detalle.items():
        L += [f"### {nombre} · {ESTRATEGIAS[nombre]['perfil']}", "", frase(nombre, curvas[nombre][0], mes_actual), "",
              "| Activo | Ticker | Peso | Rentab. del mes |", "|---|---|---:|---:|"]
        L += [f"| {a} | {t} | {pct(w, 0)} | {pct(r, signo=True)} |" for a, t, w, r in filas_d] + [""]
    L += ["_Cada mes parte de los pesos objetivo y no rebalancea dentro del mes. Uso interno: no es una recomendación._"]
    with open(os.path.join(CARPETA_INFORME, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    # --- Una línea por día ---
    ruta_csv = os.path.join(CARPETA_SALIDA, "fijas_seguimiento_diario.csv")
    fila = pd.DataFrame([{"fecha": str(hoy.date()), "ultimo_cierre": str(ultimo_dato.date()),
                          "mes": mes_actual.strftime("%Y-%m"),
                          **{n.split(" ")[0].lower(): round(tabla[n][mes_actual], 6) for n in nombres_tabla}}])
    if os.path.exists(ruta_csv):
        viejo = pd.read_csv(ruta_csv, dtype=str)
        fila = pd.concat([viejo[viejo["fecha"] != str(hoy.date())], fila.astype(str)], ignore_index=True)
    fila.to_csv(ruta_csv, index=False)

    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(f"## ROCA · IMPULSO · CRYPTO {hoy.date()}\n\n- {etiqueta(mes_actual)}: {cabecera}\n"
                    + "".join(f"- ⚠️ {a}\n" for a in avisos))
    print(f"✅ {etiqueta(mes_actual)}: {cabecera}")
    for a in avisos:
        print(f"⚠️ {a}")

    if all(_secreto(k) for k in SECRETOS):
        asunto = f"ROCA · IMPULSO · CRYPTO {hoy:%d-%m} · " + " · ".join(
            f"{n} {pct(tabla[n][mes_actual], signo=True)}" for n in ESTRATEGIAS) + (" · ⚠️" if avisos else "")
        try:
            enviar_correo(asunto, documento("cid:grafico"), png)
            print("📧 Correo enviado")
        except Exception as e:  # noqa: BLE001
            print(f"⛔ No se ha podido enviar el correo: {e}")
            return 3
    else:
        print("ℹ️ Sin secretos de correo (EMAIL_USER, EMAIL_PASS, EMAIL_TO): no se envía.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except YahooRoto as e:
        print(f"⛔ YAHOO: {e}")
        sys.exit(2)
    except (RuntimeError, ValueError) as e:
        txt = str(e)
        if any(k in txt for k in ("DESCARGA ROTA", "divisa", "tipo de cambio", "Ningún ticker con datos")):
            print(f"⛔ YAHOO: {txt}")
            sys.exit(2)
        traceback.print_exc()
        sys.exit(1)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
