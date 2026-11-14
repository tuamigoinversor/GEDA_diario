# -*- coding: utf-8 -*-
"""
TODAS LAS ESTRATEGIAS — reporte conjunto diario
===============================================
Se ejecuta al final del workflow "Todas las estrategias", después de GEDA, GEN-Z, ROCA·IMPULSO·CRYPTO y
HORIZONTE. Calcula el mes en curso de las 7 carteras con una sola descarga de Yahoo y manda UN correo:
  1. Gráfico resumen arriba: todas las estrategias día a día en el mes (EUR) y el MSCI World.
  2. Tabla: rentabilidad del mes, mejor y peor momento, posiciones del mes y previsión del mes siguiente.
  3. Debajo, el gráfico de cada informe individual (si existe) y el enlace a su README en GitHub.

Posiciones: las calcula este mismo script, sin ficheros de posiciones.
  - GEDA: motor con la configuración cerrada (1 índice, 2 sectores, 2 acciones USA y 2 de Europa; señal "ambas";
    regla dinámica de 3 meses). Mes en curso = señal del cierre del mes anterior; previsión = señal con el cierre de hoy.
  - GEN-Z: motor con la versión definitiva (4 acciones, máximo 2 por tema), universo de genz_diario.py.
  - HORIZONTE (simple y avanzada): S&P 500 frente a su WMA 150 (horizonte_diario.py).
  - ROCA, IMPULSO, CRYPTO: pesos fijos de fijas_diario.py.
Necesita en datos/ las membresías (y para GEN-Z la caché de industrias), las mismas que usan GEDA y GEN-Z.
Pesos iguales en GEDA y GEN-Z. Cada mes parte de los pesos objetivo y mantiene hasta fin de mes.
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

import fijas_diario as F                   # noqa: E402
import genz_diario as G                    # noqa: E402
import horizonte_diario as H               # noqa: E402
from fijas_diario import M, YahooRoto, etiqueta, pct, color   # noqa: E402

# =========================================================
# CONFIGURACIÓN
# =========================================================
ORDEN = ["ROCA", "IMPULSO", "HORIZONTE simple", "HORIZONTE avanzada", "GEDA", "GEN-Z", "CRYPTO"]
PERFIL = {"ROCA": "Conservador", "IMPULSO": "Moderado", "HORIZONTE simple": "Moderado",
          "HORIZONTE avanzada": "Moderado", "GEDA": "Agresivo", "GEN-Z": "Muy agresivo", "CRYPTO": "Especulativo"}
COLORES = {"ROCA": "#8c8c8c", "IMPULSO": "#b8860b", "HORIZONTE simple": "#1f77b4", "HORIZONTE avanzada": "#17becf",
           "GEDA": "#d62728", "GEN-Z": "#9467bd", "CRYPTO": "#ff7f0e"}
INFORMES = {   # gráfico de cada informe individual (si existe) y su carpeta
    "GEDA": ("informe/geda_mes_actual.png", "informe"),
    "GEN-Z": ("informe_genz/genz_mes_actual.png", "informe_genz"),
    "ROCA · IMPULSO · CRYPTO": ("informe_fijas/fijas_mes_actual.png", "informe_fijas"),
    "HORIZONTE": ("informe_horizonte/horizonte_mes_actual.png", "informe_horizonte"),
}
CARPETA_DATOS, CARPETA_SALIDA, CARPETA_INFORME = "datos", "salida", "informe_conjunto"
REFERENCIA = F.REFERENCIA

# GEDA: configuración cerrada el 27-28 oct 2026 (la misma que geda_diario.py)
GEDA_SENAL, GEDA_MESES_MIN = "ambas", 3
GEDA_INDICES = ['AGG', 'DBC', 'GLD', 'VNQ', 'SPY', 'QQQ', 'IWM', 'VGK']
GEDA_SECTORES = ['XLP', 'XLV', 'XLK', 'XLE', 'XLB', 'XLI', 'XLY', 'XLC', 'XLU', 'XLF', 'XLRE']


# =========================================================
# POSICIONES CON EL MOTOR (mes en curso y previsión)
# =========================================================
def geda_cfg():
    memb_usa = G.cargar_membresias(G.FICHERO_MEMB_USA)
    memb_eur = G.cargar_membresias(G.FICHERO_MEMB_EUROPA)
    comun = {"senal": GEDA_SENAL}
    acc = {"meses_min": GEDA_MESES_MIN, "regla": "dinamica", "n": 2, **comun}
    return {"Indices": {"tickers": GEDA_INDICES, "n": 1, **comun},
            "Sectores": {"tickers": GEDA_SECTORES, "n": 2, **comun},
            "Acciones USA": {"membresias": memb_usa, **acc},
            "Acciones EUROPA": {"membresias": memb_eur, **acc}}


def posiciones_motor(nombre, cfg, mes, mes_sig, avisos):
    """Posiciones del mes en curso y del siguiente (previsión) con pesos iguales, más alertas de votos."""
    start = str((mes.replace(day=1) - pd.offsets.MonthEnd(1)).replace(day=1).date())
    start = str((pd.Timestamp(start) - pd.Timedelta(days=1)).date())
    total, _, _, sub_pos = M.run_multi_strategy(cfg, start, None, True, provisional=True)
    n_total = sum(c["n"] for c in cfg.values())
    actual, siguiente = {}, set()
    for blq, c in cfg.items():
        ts = list(sub_pos.get(blq, {}).get(mes, {}))
        if not ts:
            raise RuntimeError(f"{nombre}: el motor no da posiciones de {blq} para {etiqueta(mes)}.")
        for t in ts:
            actual[t] = actual.get(t, 0) + (c["n"] / n_total) / len(ts)
        siguiente |= set(sub_pos.get(blq, {}).get(mes_sig, {}))
    entran, salen = sorted(siguiente - set(actual)), sorted(set(actual) - siguiente)
    alertas = []
    for blq, por_mes in total.attrs.get("rankings", {}).items():
        inf = por_mes.get(mes_sig)
        a = M._alerta_margen(inf) if inf else ""
        if a:
            alertas.append(f"{blq.strip()} {a}")
    txt = (f"ENTRAN {', '.join(entran)} · SALEN {', '.join(salen)}" if entran or salen else "sin cambios previstos")
    if alertas:
        txt += " · ⚠️ " + ", ".join(alertas)
    return actual, txt


# =========================================================
# GRÁFICO RESUMEN
# =========================================================
def grafico(curvas, ref, mes, ruta_png):
    fig, ax = plt.subplots(figsize=(11, 5.6))
    for n in ORDEN:
        if n in curvas:
            c = curvas[n]
            ax.plot(c.index, c * 100, color=COLORES[n], lw=2.2 if n in ("GEDA", "GEN-Z", "CRYPTO") else 1.8,
                    label=f"{n}  {pct(c.iloc[-1], signo=True)}")
    ax.plot(ref.index, ref * 100, color="black", ls="--", lw=1.4, label=f"{REFERENCIA[0]}  {pct(ref.iloc[-1], signo=True)}")
    ax.axhline(0, color="#999999", lw=0.8)
    ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda y, _: f"{y:+.0f} %".replace("+0 ", "0 ")))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d"))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    ax.set_title(f"Todas las estrategias · {F.MESES_LARGO[mes.month - 1]} de {mes.year} · día a día (EUR)",
                 loc="left", fontsize=12)
    ax.set_xlabel(f"día de {F.MESES_LARGO[mes.month - 1]} (el primer punto es el cierre del mes anterior = 0 %)", fontsize=8)
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=9, frameon=False)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    png = buf.getvalue()
    with open(ruta_png, "wb") as f:
        f.write(png)
    return png


def enviar_correo(asunto, html, imagenes):
    usuario, clave, para = F._secreto("SMTP_USER"), F._secreto("SMTP_PASSWORD"), F._secreto("MAIL_TO")
    msg = MIMEMultipart("related")
    msg["Subject"], msg["From"], msg["To"] = asunto, usuario, para
    msg.attach(MIMEText(html, "html", "utf-8"))
    for cid, png in imagenes.items():
        img = MIMEImage(png, "png")
        img.add_header("Content-ID", f"<{cid}>")
        img.add_header("Content-Disposition", "inline", filename=f"{cid}.png")
        msg.attach(img)
    with smtplib.SMTP_SSL(os.environ.get("SMTP_HOST", "smtp.gmail.com"), int(os.environ.get("SMTP_PORT", "465")),
                          timeout=60) as s:
        s.login(usuario, clave)
        s.sendmail(usuario, [x.strip() for x in para.split(",")], msg.as_string())


# =========================================================
# PROGRAMA
# =========================================================
def main():
    hoy = M._hoy()
    mes = hoy + pd.offsets.MonthEnd(0)
    mes_sig = mes + pd.offsets.MonthEnd(1)
    hasta = hoy + pd.Timedelta(days=1)
    avisos = []
    os.makedirs(CARPETA_INFORME, exist_ok=True)

    # --- Carteras del mes ---
    carteras, fuente_pos = {}, {}
    for nombre in ("ROCA", "IMPULSO", "CRYPTO"):
        carteras[nombre] = {t: w for t, (_, w) in F.ESTRATEGIAS[nombre]["pesos"].items()}
        fuente_pos[nombre] = "pesos fijos"
    prev_txt = {n: "Pesos fijos (revisión en enero y julio)" for n in ("ROCA", "IMPULSO", "CRYPTO")}
    with warnings.catch_warnings(record=True) as capt_m:
        warnings.simplefilter("always")
        for nombre, hacer_cfg in (("GEDA", geda_cfg), ("GEN-Z", lambda: G.universo_genz()[0])):
            try:
                carteras[nombre], prev_txt[nombre] = posiciones_motor(nombre, hacer_cfg(), mes, mes_sig, avisos)
                fuente_pos[nombre] = "motor (señal del cierre del mes anterior)"
            except (YahooRoto, G.YahooRoto):
                raise
            except Exception as e:  # noqa: BLE001
                if "DESCARGA ROTA" in str(e):
                    raise YahooRoto(str(e)) from e
                avisos.append(f"{nombre}: no se han podido calcular las posiciones ({e}). Queda fuera.")
    avisos += sorted({f"Motor: {w.message}" for w in capt_m if issubclass(w.category, UserWarning)
                      and "congelado" not in str(w.message)})

    with warnings.catch_warnings(record=True) as capt:
        warnings.simplefilter("always")
        sp_df = M.load_prices([H.INDICE_SENAL], mes - pd.DateOffset(months=14), hasta, convert_to_eur=False)
        if H.INDICE_SENAL not in sp_df.columns:
            raise YahooRoto(f"Sin precios del {H.INDICE_SENAL}.")
        sp = sp_df[H.INDICE_SENAL].dropna()
        media = H.wma(sp, H.SESIONES_WMA)
        s_mes, _, _ = H.senal_en(sp, media, mes.replace(day=1) - pd.Timedelta(days=1))
        s_hoy, dist, _ = H.senal_en(sp, media, hoy)
        for nombre, (plan, t_rv) in H.VERSIONES.items():
            t = t_rv if s_mes == "RV" else H.PLAN_RF[1]
            carteras[nombre] = {t: 1.0}
            fuente_pos[nombre] = f"señal {'renta variable' if s_mes == 'RV' else 'renta fija'}"
            prev_txt[nombre] = None
        txt_h = (f"{'Renta variable' if s_hoy == 'RV' else 'Renta fija'} (S&P {pct(dist, 1, True)} sobre su WMA "
                 f"{H.SESIONES_WMA})" + (" ⚠️ CAMBIO" if s_hoy != s_mes else " · sin cambios"))
        for nombre in H.VERSIONES:
            prev_txt[nombre] = txt_h

        tickers = sorted({t for c in carteras.values() for t in c} | {REFERENCIA[1]})
        p = M.load_prices(tickers, mes.replace(day=1) - pd.Timedelta(days=20), hasta, convert_to_eur=True)
    avisos += sorted({str(w.message) for w in capt if issubclass(w.category, UserWarning)})
    p = p.sort_index().ffill()

    curvas, filas = {}, []
    for nombre in ORDEN:
        if nombre not in carteras:
            continue
        faltan = [t for t in carteras[nombre] if t not in p.columns or p[t].dropna().empty]
        if faltan:
            avisos.append(f"{nombre}: sin precios en Yahoo para {faltan}. Queda fuera del gráfico conjunto.")
            continue
        r, res = F.rentab_mes(p, carteras[nombre], mes, hoy)
        if res is None:
            avisos.append(f"{nombre}: todavía no hay cierres de {etiqueta(mes)}.")
            continue
        c = res[0]
        curvas[nombre] = c
        dentro = c[c.index >= mes.replace(day=1)]
        filas.append({"Estrategia": nombre, "Perfil": PERFIL[nombre], "Mes": r,
                      "Máx": dentro.max(), "Mín": dentro.min(),
                      "Posiciones": ", ".join(sorted(carteras[nombre])),
                      "Fuente": fuente_pos[nombre], "Previsión": prev_txt[nombre]})
    if not curvas:
        raise YahooRoto("No se ha podido calcular ninguna estrategia.")
    _, res_ref = F.rentab_mes(p, {REFERENCIA[1]: 1.0}, mes, hoy)
    ref = res_ref[0]
    ultimo = max(c.index[-1] for c in curvas.values())

    png = grafico(curvas, ref, mes, os.path.join(CARPETA_INFORME, "conjunto_mes_actual.png"))
    imagenes = {"resumen": png}
    individuales = []
    for titulo, (ruta, carpeta) in INFORMES.items():
        if os.path.exists(ruta):
            cid = carpeta.replace("informe_", "").replace("informe", "geda")
            with open(ruta, "rb") as f:
                imagenes[cid] = f.read()
            individuales.append((titulo, cid, ruta, carpeta))
        else:
            avisos.append(f"No encuentro el gráfico de {titulo} ({ruta}): ¿ha fallado su paso?")

    # --- Informe ---
    repo = os.environ.get("GITHUB_REPOSITORY")
    url = (lambda c: f"https://github.com/{repo}/tree/HEAD/{c}") if repo else (lambda c: c)
    fila_html = "".join(
        f"<tr><td><b>{f['Estrategia']}</b><br><span style='font-size:11px;color:#777'>{f['Perfil']}</span></td>"
        f"<td style='text-align:right;color:{color(f['Mes'])};font-weight:bold;font-size:15px'>{pct(f['Mes'], signo=True)}</td>"
        f"<td style='text-align:right;color:{color(f['Máx'])}'>{pct(f['Máx'], signo=True)}</td>"
        f"<td style='text-align:right;color:{color(f['Mín'])}'>{pct(f['Mín'], signo=True)}</td>"
        f"<td style='font-size:12px'>{f['Posiciones']}<br><span style='color:#777'>{f['Fuente']}</span></td>"
        f"<td style='font-size:12px'>{f['Previsión']}</td></tr>" for f in filas)
    r_ref = float(ref.iloc[-1])
    fila_html += (f"<tr style='background:#f7f7f7'><td><b>{REFERENCIA[0]}</b></td>"
                  f"<td style='text-align:right;color:{color(r_ref)};font-weight:bold'>{pct(r_ref, signo=True)}</td>"
                  "<td></td><td></td><td></td><td></td></tr>")
    tabla = ("<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;font-size:13px'>"
             "<tr style='background:#f2f2f2'><th>Estrategia</th><th>Mes en curso</th><th>Mejor momento</th>"
             f"<th>Peor momento</th><th>Posiciones del mes</th><th>Previsión {etiqueta(mes_sig)}</th></tr>{fila_html}</table>")
    caja = ("" if not avisos else "<div style='border:1px solid #e0a800;background:#fff8e1;padding:8px 12px'>"
            "<b>⚠️ Avisos</b><ul>" + "".join(f"<li>{a}</li>" for a in avisos) + "</ul></div>")

    def documento(src_img):
        bloques = "".join(f"<h3>{t}</h3><img src='{src_img(cid, ruta)}' style='max-width:100%'>"
                          f"<p style='font-size:12px'><a href='{url(carp)}'>Informe completo de {t} en GitHub</a></p>"
                          for t, cid, ruta, carp in individuales)
        return f"""<html><head><meta charset="utf-8"><title>Estrategias {hoy.date()}</title></head>
