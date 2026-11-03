# -*- coding: utf-8 -*-
"""
GEDA — seguimiento diario (para GitHub Actions)
================================================
Genera en salida/: README.md (se ve en GitHub), informe.html (correo), geda_mes_actual.png y asunto.txt, con:
  1. Previsión del mes siguiente: posiciones, cambios y votos.
  2. Mes en curso: posiciones oficiales, rentabilidad provisional y gráfica diaria.
  3. Los dos meses anteriores cerrados: posiciones, rentabilidades y votos.

Motor: motor.py (el mismo de GEDA_limpio, sin cambios). Configuración cerrada (oct-2026):
señal "ambas", ventanas 2-24, regla dinámica de 3 meses, 7 posiciones iguales.
Membresías y divisas: carpeta datos/ (mantenlas al día; ver README).
"""
import os
import sys
import time
import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import motor as M

RAIZ = os.path.dirname(os.path.abspath(__file__))
DATOS = os.path.join(RAIZ, "datos")
SALIDA = os.path.join(RAIZ, "salida")

# ---------------- Configuración cerrada de GEDA ----------------
MESES_MIN = 3
SENAL = "ambas"
INDICES = ['AGG', 'DBC', 'GLD', 'VNQ', 'SPY', 'QQQ', 'IWM', 'VGK']
SECTORES = ['XLP', 'XLV', 'XLK', 'XLE', 'XLB', 'XLI', 'XLY', 'XLC', 'XLU', 'XLF', 'XLRE']
INTENTOS = 3            # reintentos si Yahoo corta la descarga
ESPERA_MINUTOS = 10     # espera entre reintentos

M.RUTA_CACHE_DIVISAS = os.path.join(DATOS, "divisas_cache.json")
M.VERBOSE = False
warnings.filterwarnings("ignore", category=FutureWarning)


def geda(memb_usa, memb_eur):
    comun = {"senal": SENAL}
    acc = {"meses_min": MESES_MIN, "regla": "dinamica", "n": 2, **comun}
    return {
        "Indices": {"tickers": INDICES, "n": 1, **comun},
        "Sectores": {"tickers": SECTORES, "n": 2, **comun},
        "Acciones USA": {"membresias": memb_usa, **acc},
        "Acciones EUROPA": {"membresias": memb_eur, **acc},
    }


def ejecutar_con_reintentos(cfg, start):
    for intento in range(1, INTENTOS + 1):
        try:
            return M.run_multi_strategy(cfg, start, None, True, provisional=True)
        except Exception as e:                      # descarga rota u otro fallo de Yahoo
            print(f"⚠️ Intento {intento}/{INTENTOS} fallido: {e}")
            if intento == INTENTOS:
                raise
            M.borrar_cache_precios()
            time.sleep(ESPERA_MINUTOS * 60)


def pct(x, dec=2):
    return "–" if x is None or pd.isna(x) else f"{x * 100:+.{dec}f}%"


def color(x):
    return "#666" if x is None or pd.isna(x) else ("#0a7d32" if x >= 0 else "#c62828")


def nombre_mes(m):
    m = pd.Timestamp(m)
    return f"{M.MESES_ES[m.month - 1]}-{m.year}"


