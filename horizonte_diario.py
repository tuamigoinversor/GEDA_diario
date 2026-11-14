# -*- coding: utf-8 -*-
"""
HORIZONTE — seguimiento diario (GitHub Actions o local)
=======================================================
Regla: si el S&P 500 cierra el mes por encima de su media ponderada (WMA) de 150 sesiones, el mes siguiente
100 % plan de renta variable; si no, 100 % plan de renta fija (Santander Renta Fija PP).
Dos versiones con la misma señal:
  - Simple:   RV = BK Variable América PP
  - Avanzada: RV = BBVA Telecomunicaciones PP (ticker de siempre; el plan ha cambiado de nombre)

Qué saca cada día:
  1. Resumen: 3 meses cerrados y el mes en curso (provisional) de las dos versiones, frente al MSCI World,
     con la señal de cada mes.
  2. PREVISIÓN del mes siguiente: la señal con el cierre de hoy (S&P 500 frente a su WMA 150 y distancia).
  3. Gráfico: el mes día a día (dos versiones y MSCI World) y el S&P 500 con su WMA 150 de los últimos 12 meses.
  4. informe_horizonte/README.md + gráfico (se ve en GitHub), salida/ con HTML y CSV, y correo.

Utilidades comunes (precios, correo, formato): fijas_diario.py.
"""
import base64
import io
import os
import sys
import traceback
import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates          # noqa: E402
import matplotlib.pyplot as plt            # noqa: E402
import matplotlib.ticker as mtick          # noqa: E402
import numpy as np                         # noqa: E402
import pandas as pd                        # noqa: E402

import fijas_diario as F                   # noqa: E402
from fijas_diario import M, YahooRoto, etiqueta, pct, color   # noqa: E402

# =========================================================
# CONFIGURACIÓN — lo único que deberías tocar
# =========================================================
INDICE_SENAL = "^GSPC"            # S&P 500 (índice de precio, en USD)
SESIONES_WMA = 150
PLAN_RF = ("Santander Renta Fija PP", "0P0000142P.F")
VERSIONES = {
    "HORIZONTE simple": ("BK Variable América PP", "0P000015V0.F"),
    "HORIZONTE avanzada": ("BBVA Telecomunicaciones PP", "0P000011YA.F"),
}
REFERENCIA = F.REFERENCIA
MESES_CERRADOS = 3
CARPETA_SALIDA = F.CARPETA_SALIDA
CARPETA_INFORME = "informe_horizonte"
DIAS_DATOS_ATRASADOS = 5          # los planes publican el valor liquidativo con algún día de retraso


def wma(serie, n):
    pesos = np.arange(1, n + 1, dtype=float)
    return serie.rolling(n).apply(lambda x: float(np.dot(x, pesos) / pesos.sum()), raw=True)


def senal_en(sp, media, fecha):
    """Señal con el último cierre hasta `fecha` (incluida): 'RV' o 'RF', y distancia a la media."""
    s, w = sp[sp.index <= fecha].dropna(), media[media.index <= fecha].dropna()
    if s.empty or w.empty:
        return None, np.nan, None
    d = s.index[-1]
    return ("RV" if s.iloc[-1] > w.loc[d] else "RF"), float(s.iloc[-1] / w.loc[d] - 1), d


