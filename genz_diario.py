# -*- coding: utf-8 -*-
"""
GEN-Z · seguimiento diario (pensado para GitHub Actions, también funciona en local)
===================================================================================
Mismo esquema que geda_diario.py. Qué saca cada día:
  1. Los últimos 3 meses cerrados (lo PUBLICADO, versión v2 con tu lista fija y sin límite de tema),
     el mes en curso (PROVISIONAL) y la PREVISIÓN del mes siguiente (versión definitiva), con GEN-Z
     frente al MSCI World y al ETF de semiconductores.
  2. Posiciones y votos de la versión definitiva (mes en curso y previsión).
  3. El gráfico día a día del mes en curso (cartera, cada posición y referencias), en EUR.
  4. Una línea por día en salida/genz_seguimiento_diario.csv.

Dos versiones, separadas por fecha (NUEVA_DESDE):
  - Hasta oct-2026: v2. Las posiciones salen de datos/genz_posiciones.csv (lo que publicaste),
    para que cuadre con tu histórico. No se recalculan.
  - Desde nov-2026: definitiva (11-11-2026). 4 acciones al 25 %, máximo 2 por tema, señal "ambas",
    universo = acciones de S&P 500, Nasdaq-100, Dow y europeos de GEDA, con más de 3 meses en el índice
    y tema GEN-Z por industria de Yahoo. Robótica/IA y cuántica por cartera de ETF, desde dic-2026.

Motor: motor_genz.py = copia literal de la celda "Motor" de GENZ_que_comprar.ipynb.

Códigos de salida (los usa el workflow):
  0 = informe generado · 2 = Yahoo ha fallado (reintenta) · 3 = sin correo · 1 = otro error
Si falla, NO se escribe ningún fichero.
"""
import base64
import io
import json
import os
import smtplib
import sys
import time
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
MESES_CERRADOS = 3
NUEVA_DESDE = "2026-11"            # primer mes con la versión definitiva; antes, lo publicado (v2)

# Versión definitiva (11-11-2026). No se toca hasta ene-2028.
GENZ_SENAL = "ambas"
GENZ_MESES_MIN = 3
GENZ_N = 4
GENZ_MAX_TEMA = 2
TEMAS_YAHOO = {
    "Semiconductors": "SEMICONDUCTORES", "Semiconductor Equipment & Materials": "SEMICONDUCTORES",
    "Aerospace & Defense": "AEROESPACIAL_DEFENSA",
    "Software - Infrastructure": "SOFTWARE_CIBER",
    "Electronic Gaming & Multimedia": "VIDEOJUEGOS",
    "Other Industrial Metals & Mining": "METALES_CRITICOS", "Copper": "METALES_CRITICOS",
    "Uranium": "ENERGIA_IA", "Utilities - Independent Power Producers": "ENERGIA_IA",
    "Electrical Equipment & Parts": "ENERGIA_IA",
    "Health Information Services": "SALUD_IA", "Diagnostics & Research": "SALUD_IA",
}
SENAL_SALUD_DESDE = "2026-09-30"   # SALUD_IA cuenta desde las posiciones de oct-2026
ETF_TEMAS_DESDE = "2026-11-30"     # robótica/IA y cuántica (por cartera de ETF) desde las posiciones de dic-2026
TEMAS_ETF_VALIDOS = ("ROBOTICA_IA", "CUANTICA")

REFERENCIAS = [("MSCI World (URTH, EUR)", "URTH"), ("Semiconductores (SMH, EUR)", "SMH")]

CARPETA_DATOS = "datos"
CARPETA_SALIDA = "salida"
FICHERO_MEMB_USA = "membresias_usa_desde2015.csv"
FICHERO_MEMB_EUROPA = "membresias_europa_desde2015.csv"
FICHERO_POSICIONES = "genz_posiciones.csv"         # mes,ticker,rentab_publicada
FICHERO_TEMAS_ETF = "genz_temas_etf.csv"           # ticker,tema (ROBOTICA_IA | CUANTICA)
FICHERO_INDUSTRIAS = "industrias_yahoo_cache.json"  # cópialo de Drive/Ficheros_auxiliares

TOLERANCIA_CUADRE = 0.001          # gráfico frente a motor: 0,1 puntos
TOLERANCIA_PUBLICADO = 0.01        # lo publicado frente a Yahoo: aviso si difieren más de 1 punto
MAX_SIN_PRECIO_ACCIONES = 0.05
DIAS_DATOS_ATRASADOS = 4
MAX_FALLO_INDUSTRIAS = 0.40