<body style="font-family:Arial,Helvetica,sans-serif;max-width:1100px;margin:auto;color:#222">
<h1 style="margin-bottom:2px">TuAmigoInversor · todas las estrategias</h1>
<p style="color:#555;margin-top:0">Ejecutado el {pd.Timestamp.now(tz='Europe/Madrid'):%d-%m-%Y %H:%M} (Madrid) ·
último cierre con datos: {ultimo:%d-%m-%Y} · {etiqueta(mes)} provisional · rentabilidades en EUR</p>
<img src="{src_img('resumen', None)}" style="max-width:100%">{caja}
<h2>Resumen del mes</h2>{tabla}
<p style="font-size:11px;color:#777">Compra al cierre del mes anterior con los pesos objetivo y mantiene todo el mes.
Posiciones de GEDA y GEN-Z calculadas con el motor; la previsión, como si el mes terminase hoy (puede cambiar hasta el cierre).</p>
<h2>Informes de cada estrategia</h2>{bloques}
<hr><p style="font-size:11px;color:#777">Uso interno: no es una recomendación de inversión.</p></body></html>"""

    b64 = lambda cid, _r: f"data:image/png;base64,{base64.b64encode(imagenes[cid]).decode()}"   # noqa: E731
    os.makedirs(os.path.join(CARPETA_SALIDA, "historico"), exist_ok=True)
    for ruta in (os.path.join(CARPETA_SALIDA, "CONJUNTO_hoy.html"),
                 os.path.join(CARPETA_SALIDA, "historico", f"CONJUNTO_{hoy.date()}.html")):
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(documento(b64))

    L = ["# TuAmigoInversor · todas las estrategias",
         f"Ejecutado el {pd.Timestamp.now(tz='Europe/Madrid'):%d-%m-%Y %H:%M} (Madrid) · último cierre con datos: "
         f"{ultimo:%d-%m-%Y} · {etiqueta(mes)} provisional · EUR", "", "![Resumen](conjunto_mes_actual.png)", ""]
    if avisos:
        L += ["> ⚠️ **Avisos**"] + [f"> - {a}" for a in avisos] + [""]
    L += ["| Estrategia | Perfil | Mes en curso | Mejor | Peor | Posiciones del mes | Previsión " + etiqueta(mes_sig) + " |",
          "|---|---|---:|---:|---:|---|---|"]
    L += [f"| **{f['Estrategia']}** | {f['Perfil']} | **{pct(f['Mes'], signo=True)}** | {pct(f['Máx'], signo=True)} | "
          f"{pct(f['Mín'], signo=True)} | {f['Posiciones']} | {f['Previsión']} |" for f in filas]
    L += [f"| {REFERENCIA[0]} | | **{pct(r_ref, signo=True)}** | | | | |", "", "## Informes de cada estrategia", ""]
    L += [f"- [{t}](../{carp}/README.md)" for t, _, _, carp in individuales]
    with open(os.path.join(CARPETA_INFORME, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    ruta_csv = os.path.join(CARPETA_SALIDA, "conjunto_seguimiento_diario.csv")
    fila = pd.DataFrame([{"fecha": str(hoy.date()), "ultimo_cierre": str(ultimo.date()), "mes": mes.strftime("%Y-%m"),
                          **{f["Estrategia"]: round(f["Mes"], 6) for f in filas}, "msci_world": round(r_ref, 6)}])
    if os.path.exists(ruta_csv):
        viejo = pd.read_csv(ruta_csv, dtype=str)
        fila = pd.concat([viejo[viejo["fecha"] != str(hoy.date())], fila.astype(str)], ignore_index=True)
    fila.to_csv(ruta_csv, index=False)

    resumen_txt = " · ".join(f"{f['Estrategia'].replace('HORIZONTE ', 'HZ ')} {pct(f['Mes'], 1, True)}" for f in filas)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(f"## Todas las estrategias {hoy.date()}\n\n{resumen_txt}\n" + "".join(f"- ⚠️ {a}\n" for a in avisos))
    print(f"✅ {etiqueta(mes)}: {resumen_txt}")
    for a in avisos:
        print(f"⚠️ {a}")

    if all(F._secreto(k) for k in F.SECRETOS):
        try:
            enviar_correo(f"Estrategias {hoy:%d-%m} · {resumen_txt}" + (" · ⚠️" if avisos else ""),
                          documento(lambda cid, _r: f"cid:{cid}"), imagenes)
            print("📧 Correo enviado")
        except Exception as e:  # noqa: BLE001
            print(f"⛔ No se ha podido enviar el correo: {e}")
            return 3
    else:
        print("ℹ️ Sin secretos de correo: no se envía.")
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