def main():
    os.makedirs(SALIDA, exist_ok=True)
    memb_usa = M.cargar_membresias(os.path.join(DATOS, "membresias_usa_desde2015.csv"))
    memb_eur = M.cargar_membresias(os.path.join(DATOS, "membresias_europa_desde2015.csv"))

    hoy = M._hoy()
    inicio_mes = hoy.replace(day=1)
    # Señal del primero de los dos meses cerrados = último día del mes anterior a ese
    start = str((inicio_mes - pd.DateOffset(months=2) - pd.Timedelta(days=1)).date())
    print(f"⏳ GEDA | hoy {hoy.date()} | desde {start}")

    tot, _, sub_ret, sub_pos = ejecutar_con_reintentos(geda(memb_usa, memb_eur), start)
    rk = tot.attrs["rankings"]
    mes_act = tot.attrs["mes_provisional"]
    mes_sig = mes_act + pd.offsets.MonthEnd(1)
    cerrados = [mes_act - pd.offsets.MonthEnd(2), mes_act - pd.offsets.MonthEnd(1)]
    bloques = list(rk)

    def seleccion(b, m):
        info = rk[b].get(pd.Timestamp(m))
        return [] if info is None else list(info["seleccion"])

    # ---------- Gráfica diaria del mes en curso ----------
    pesos = {}
    for b in bloques:
        sel = seleccion(b, mes_act)
        n_b = rk[b][mes_act]["n"]
        for t in sel:
            pesos[t] = pesos.get(t, 0) + n_b / 7 / len(sel)
    p = M.load_prices(list(pesos), inicio_mes - pd.Timedelta(days=12), hoy + pd.Timedelta(days=1), True)
    previo = p[p.index < inicio_mes]
    del_mes = p[p.index >= inicio_mes]
    ultimo_cierre = p.index[-1].date()
    hay_grafica, ret_diaria = False, None
    if len(previo) and len(del_mes):
        base = previo.ffill().iloc[-1]
        rel = del_mes.ffill() / base
        rel = rel.fillna(1.0)                       # sin precio: 0 % (como el motor)
        rel = pd.concat([pd.DataFrame(1.0, index=[previo.index[-1]], columns=rel.columns), rel])  # punto 0 %
        cartera = (rel * pd.Series(pesos)).sum(axis=1) + (1 - sum(pesos.values()))
        ret_diaria = cartera - 1
        dif = abs(ret_diaria.iloc[-1] - tot.loc[mes_act])
        if dif > 0.0005:
            print(f"⚠️ La gráfica ({ret_diaria.iloc[-1]:.2%}) no cuadra con el motor ({tot.loc[mes_act]:.2%})")
        fig, ax = plt.subplots(figsize=(10, 5.5))
        for t in rel.columns:
            ax.plot(rel.index, (rel[t] - 1) * 100, lw=1.1, alpha=0.75, label=t)
        ax.plot(ret_diaria.index, ret_diaria * 100, lw=3, color="black", label="GEDA")
        ax.axhline(0, color="#999", lw=0.8)
        ax.set_title(f"GEDA — {nombre_mes(mes_act)} (provisional, hasta {ultimo_cierre})")
        ax.set_ylabel("Rentabilidad del mes (%)")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8, ncol=4, loc="upper left")
        import matplotlib.dates as mdates
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d-%m"))
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, len(rel) // 12)))
        fig.autofmt_xdate()
        fig.tight_layout()
        ruta_png = os.path.join(SALIDA, "geda_mes_actual.png")
        fig.savefig(ruta_png, dpi=110)
        plt.close(fig)
        hay_grafica = True

    # ---------- Datos comunes de los informes ----------
    def filas_mes(m):
        filas = []
        for b in bloques:
            sel = seleccion(b, m)
            rets = sub_pos[b].get(m, {})
            for t in sel:
                filas.append((b, t, rk[b][m]["n"] / 7 / len(sel), rets.get(t, np.nan)))
        return filas

    prevision = []
    for b in bloques:
        nuevo, actual = seleccion(b, mes_sig), seleccion(b, mes_act)
        prevision.append({"bloque": b, "posiciones": nuevo,
                          "entra": [t for t in nuevo if t not in actual],
                          "sale": [t for t in actual if t not in nuevo],
                          "alerta": M._alerta_margen(rk[b][mes_sig])})
    hay_cambios = any(x["entra"] or x["sale"] for x in prevision)
    generado = pd.Timestamp.now(tz="Europe/Madrid")
    cab = (f"Datos hasta el cierre del {ultimo_cierre}. Generado el {generado:%d-%m-%Y %H:%M} (hora de Madrid). "
           f"Rentabilidades en EUR, sin comisiones ni impuestos.")
    meses_v = [mes_sig, mes_act] + list(reversed(cerrados))

    def votos_texto(info):
        partes = [f"{'✔ ' if t in info['seleccion'] else ''}{t} {int(v)}" for t, v in info["votos"].head(M.N_RANKING).items()]
        mg, al = M._margen(info), M._alerta_margen(info)
        return partes, ("" if mg is None else f"margen {info['n']}º-{info['n'] + 1}º: {mg}" + (f" ⚠️ {al}" if al else ""))

    # ---------- HTML (correo) ----------
    css = ("body{font-family:Arial,sans-serif;color:#222}table{border-collapse:collapse;margin:8px 0 18px}"
           "td,th{border:1px solid #ccc;padding:5px 9px;vertical-align:top;font-size:14px}th{background:#f2f4f8}"
           "h2{margin-top:28px;border-bottom:2px solid #1f4fd1}.nota{color:#666;font-size:13px}")
    h = [f"<!DOCTYPE html><html lang='es'><head><meta charset='utf-8'><style>{css}</style></head><body>",
         f"<h1>GEDA — seguimiento diario</h1><p class='nota'>{cab}</p>",
         f"<h2>1. Previsión para {nombre_mes(mes_sig)}</h2>"
         "<p class='nota'>Calculada como si el mes terminase hoy. Puede cambiar hasta el último cierre del mes.</p>"
         "<table><tr><th>Bloque</th><th>Posiciones previstas</th><th>Cambios frente al mes en curso</th></tr>"]
    for x in prevision:
        c = " ".join([f"<b style='color:#0a7d32'>+{t}</b>" for t in x["entra"]] +
                     [f"<b style='color:#c62828'>−{t}</b>" for t in x["sale"]]) or "sin cambios"
        if x["alerta"]:
            c += f" ⚠️ {x['alerta']}"
        h.append(f"<tr><td><b>{x['bloque']}</b></td><td>{', '.join(x['posiciones'])}</td><td>{c}</td></tr>")
    h.append("</table>")

    def html_mes(m, titulo):
        r = tot.loc[m]
        h.append(f"<h2>{titulo}</h2><p>Rentabilidad GEDA: <b style='color:{color(r)};font-size:20px'>{pct(r)}</b></p>"
                 "<table><tr><th>Bloque</th><th>Posición</th><th>Peso</th><th>Rentabilidad</th></tr>")
        for b, t, peso, rr in filas_mes(m):
            h.append(f"<tr><td>{b}</td><td>{t}</td><td>{peso:.1%}</td><td style='color:{color(rr)}'>{pct(rr)}</td></tr>")
        h.append("</table>")

    html_mes(mes_act, f"2. Mes en curso: {nombre_mes(mes_act)} (provisional)")
    h.append("<img src='cid:grafica' style='max-width:100%' alt='Rentabilidad diaria del mes'>" if hay_grafica
             else "<p class='nota'>Todavía no hay cierres de este mes.</p>")
    h.append("<h2>3. Meses anteriores cerrados</h2>")
    for m in reversed(cerrados):
        html_mes(m, nombre_mes(m))
    h.append("<h2>4. Votos (top 5)</h2><table><tr><th>Bloque</th>" +
             "".join(f"<th>{nombre_mes(m)}{'<br>(' + rk[bloques[0]][m]['tipo'] + ')' if rk[bloques[0]][m]['tipo'] else ''}</th>"
                     for m in meses_v) + "</tr>")
    for b in bloques:
        celdas = []
        for m in meses_v:
            partes, mg = votos_texto(rk[b][m])
            celdas.append("<td>" + "<br>".join(partes) + (f"<br><span class='nota'>{mg}</span>" if mg else "") + "</td>")
        h.append(f"<tr><td><b>{b}</b></td>{''.join(celdas)}</tr>")
    h.append(f"</table><p class='nota'>Máximo {rk[bloques[0]][mes_sig]['max']} votos por valor. ✔ = elegido. "
             "AJUSTADO = margen pequeño entre el último elegido y el primero que queda fuera.</p></body></html>")
    with open(os.path.join(SALIDA, "informe.html"), "w", encoding="utf-8") as f:
        f.write("\n".join(h))

    # ---------- Markdown (se ve directamente en GitHub) ----------
    md = [f"# GEDA — seguimiento diario", "", f"_{cab}_", "",
          f"## 1. Previsión para {nombre_mes(mes_sig)}", "",
          "_Calculada como si el mes terminase hoy. Puede cambiar hasta el último cierre del mes._", "",
          "| Bloque | Posiciones previstas | Cambios frente al mes en curso |", "|---|---|---|"]
    for x in prevision:
        c = " ".join([f"**+{t}**" for t in x["entra"]] + [f"**−{t}**" for t in x["sale"]]) or "sin cambios"
        if x["alerta"]:
            c += f" ⚠️ {x['alerta']}"
        md.append(f"| {x['bloque']} | {', '.join(x['posiciones'])} | {c} |")

    def md_mes(m, titulo):
        md.extend(["", f"## {titulo}", "", f"**Rentabilidad GEDA: {pct(tot.loc[m])}**", "",
                   "| Bloque | Posición | Peso | Rentabilidad |", "|---|---|---|---|"])
        for b, t, peso, rr in filas_mes(m):
            md.append(f"| {b} | {t} | {peso:.1%} | {pct(rr)} |")

    md_mes(mes_act, f"2. Mes en curso: {nombre_mes(mes_act)} (provisional)")
    md.extend(["", "![Rentabilidad diaria del mes](geda_mes_actual.png)" if hay_grafica
               else "_Todavía no hay cierres de este mes._"])
    if ret_diaria is not None:
        md.extend(["", "<details><summary>Rentabilidad acumulada día a día</summary>", "",
                   "| Día | GEDA |", "|---|---|"] +
                  [f"| {d:%d-%m} | {pct(v)} |" for d, v in ret_diaria.iloc[1:].items()] + ["", "</details>"])
    md.extend(["", "## 3. Meses anteriores cerrados"])
    for m in reversed(cerrados):
        md_mes(m, nombre_mes(m))
    md.extend(["", "## 4. Votos (top 5)", "",
               "| Bloque | " + " | ".join(f"{nombre_mes(m)}{' (' + rk[bloques[0]][m]['tipo'] + ')' if rk[bloques[0]][m]['tipo'] else ''}"
                                       for m in meses_v) + " |",
               "|---|" + "---|" * len(meses_v)])
    for b in bloques:
        celdas = []
        for m in meses_v:
            partes, mg = votos_texto(rk[b][m])
            celdas.append("<br>".join(partes) + (f"<br>_{mg}_" if mg else ""))
        md.append(f"| **{b}** | " + " | ".join(celdas) + " |")
    md.extend(["", f"_Máximo {rk[bloques[0]][mes_sig]['max']} votos por valor. ✔ = elegido. "
                   "AJUSTADO = margen pequeño entre el último elegido y el primero que queda fuera._"])
    with open(os.path.join(SALIDA, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    # ---------- Resumen para el asunto del correo ----------
    cambios_txt = " ".join([f"+{t}" for x in prevision for t in x["entra"]] +
                           [f"−{t}" for x in prevision for t in x["sale"]]) or "sin cambios"
    asunto = (f"GEDA {ultimo_cierre:%d-%m}: {nombre_mes(mes_act)} {pct(tot.loc[mes_act])} · "
              f"previsión {nombre_mes(mes_sig)}: {cambios_txt}")
    with open(os.path.join(SALIDA, "asunto.txt"), "w", encoding="utf-8") as f:
        f.write(asunto)
    print(f"✅ {asunto}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"⛔ ERROR: {e}")
        sys.exit(1)