M.VERBOSE = False
M.RUTA_CACHE_DIVISAS = os.path.join(CARPETA_DATOS, "divisas_cache.json")

MESES_LARGO = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
               "septiembre", "octubre", "noviembre", "diciembre"]
COLORES_REF = ["grey", "#b36b00"]


class YahooRoto(Exception):
    """Datos incompletos de Yahoo: hay que reintentar más tarde."""


# =========================================================
# UTILIDADES
# =========================================================
def etiqueta(m):
    return f"{M.MESES_ES[m.month - 1].lower()}-{str(m.year)[2:]}"


def pct(x, dec=2, signo=False):
    if x is None or pd.isna(x):
        return "—"
    s = f"{x*100:+.{dec}f}" if signo else f"{x*100:.{dec}f}"
    return s.replace(".", ",") + " %"


def pp(x):
    return "—" if x is None or pd.isna(x) else f"{x*100:+.2f}".replace(".", ",") + " pp"


def color(x):
    return "#999999" if x is None or pd.isna(x) else ("#0a7d2c" if x >= 0 else "#c62828")


def ruta_datos(nombre):
    return os.path.join(CARPETA_DATOS, nombre)


def es_nueva(m):
    return m.strftime("%Y-%m") >= NUEVA_DESDE


# =========================================================
# POSICIONES (publicadas hasta oct-2026; reales opcionales desde nov-2026)
# =========================================================
def leer_posiciones():
    ruta = ruta_datos(FICHERO_POSICIONES)
    if not os.path.exists(ruta):
        raise FileNotFoundError(f"Falta {ruta} (mes,ticker,rentab_publicada).")
    d = pd.read_csv(ruta, dtype=str).dropna(how="all")
    d.columns = [c.strip().lower() for c in d.columns]
    d["mes"] = d["mes"].str.strip()
    d["ticker"] = d["ticker"].str.strip()
    d["rentab_publicada"] = pd.to_numeric(d.get("rentab_publicada"), errors="coerce")
    return d[d["ticker"].fillna("") != ""]


def posiciones_de(pos, mes):
    filas = pos[pos["mes"] == mes.strftime("%Y-%m")]
    return dict(zip(filas["ticker"], filas["rentab_publicada"]))


# =========================================================
# UNIVERSO DE LA VERSIÓN DEFINITIVA
# =========================================================
def cargar_membresias(nombre):
    ruta = ruta_datos(nombre)
    if not os.path.exists(ruta):
        raise FileNotFoundError(f"Falta {ruta}. Cópialo desde Drive/Ficheros_auxiliares (ver LEEME.md).")
    return M.cargar_membresias(ruta)


def industrias_yahoo(tickers):
    """Industria y nombre de Yahoo, con caché en datos/ (se guarda en el repo)."""
    import yfinance as yf
    ruta = ruta_datos(FICHERO_INDUSTRIAS)
    cache = {}
    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8") as f:
            cache = json.load(f)
    pendientes = [t for t in tickers if t not in cache or cache[t].get("error")]
    if pendientes:
        print(f"🏷️ Consultando la industria de {len(pendientes)} valores en Yahoo...")
        fallos = 0
        for t in pendientes:
            try:
                info = yf.Ticker(t).info
                cache[t] = {"industria": info.get("industry"), "nombre": info.get("shortName")}
            except Exception as e:  # noqa: BLE001
                cache[t] = {"industria": None, "error": str(e)[:80]}
                fallos += 1
            time.sleep(0.2)
        if len(pendientes) >= 10 and fallos / len(pendientes) > MAX_FALLO_INDUSTRIAS:
            raise YahooRoto(f"Yahoo ha fallado en {fallos} de {len(pendientes)} consultas de industria.")
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=1, ensure_ascii=False)
    return {t: cache.get(t, {}) for t in tickers}