def grafico(curvas, ref, sp, media, mes, ruta_png):
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(11, 8.6), gridspec_kw={"height_ratios": [1.1, 1]})
    for (nombre, (serie, plan)), col in zip(curvas.items(), ["black", "#1f5fbf"]):
        ax.plot(serie.index, serie * 100, color=col, lw=2.6 if col == "black" else 2,
                label=f"{nombre} · {plan}  {pct(serie.iloc[-1], signo=True)}")
    ax.plot(ref.index, ref * 100, color="grey", ls="--", lw=1.4, label=f"{REFERENCIA[0]}  {pct(ref.iloc[-1], signo=True)}")
    ax.axhline(0, color="#999999", lw=0.8)
    ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda y, _: f"{y:+.1f} %"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d"))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    ax.set_title(f"HORIZONTE · {F.MESES_LARGO[mes.month - 1]} de {mes.year} · día a día (EUR)", loc="left", fontsize=12)
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    ax.grid(alpha=0.25)

    desde = mes - pd.DateOffset(months=12)
    s, w = sp[sp.index >= desde], media[media.index >= desde]
    ax2.plot(s.index, s, color="black", lw=1.4, label="S&P 500")
    ax2.plot(w.index, w, color="#c62828", lw=1.4, label=f"WMA {SESIONES_WMA} sesiones")
    ax2.fill_between(s.index, s, w.reindex(s.index), where=s >= w.reindex(s.index), color="#0a7d2c", alpha=0.12)
    ax2.fill_between(s.index, s, w.reindex(s.index), where=s < w.reindex(s.index), color="#c62828", alpha=0.12)
    for fm in pd.date_range(desde, mes, freq="ME"):
        ax2.axvline(fm, color="#bbbbbb", lw=0.6)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b-%y"))
    ax2.set_title("Señal: S&P 500 frente a su media ponderada (verde = renta variable, rojo = renta fija)",
                  loc="left", fontsize=11)
    ax2.legend(loc="upper left", fontsize=8, frameon=False)
    ax2.grid(alpha=0.25)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    png = buf.getvalue()
    with open(ruta_png, "wb") as f:
        f.write(png)
    return png


