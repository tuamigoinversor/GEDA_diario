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
        serie = ret_diaria.iloc[1:] * 100            # días del mes (sin el punto de partida)
        marcas = {serie.index[-1]: "último"}
        marcas.setdefault(serie.idxmax(), "máx.")
        marcas.setdefault(serie.idxmin(), "mín.")
        for d, etiqueta in marcas.items():
            v = serie.loc[d]
            arriba = v >= serie.median()
            ax.scatter([d], [v], color="black", zorder=5, s=28)
            ax.annotate(f"{v:+.2f}%" + ("" if etiqueta == "último" else f" ({etiqueta})"), (d, v),
                        textcoords="offset points", xytext=(0, 10 if arriba else -16), ha="center",
                        fontsize=10, fontweight="bold",
                        bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.85))
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

    # ---------- Informe con el formato habitual (motor: generate_email_report) ----------
    prevision = [{"entra": [t for t in seleccion(b, mes_sig) if t not in seleccion(b, mes_act)],
                  "sale": [t for t in seleccion(b, mes_act) if t not in seleccion(b, mes_sig)]} for b in bloques]
    generado = pd.Timestamp.now(tz="Europe/Madrid")
    cab = (f"Datos hasta el cierre del {ultimo_cierre:%d-%m-%Y}. Generado el {generado:%d-%m-%Y %H:%M} "
           f"(hora de Madrid). Rentabilidades en EUR, sin comisiones ni impuestos.")
    cuerpo = M.generate_email_report(sub_pos, tot, "GEDA (en EUR)", 4)
    cuerpo = cuerpo.replace("<html><body>", "").replace("</body></html>", "")
    titulo_graf = f"<h3>Rentabilidad diaria de {nombre_mes(mes_act)} (provisional)</h3>"

    html = (f"<!DOCTYPE html><html lang='es'><head><meta charset='utf-8'></head>"
            f"<body style='font-family:Arial,sans-serif;'><p style='color:#666;font-size:13px'>{cab}</p>{cuerpo}"
            + (f"{titulo_graf}<img src='cid:grafica' style='max-width:100%' alt='Rentabilidad diaria'>" if hay_grafica
               else "<p><i>Todavía no hay cierres de este mes.</i></p>") + "</body></html>")
    with open(os.path.join(SALIDA, "informe.html"), "w", encoding="utf-8") as f:
        f.write(html)

    md = (f"_{cab}_\n\n{cuerpo}\n\n"
          + (f"{titulo_graf}\n\n![Rentabilidad diaria](geda_mes_actual.png)\n" if hay_grafica
             else "<p><i>Todavía no hay cierres de este mes.</i></p>\n"))
    with open(os.path.join(SALIDA, "README.md"), "w", encoding="utf-8") as f:
        f.write(md)

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