def leer_temas_etf(tickers_indices):
    """Robótica/IA y cuántica por cartera de ETF (top 25 de BOTZ y WQTM ∩ índices grandes)."""
    ruta = ruta_datos(FICHERO_TEMAS_ETF)
    if not os.path.exists(ruta):
        return {}
    d = pd.read_csv(ruta, dtype=str).dropna(how="all")
    if d.empty:
        return {}
    d.columns = [c.strip().lower() for c in d.columns]
    out = {}
    for t, tema in zip(d["ticker"].str.strip(), d["tema"].str.strip().str.upper()):
        if tema not in TEMAS_ETF_VALIDOS:
            raise ValueError(f"{FICHERO_TEMAS_ETF}: tema '{tema}' de {t} no válido ({TEMAS_ETF_VALIDOS}).")
        if t in tickers_indices and (t not in out or tema == "ROBOTICA_IA"):   # en las dos → robótica/IA
            out[t] = tema
    return out


def _retrasar_alta(memb, mascara, desde):
    """El valor solo es elegible desde las posiciones que se deciden con la señal de `desde`."""
    alta_min = pd.Timestamp(desde) - pd.DateOffset(months=GENZ_MESES_MIN) - pd.Timedelta(days=1)
    memb.loc[mascara, "alta"] = memb.loc[mascara, "alta"].fillna(alta_min).clip(lower=alta_min)


def universo_genz():
    memb = pd.concat([cargar_membresias(FICHERO_MEMB_USA), cargar_membresias(FICHERO_MEMB_EUROPA)],
                     ignore_index=True)[["ticker", "indice", "alta", "baja"]]
    todos = sorted(set(memb["ticker"]))
    info = industrias_yahoo(todos)
    tema = {t: TEMAS_YAHOO[d["industria"]] for t, d in info.items() if d.get("industria") in TEMAS_YAHOO}
    etf = {t: v for t, v in leer_temas_etf(set(todos)).items() if t not in tema}   # Yahoo manda
    tema.update(etf)
    sat = memb[memb["ticker"].isin(tema)].copy()
    _retrasar_alta(sat, sat["ticker"].map(tema).eq("SALUD_IA"), SENAL_SALUD_DESDE)
    _retrasar_alta(sat, sat["ticker"].isin(etf), ETF_TEMAS_DESDE)
    sat = sat[sat["baja"].isna() | (sat["baja"] > sat["alta"])].reset_index(drop=True)
    cfg = {"Acciones GEN-Z": {"tickers": sorted(sat["ticker"].unique()), "membresias": sat,
                              "meses_min": GENZ_MESES_MIN, "regla": "dinamica", "n": GENZ_N,
                              "senal": GENZ_SENAL, "grupos": tema, "max_por_grupo": GENZ_MAX_TEMA}}
    return cfg, tema, info, etf


def comprobar_universo(total, cfg, meses):
    universos = total.attrs.get("universos", {})
    avisos = []
    for bloque in cfg:
        for m in meses:
            info = universos.get(bloque, {}).get(m)
            if info is None:
                continue
            sd = info["sin_datos"]
            n_eleg = len(info["efectivo"]) + len(sd)
            if n_eleg and len(sd) / n_eleg > MAX_SIN_PRECIO_ACCIONES:
                raise YahooRoto(f"{etiqueta(m)}: faltan precios de {len(sd)} de {n_eleg} acciones elegibles "
                                f"({sd[:15]}...). Probable bloqueo parcial de Yahoo.")
            if sd:
                avisos.append(f"{etiqueta(m)}: elegibles sin precio en Yahoo (quedan fuera): {sd}")
    return sorted(set(avisos))


# =========================================================
# RENTABILIDADES
# =========================================================
def rentab_mensual_yahoo(pesos, mes):
    """Rentabilidad del mes en EUR (cierre a cierre) para comprobar lo publicado."""
    ini = mes.replace(day=1)
    p = M.load_prices(list(pesos), ini - pd.Timedelta(days=20), mes + pd.Timedelta(days=1), convert_to_eur=True)
    faltan = [t for t in pesos if t not in p.columns]
    antes, dentro = p[p.index < ini].ffill(), p[p.index >= ini].ffill()
    if faltan or antes.empty or dentro.empty:
        return np.nan, faltan
    r = dentro.iloc[-1][list(pesos)] / antes.iloc[-1][list(pesos)] - 1
    return float((r * pd.Series(pesos)).sum()), faltan