def main():
    hoy = M._hoy()
    mes_actual = hoy + pd.offsets.MonthEnd(0)
    mes_siguiente = mes_actual + pd.offsets.MonthEnd(1)
    ultimo_cerrado = hoy.replace(day=1) - pd.offsets.MonthEnd(1)
    primer_mes = ultimo_cerrado - pd.offsets.MonthEnd(MESES_CERRADOS - 1)
    cerrados = list(pd.date_range(primer_mes, ultimo_cerrado, freq="ME"))
    meses = cerrados + [mes_actual]
    hasta = hoy + pd.Timedelta(days=1)
    os.makedirs(os.path.join(CARPETA_SALIDA, "historico"), exist_ok=True)
    os.makedirs(CARPETA_INFORME, exist_ok=True)

    with warnings.catch_warnings(record=True) as capt:
        warnings.simplefilter("always")
        planes = sorted({t for _, t in VERSIONES.values()} | {PLAN_RF[1], REFERENCIA[1]})
        p = F.precios(planes, primer_mes.replace(day=1) - pd.Timedelta(days=20), hasta)
        sp_df = M.load_prices([INDICE_SENAL], primer_mes - pd.DateOffset(months=16), hasta, convert_to_eur=False)
    avisos = sorted({str(w.message) for w in capt if issubclass(w.category, UserWarning)})
    if INDICE_SENAL not in sp_df.columns:
        raise YahooRoto(f"Sin precios del {INDICE_SENAL}.")
    sp = sp_df[INDICE_SENAL].dropna()
    media = wma(sp, SESIONES_WMA)

    # --- Señal de cada mes (la del cierre del mes anterior) y previsión con el cierre de hoy ---
    senal = {}
    for m in meses:
        fin_ant = m.replace(day=1) - pd.Timedelta(days=1)
        senal[m] = senal_en(sp, media, fin_ant)
        if senal[m][0] is None:
            raise YahooRoto(f"No hay datos suficientes para la señal de {etiqueta(m)}.")
    prev, dist_hoy, dia_prev = senal_en(sp, media, hoy)
    cambio = prev != senal[mes_actual][0]

    # --- Rentabilidades ---
    tabla, curvas, plan_mes = {}, {}, {}
    for nombre, (plan_rv, t_rv) in VERSIONES.items():
        tabla[nombre] = {}
        for m in meses:
            t = t_rv if senal[m][0] == "RV" else PLAN_RF[1]
            r, res = F.rentab_mes(p, {t: 1.0}, m, hoy if m == mes_actual else None)
            tabla[nombre][m] = r
            if m == mes_actual:
                curvas[nombre] = (res[0], plan_rv if t == t_rv else PLAN_RF[0])
                plan_mes[nombre] = (curvas[nombre][1], t)
    r_ref, res_ref = {}, None
    for m in meses:
        r_ref[m], res = F.rentab_mes(p, {REFERENCIA[1]: 1.0}, m, hoy if m == mes_actual else None)
        if m == mes_actual:
            res_ref = res[0]
    tabla[REFERENCIA[0]] = r_ref
    for nombre, (_, t) in plan_mes.items():
        ultimo = p[t].dropna().index[-1]
        if (hoy - ultimo).days > DIAS_DATOS_ATRASADOS:
            avisos.append(f"{nombre}: el último valor liquidativo de {t} es del {ultimo.date()}.")
    ultimo_dato = max(s.index[-1] for s, _ in curvas.values())

    ruta_png = os.path.join(CARPETA_SALIDA, f"HORIZONTE_grafico_diario_{mes_actual.strftime('%Y-%m')}.png")
    png = grafico(curvas, res_ref, sp, media, mes_actual, ruta_png)
    with open(os.path.join(CARPETA_INFORME, "horizonte_mes_actual.png"), "wb") as f:
        f.write(png)

    nombres = list(VERSIONES) + [REFERENCIA[0]]
    acum = {n: float(np.prod([1 + tabla[n][m] for m in cerrados]) - 1) for n in nombres}
    txt_senal = lambda m: (f"{'Renta variable' if senal[m][0] == 'RV' else 'Renta fija'} "   # noqa: E731
                           f"(S&P {pct(senal[m][1], 1, True)} sobre la media)")
    txt_prev = (f"{'Renta variable' if prev == 'RV' else 'Renta fija'}: el S&P 500 está {pct(abs(dist_hoy), 1)} "
                f"{'por encima' if dist_hoy > 0 else 'por debajo'} de su WMA {SESIONES_WMA} (cierre del {dia_prev:%d-%m}). "
                + ("⚠️ CAMBIO respecto al mes en curso." if cambio else "Sin cambios."))
    if cambio:
        avisos.insert(0, f"La señal de hoy cambia a {'renta variable' if prev == 'RV' else 'renta fija'} para "
                         f"{etiqueta(mes_siguiente)}. Solo cuenta el cierre del último día del mes.")
    cabecera = " · ".join(f"{n} {pct(tabla[n][mes_actual], signo=True)}" for n in nombres)
    est = lambda m: "provisional" if m == mes_actual else "cerrado"   # noqa: E731

    filas = "".join(f"<tr><td><b>{etiqueta(m)}</b> ({est(m)})</td><td>{txt_senal(m)}</td>"
                    + "".join(f"<td style='text-align:right;color:{color(tabla[n][m])};font-weight:bold'>{pct(tabla[n][m])}</td>"
                              for n in nombres) + "</tr>" for m in meses)
    filas += ("<tr style='background:#eef3ff'><td colspan='2'><b>Acumulado meses cerrados</b></td>"
              + "".join(f"<td style='text-align:right'>{pct(acum[n])}</td>" for n in nombres) + "</tr>")
    html_tabla = ("<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;font-size:13px'>"
                  "<tr style='background:#f2f2f2'><th>Mes</th><th>Señal</th>" + "".join(f"<th>{n}</th>" for n in nombres)
                  + f"</tr>{filas}</table>")
    planes_txt = " · ".join(f"{n}: {plan} ({t})" for n, (plan, t) in plan_mes.items())
    caja = ("" if not avisos else "<div style='border:1px solid #e0a800;background:#fff8e1;padding:8px 12px'>"
            "<b>⚠️ Avisos</b><ul>" + "".join(f"<li>{a}</li>" for a in avisos) + "</ul></div>")

    def documento(src):
        return f"""<html><head><meta charset="utf-8"><title>HORIZONTE {hoy.date()}</title></head>
<body style="font-family:Arial,Helvetica,sans-serif;max-width:1100px;margin:auto;color:#222">
<h1 style="margin-bottom:2px">HORIZONTE · seguimiento diario</h1>
<p style="color:#555;margin-top:0">Ejecutado el {pd.Timestamp.now(tz='Europe/Madrid'):%d-%m-%Y %H:%M} (Madrid) ·
último valor liquidativo: {ultimo_dato:%d-%m-%Y} · rentabilidades en EUR</p>
<p><b>{etiqueta(mes_actual)} (provisional):</b> {cabecera}<br><b>Planes este mes:</b> {planes_txt}<br>
<b>Previsión para {etiqueta(mes_siguiente)}:</b> {txt_prev}</p>{caja}
<h2>1. {F.MESES_LARGO[mes_actual.month - 1].capitalize()} de {mes_actual.year} y la señal</h2>
<img src="{src}" style="max-width:100%">
<h2>2. Resumen</h2>{html_tabla}
<hr><p style="font-size:11px;color:#777">Señal con el cierre del último día de cada mes (S&P 500 en USD frente a su media
ponderada de {SESIONES_WMA} sesiones). Valores liquidativos de Yahoo, que pueden llegar con uno o dos días de retraso.
Uso interno: no es una recomendación de inversión.</p></body></html>"""

    html = documento(f"data:image/png;base64,{base64.b64encode(png).decode()}")
    for ruta in (os.path.join(CARPETA_SALIDA, "HORIZONTE_hoy.html"),
                 os.path.join(CARPETA_SALIDA, "historico", f"HORIZONTE_{hoy.date()}.html")):
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(html)

    L = ["# HORIZONTE · seguimiento diario",
         f"Ejecutado el {pd.Timestamp.now(tz='Europe/Madrid'):%d-%m-%Y %H:%M} (Madrid) · último valor liquidativo: "
         f"{ultimo_dato:%d-%m-%Y} · rentabilidades en EUR", "",
         f"**{etiqueta(mes_actual)} (provisional):** {cabecera}", "", f"**Planes este mes:** {planes_txt}", "",
         f"**Previsión para {etiqueta(mes_siguiente)}:** {txt_prev}", ""]
    if avisos:
        L += ["> ⚠️ **Avisos**"] + [f"> - {a}" for a in avisos] + [""]
    L += [f"## 1. {F.MESES_LARGO[mes_actual.month - 1].capitalize()} de {mes_actual.year} y la señal", "",
          "![Gráfico](horizonte_mes_actual.png)", "", "## 2. Resumen", "",
          "| Mes | Señal | " + " | ".join(nombres) + " |", "|---|---|" + "---:|" * len(nombres)]
    L += [f"| **{etiqueta(m)}** ({est(m)}) | {txt_senal(m)} | " + " | ".join(f"**{pct(tabla[n][m])}**" for n in nombres) + " |"
          for m in meses]
    L += ["| **Acumulado meses cerrados** | | " + " | ".join(pct(acum[n]) for n in nombres) + " |", "",
          "_Señal con el cierre del último día de cada mes. Uso interno: no es una recomendación de inversión._"]
    with open(os.path.join(CARPETA_INFORME, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    ruta_csv = os.path.join(CARPETA_SALIDA, "horizonte_seguimiento_diario.csv")
    fila = pd.DataFrame([{"fecha": str(hoy.date()), "ultimo_valor": str(ultimo_dato.date()),
                          "mes": mes_actual.strftime("%Y-%m"), "senal_mes": senal[mes_actual][0],
                          "simple": round(tabla["HORIZONTE simple"][mes_actual], 6),
                          "avanzada": round(tabla["HORIZONTE avanzada"][mes_actual], 6),
                          "msci_world": round(r_ref[mes_actual], 6),
                          "senal_prevision": prev, "distancia_wma": round(dist_hoy, 6)}])
    if os.path.exists(ruta_csv):
        viejo = pd.read_csv(ruta_csv, dtype=str)
        fila = pd.concat([viejo[viejo["fecha"] != str(hoy.date())], fila.astype(str)], ignore_index=True)
    fila.to_csv(ruta_csv, index=False)

    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(f"## HORIZONTE {hoy.date()}\n\n- {etiqueta(mes_actual)}: {cabecera}\n"
                    f"- Previsión {etiqueta(mes_siguiente)}: {txt_prev}\n" + "".join(f"- ⚠️ {a}\n" for a in avisos))
    print(f"✅ {etiqueta(mes_actual)}: {cabecera}\n   Previsión {etiqueta(mes_siguiente)}: {txt_prev}")
    for a in avisos:
        print(f"⚠️ {a}")

    if all(F._secreto(k) for k in F.SECRETOS):
        asunto = (f"HORIZONTE {hoy:%d-%m} · {etiqueta(mes_actual)} "
                  + " · ".join(f"{n.split()[-1]} {pct(tabla[n][mes_actual], signo=True)}" for n in VERSIONES)
                  + f" · {etiqueta(mes_siguiente)}: {'RV' if prev == 'RV' else 'RF'}" + (" ⚠️ CAMBIO" if cambio else ""))
        try:
            F.enviar_correo(asunto, documento("cid:grafico"), png)
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