def curva_diaria(pesos, mes, hoy):
    """Rentabilidad acumulada en EUR desde el cierre del mes anterior (compra y mantiene todo el mes)."""
    ini = mes.replace(day=1)
    p = M.load_prices(list(pesos), ini - pd.Timedelta(days=20), hoy + pd.Timedelta(days=1), convert_to_eur=True)
    faltan = [t for t in pesos if t not in p.columns]
    if faltan:
        raise YahooRoto(f"Sin precios en Yahoo para el gráfico diario: {faltan}")
    antes = p[p.index < ini]
    if antes.empty:
        raise YahooRoto("No hay precio del cierre del mes anterior para el gráfico diario.")
    base = antes.ffill().iloc[-1][list(pesos)]
    if base.isna().any():
        raise YahooRoto(f"Sin precio al cierre del mes anterior: {base[base.isna()].index.tolist()}")
    rel = p[p.index >= ini][list(pesos)] / base - 1
    rel.loc[antes.index[-1]] = 0.0
    rel = rel.sort_index().ffill()
    cartera = (rel * pd.Series(pesos)).sum(axis=1)
    return cartera, rel


def serie_referencia(ticker, start):
    tot, _, _, _ = M.run_multi_strategy({ticker: {"tickers": [ticker], "n": 1}}, start, None, True, provisional=True)
    return tot


# =========================================================
# GRÁFICO
# =========================================================
def grafico(cartera, rel, refs, mes, fuente, ruta_png):
    fig, ax = plt.subplots(figsize=(11, 5))
    for t in rel.columns:
        ax.plot(rel.index, rel[t] * 100, lw=1, alpha=0.75, label=f"{t}  {pct(rel[t].iloc[-1], signo=True)}")
    ax.plot(cartera.index, cartera * 100, color="black", lw=2.8, label=f"GEN-Z  {pct(cartera.iloc[-1], signo=True)}")
    for (nombre, serie), col in zip(refs.items(), COLORES_REF):
        ax.plot(serie.index, serie * 100, color=col, ls="--", lw=1.4, label=f"{nombre}  {pct(serie.iloc[-1], signo=True)}")
    ax.axhline(0, color="#999999", lw=0.8)
    dentro = cartera[cartera.index >= mes.replace(day=1)]
    if len(dentro):
        for f, v, arriba in ((dentro.idxmax(), dentro.max(), True), (dentro.idxmin(), dentro.min(), False)):
            ax.scatter([f], [v * 100], color="black", zorder=5, s=18)
            ax.annotate(pct(v, signo=True), (f, v * 100), textcoords="offset points",
                        xytext=(0, 6 if arriba else -12), ha="center", fontsize=9)
    else:
        ax.text(0.5, 0.5, "Todavía no hay cierres de este mes", transform=ax.transAxes, ha="center", color="#777777")
    ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda y, _: f"{y:+.0f} %".replace("+0 ", "0 ")))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d"))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    ax.set_xlabel(f"día de {MESES_LARGO[mes.month - 1]} (el primer punto es el cierre del mes anterior = 0 %)",
                  fontsize=8)
    ax.set_title(f"GEN-Z · {MESES_LARGO[mes.month - 1]} de {mes.year} · día a día (EUR)", loc="left", fontsize=12)
    ax.text(0, -0.17, f"Posiciones: {fuente}", transform=ax.transAxes, fontsize=8, color="#555555")
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=8, frameon=False)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    png = buf.getvalue()
    with open(ruta_png, "wb") as f:
        f.write(png)
    return png


def frase(cartera, mes):
    c = cartera[cartera.index >= mes.replace(day=1)]
    if c.empty:
        return "Todavía no hay cierres de este mes."
    mx, dmx, mn, dmn = c.max(), c.idxmax(), c.min(), c.idxmin()
    return (f"Ha llegado a ir {pct(mx, signo=True)} el {dmx.day} de {MESES_LARGO[dmx.month - 1]} y su peor momento ha "
            f"sido {pct(mn, signo=True)} el {dmn.day} de {MESES_LARGO[dmn.month - 1]}. Va en {pct(c.iloc[-1], signo=True)}.")


# =========================================================
# TABLAS HTML
# =========================================================
def tabla(cabecera, filas):
    th = "".join(f"<th>{c}</th>" for c in cabecera)
    return ("<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;font-size:13px'>"
            f"<tr style='background:#f2f2f2'>{th}</tr>{''.join(filas)}</table>")


def td(x, negrita=False, num=True):
    estilo = f"text-align:right;color:{color(x)}" if num else ""
    if negrita:
        estilo += ";font-weight:bold"
    return f"<td style='{estilo}'>{pct(x) if num else x}</td>"


def html_resumen(meses, estados, versiones, genz, refs, acum_g, acum_r):
    filas = []
    for m in meses:
        g = genz.get(m, np.nan)
        r0 = refs[REFERENCIAS[0][0]].get(m, np.nan)
        dif = g - r0 if not (pd.isna(g) or pd.isna(r0)) else np.nan
        celdas = "".join(td(refs[n].get(m, np.nan)) for n, _ in REFERENCIAS)
        filas.append(f"<tr><td><b>{etiqueta(m)}</b></td><td>{estados[m]}</td><td>{versiones[m]}</td>"
                     f"{td(g, True)}{celdas}<td style='text-align:right'>{pp(dif)}</td></tr>")
    celdas = "".join(td(acum_r[n]) for n, _ in REFERENCIAS)
    filas.append(f"<tr style='background:#eef3ff'><td colspan='3'><b>Acumulado {MESES_CERRADOS} meses cerrados</b></td>"
                 f"{td(acum_g, True)}{celdas}<td style='text-align:right'>{pp(acum_g - acum_r[REFERENCIAS[0][0]])}</td></tr>")
    return tabla(["Mes", "Estado", "Versión", "GEN-Z"] + [n for n, _ in REFERENCIAS] + ["Dif. MSCI World"], filas)


def html_publicados(cerrados, pub, comprobacion):
    filas = []
    for m in cerrados:
        pos = pub[m]
        celdas = "".join(f"<div style='color:{color(r)};font-weight:bold'>{t} {pct(r)}</div>" for t, r in pos.items())
        tot, yh = np.nanmean(list(pos.values())) if pos else np.nan, comprobacion.get(m, np.nan)
        filas.append(f"<tr><td><b>{etiqueta(m)}</b></td><td>{celdas}</td>{td(tot, True)}{td(yh)}</tr>")
    return tabla(["Mes", "Posiciones publicadas (v2)", "GEN-Z publicado", "Comprobación Yahoo"], filas)


def html_posiciones_mes(pesos, rel, tema, info, metodo, fuente):
    filas = []
    for t, w in sorted(pesos.items()):
        r = rel[t].iloc[-1]
        marca = "" if t in metodo else " <span style='color:#c62828'>(no es del método)</span>"
        nombre = (info.get(t) or {}).get("nombre") or ""
        filas.append(f"<tr><td>{tema.get(t, '—')}</td><td><b>{t}</b>{marca}</td><td>{nombre}</td>"
                     f"<td style='text-align:right'>{pct(w, 1)}</td>{td(r, True)}</tr>")
    return (tabla(["Tema", "Ticker", "Nombre", "Peso", "Rentab. del mes"], filas)
            + f"<p style='font-size:12px;color:#555'>Posiciones: {fuente}</p>")


def enviar_correo(asunto, html_cid, png):
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    puerto = int(os.environ.get("SMTP_PORT", "465"))
    usuario, clave, para = os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"], os.environ["MAIL_TO"]
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
    mes_siguiente = mes_actual + pd.offsets.MonthEnd(1)
    primer_mes = ultimo_cerrado - pd.offsets.MonthEnd(MESES_CERRADOS - 1)
    cerrados = list(pd.date_range(primer_mes, ultimo_cerrado, freq="ME"))
    meses = cerrados + [mes_actual, mes_siguiente]
    if not es_nueva(mes_actual):
        raise RuntimeError(f"El mes en curso ({etiqueta(mes_actual)}) es anterior a NUEVA_DESDE ({NUEVA_DESDE}).")
    print(f"📅 Hoy {hoy.date()} · cerrados {[etiqueta(m) for m in cerrados]} · en curso {etiqueta(mes_actual)} "
          f"· previsión {etiqueta(mes_siguiente)}")

    os.makedirs(os.path.join(CARPETA_SALIDA, "historico"), exist_ok=True)
    pos = leer_posiciones()
    cfg, tema, info, temas_etf = universo_genz()
    avisos = []
    if mes_siguiente >= pd.Timestamp(ETF_TEMAS_DESDE) + pd.offsets.MonthEnd(1) and not temas_etf:
        avisos.append(f"{FICHERO_TEMAS_ETF} está vacío: la previsión de {etiqueta(mes_siguiente)} NO incluye robótica/IA "
                      f"ni cuántica por cartera de ETF. Pega ahí la foto de BOTZ y WQTM (ticker,tema).")

    start_motor = str((ultimo_cerrado.replace(day=1) - pd.Timedelta(days=1)).date())   # posiciones desde el último mes cerrado
    start_ref = str((primer_mes.replace(day=1) - pd.Timedelta(days=1)).date())

    with warnings.catch_warnings(record=True) as capt:
        warnings.simplefilter("always")
        # --- Versión definitiva: mes en curso (provisional) y previsión ---
        total, _, _, sub_pos = M.run_multi_strategy(cfg, start_motor, None, True, provisional=True)
        if total.attrs.get("mes_provisional") != mes_actual:
            raise RuntimeError(f"El motor no ha devuelto el mes en curso ({etiqueta(mes_actual)}) como provisional.")
        avisos += comprobar_universo(total, cfg, [mes_actual, mes_siguiente])
        bloque = next(iter(cfg))
        pos_metodo = list(sub_pos[bloque].get(mes_actual, {}))
        pos_sig = sorted(sub_pos[bloque].get(mes_siguiente, {}))
        if len(pos_metodo) != GENZ_N or len(pos_sig) != GENZ_N:
            raise RuntimeError(f"El motor no da {GENZ_N} posiciones ({pos_metodo} / {pos_sig}).")
        pesos_metodo = {t: 1 / GENZ_N for t in pos_metodo}

        # --- Referencias (meses) ---
        refs_mes = {n: serie_referencia(t, start_ref) for n, t in REFERENCIAS}

        # --- Meses cerrados: lo publicado (v2 hasta oct-2026; si ya hay meses de la nueva, el motor) ---
        genz, versiones, pub, comprob = {}, {}, {}, {}
        for m in cerrados:
            if es_nueva(m):
                raise RuntimeError(f"{etiqueta(m)} es un mes cerrado de la versión nueva: añade su posición publicada "
                                   f"a {FICHERO_POSICIONES} (mes,ticker,rentab_publicada) para mantener el registro.")
            p = posiciones_de(pos, m)
            if not p:
                raise RuntimeError(f"No hay posiciones publicadas de {etiqueta(m)} en {FICHERO_POSICIONES}.")
            pub[m] = p
            genz[m] = float(np.mean(list(p.values()))) if all(pd.notna(v) for v in p.values()) else np.nan
            versiones[m] = "v2 publicado"
            comprob[m], faltan = rentab_mensual_yahoo({t: 1 / len(p) for t in p}, m)
            if faltan:
                avisos.append(f"{etiqueta(m)}: sin precio en Yahoo para comprobar {faltan}.")
            elif pd.notna(genz[m]) and abs(genz[m] - comprob[m]) > TOLERANCIA_PUBLICADO:
                avisos.append(f"{etiqueta(m)}: publicado {pct(genz[m], signo=True)} frente a Yahoo "
                              f"{pct(comprob[m], signo=True)} ({pp(genz[m] - comprob[m])}). Manda lo publicado.")

        # --- Mes en curso: tu cartera real si la has anotado; si no, la del método ---
        real = posiciones_de(pos, mes_actual)
        if real:
            pesos = {t: 1 / len(real) for t in real}
            fuente = f"tu cartera real ({FICHERO_POSICIONES}), pesos iguales"
            sale, entra = sorted(set(pesos_metodo) - set(real)), sorted(set(real) - set(pesos_metodo))
            if sale or entra:
                avisos.append(f"Tu cartera real de {etiqueta(mes_actual)} NO coincide con el método: "
                              f"el método tiene {sale} y tú tienes {entra}.")
        else:
            pesos, fuente = pesos_metodo, "las del método (calculadas con el cierre del mes anterior)"
        cartera, rel = curva_diaria(pesos, mes_actual, hoy)
        refs_dia = {n: curva_diaria({t: 1.0}, mes_actual, hoy)[0] for n, t in REFERENCIAS}
    avisos += sorted({str(w.message) for w in capt if issubclass(w.category, UserWarning)})

    ultimo_dato = cartera.index[-1]
    if (hoy - ultimo_dato).days > DIAS_DATOS_ATRASADOS:
        avisos.append(f"El último cierre con datos es del {ultimo_dato.date()} ({(hoy - ultimo_dato).days} días). "
                      f"¿Festivo o datos atrasados de Yahoo?")

    g_metodo = total.get(mes_actual, np.nan)
    g_mes = float(cartera.iloc[-1])
    genz[mes_actual], versiones[mes_actual] = g_mes, "definitiva" + (" (cartera real)" if real else "")
    genz[mes_siguiente], versiones[mes_siguiente] = np.nan, "definitiva"
    if not real:
        cuadre = f"Cuadre con el motor: gráfico {pct(g_mes, signo=True)} · motor {pct(g_metodo, signo=True)}."
        if abs(g_mes - g_metodo) > TOLERANCIA_CUADRE:
            avisos.append(f"El gráfico diario ({pct(g_mes, signo=True)}) y el motor ({pct(g_metodo, signo=True)}) "
                          f"difieren {pp(g_mes - g_metodo)} en {etiqueta(mes_actual)}.")
    else:
        cuadre = f"Tu cartera real: {pct(g_mes, signo=True)} · método: {pct(g_metodo, signo=True)} en {etiqueta(mes_actual)}."

    # --- Cambios previstos (frente a lo que tienes de verdad este mes) y alertas de votos ---
    entran, salen = sorted(set(pos_sig) - set(pesos)), sorted(set(pesos) - set(pos_sig))
    alertas = []
    for blq, por_mes in total.attrs.get("rankings", {}).items():
        inf = por_mes.get(mes_siguiente)
        if inf:
            a = M._alerta_margen(inf)
            if a:
                alertas.append(f"{a} (margen {M._margen(inf)} votos entre el 4º y el 5º)")

    # --- Resumen ---
    estados = {m: "cerrado" for m in cerrados}
    estados[mes_actual] = f"provisional (hasta el {ultimo_dato.day}-{M.MESES_ES[ultimo_dato.month - 1].lower()})"
    estados[mes_siguiente] = "previsión de posiciones"
    acum = lambda s: float(np.prod([1 + s.get(m, np.nan) for m in cerrados]) - 1)   # noqa: E731
    refs_tabla = {n: s.copy() for n, s in refs_mes.items()}
    for n, _ in REFERENCIAS:
        refs_tabla[n][mes_actual] = float(refs_dia[n].iloc[-1])
    resumen = html_resumen(meses, estados, versiones, genz, refs_tabla, acum(genz),
                           {n: acum(refs_mes[n]) for n, _ in REFERENCIAS})

    tabla_motor = M.generate_email_report(sub_pos, total, "x", 2)
    tabla_motor = tabla_motor.replace("<html><body>", "").replace("</body></html>", "").replace("<h2>x</h2>", "")

    ruta_png = os.path.join(CARPETA_SALIDA, f"GENZ_grafico_diario_{mes_actual.strftime('%Y-%m')}.png")
    png = grafico(cartera, rel, refs_dia, mes_actual, fuente, ruta_png)

    cambios_txt = (f"ENTRAN {', '.join(entran)} · SALEN {', '.join(salen)}" if entran or salen
                   else "sin cambios previstos")
    prevision_txt = ", ".join(f"{t} ({tema.get(t, '—')})" for t in pos_sig)
    caja_avisos = ""
    if avisos or alertas:
        items = "".join(f"<li>{a}</li>" for a in alertas + avisos)
        caja_avisos = (f"<div style='border:1px solid #e0a800;background:#fff8e1;padding:8px 12px;margin:10px 0'>"
                       f"<b>⚠️ Avisos</b><ul style='margin:4px 0'>{items}</ul></div>")

    def documento(src_img):
        return f"""<html><head><meta charset="utf-8"><title>GEN-Z {hoy.date()}</title></head>
<body style="font-family:Arial,Helvetica,sans-serif;max-width:1100px;margin:auto;color:#222">
<h1 style="margin-bottom:2px">GEN-Z · seguimiento diario</h1>
<p style="color:#555;margin-top:0">Ejecutado el {pd.Timestamp.now(tz='Europe/Madrid'):%d-%m-%Y %H:%M} (Madrid) ·
último cierre con datos: {ultimo_dato:%d-%m-%Y} · rentabilidades en EUR</p>
<p><b>{etiqueta(mes_actual)} (provisional):</b> <span style="color:{color(g_mes)};font-weight:bold">{pct(g_mes, signo=True)}</span>
· {" · ".join(f"{n}: {pct(refs_dia[n].iloc[-1], signo=True)}" for n, _ in REFERENCIAS)}<br>
<b>Previsión para {etiqueta(mes_siguiente)}:</b> {prevision_txt} → {cambios_txt}</p>
{caja_avisos}
<h2>1. {MESES_LARGO[mes_actual.month - 1].capitalize()} de {mes_actual.year}, día a día</h2>
<img src="{src_img}" style="max-width:100%">
<p>{frase(cartera, mes_actual)}</p>
{html_posiciones_mes(pesos, rel, tema, info, pesos_metodo, fuente)}
<p style="font-size:12px;color:#555">{cuadre} Compra al cierre del mes anterior y mantiene todo el mes, con pesos iguales.</p>
<h2>2. Resumen</h2>{resumen}
<p style="font-size:12px;color:#555">Hasta {etiqueta(cerrados[-1])}: lo publicado con la v2 (tu lista fija, sin límite de tema).
Desde {etiqueta(pd.Timestamp(NUEVA_DESDE + '-01') + pd.offsets.MonthEnd(0))}: versión definitiva (4 acciones, máximo 2 por tema).</p>
<h2>3. Versión definitiva: posiciones y votos</h2>
<p style="font-size:12px;color:#555">Primera columna: previsión del mes siguiente. Segunda: mes en curso según el método.</p>
{tabla_motor}
<h2>4. Meses cerrados publicados (v2)</h2>
{html_publicados(cerrados, pub, comprob)}
<p style="font-size:12px;color:#555">La comprobación recalcula cada mes con Yahoo (pesos iguales, cierre a cierre, en EUR).
Si difiere más de 1 punto sale un aviso, pero manda lo publicado.</p>
<hr><p style="font-size:11px;color:#777">La previsión se calcula como si el mes terminase hoy y puede cambiar hasta el cierre.
Uso interno: no es una recomendación de inversión.</p>
</body></html>"""

    b64 = base64.b64encode(png).decode()
    html_repo = documento(f"data:image/png;base64,{b64}")
    for ruta in (os.path.join(CARPETA_SALIDA, "GENZ_hoy.html"),
                 os.path.join(CARPETA_SALIDA, "historico", f"GENZ_{hoy.date()}.html")):
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(html_repo)

    ruta_csv = os.path.join(CARPETA_SALIDA, "genz_seguimiento_diario.csv")
    fila = pd.DataFrame([{
        "fecha": str(hoy.date()), "ultimo_cierre": str(ultimo_dato.date()), "mes": mes_actual.strftime("%Y-%m"),
        "genz_mes": round(g_mes, 6), "metodo_mes": round(float(g_metodo), 6),
        **{f"ref_{t}": round(float(refs_dia[n].iloc[-1]), 6) for n, t in REFERENCIAS},
        "posiciones_metodo_mes": " ".join(sorted(pos_metodo)), "posiciones_grafico": " ".join(sorted(pesos)),
        "fuente_posiciones": "real" if real else "metodo",
        "prevision_siguiente": " ".join(pos_sig), "entran": " ".join(entran), "salen": " ".join(salen),
        "alertas_votos": " | ".join(alertas)}])
    if os.path.exists(ruta_csv):
        viejo = pd.read_csv(ruta_csv, dtype=str)
        fila = pd.concat([viejo[viejo["fecha"] != str(hoy.date())], fila.astype(str)], ignore_index=True)
    fila.to_csv(ruta_csv, index=False)

    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(f"## GEN-Z {hoy.date()}\n\n- {etiqueta(mes_actual)} provisional: **{pct(g_mes, signo=True)}**\n"
                    f"- Previsión {etiqueta(mes_siguiente)}: {prevision_txt} → {cambios_txt}\n"
                    + "".join(f"- ⚠️ {a}\n" for a in alertas + avisos))

    print(f"✅ {etiqueta(mes_actual)}: {pct(g_mes, signo=True)} · previsión {etiqueta(mes_siguiente)}: {prevision_txt} → {cambios_txt}")
    for a in alertas + avisos:
        print(f"⚠️ {a}")

    if all(os.environ.get(k) for k in ("SMTP_USER", "SMTP_PASSWORD", "MAIL_TO")):
        asunto = (f"GEN-Z {hoy:%d-%m} · {etiqueta(mes_actual)} {pct(g_mes, signo=True)} · "
                  f"{etiqueta(mes_siguiente)}: {cambios_txt}" + (" · ⚠️" if avisos or alertas else ""))
        try:
            enviar_correo(asunto, documento("cid:grafico"), png)
            print("📧 Correo enviado")
        except Exception as e:  # noqa: BLE001
            print(f"⛔ No se ha podido enviar el correo: {e}")
            return 3
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
