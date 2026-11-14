# =========================================================
# COPIA LITERAL de la celda 2 ("Motor") de GENZ_que_comprar.ipynb.
# Es el motor de GEDA más el límite por tema (grupos / max_por_grupo). Sin límite da lo mismo que GEDA.
# No se toca aquí: si cambias el motor en el notebook, vuelve a copiarlo entero.
# =========================================================
# -*- coding: utf-8 -*-
"""
MOTOR v2 — Estrategias cuantitativas TuAmigoInversor
=====================================================
Sustituye a la celda de funciones de la v1.

Cambios respecto a v1
---------------------
1. DIVISAS: conversión a EUR de cualquier divisa (USD, GBP/GBp, CHF, AUD, CAD, SEK,
   NOK, PLN, BRL, ...). Ya no hay "USD por defecto" silencioso: si no se puede detectar
   la divisa de un ticker, el programa se para y te dice cuál. Las divisas se guardan
   en un JSON (caché) para no consultarlas en cada ejecución; puedes corregirlo a mano.
2. FECHA DE CORTE EXPLÍCITA: las señales se calculan siempre con el último cierre de
   mes completo, da igual el día u hora en que ejecutes. Desaparecen las dos filas
   ficticias de la v1. La última fila de cada resultado es el MES SIGUIENTE
   (posiciones nuevas, rentabilidad todavía vacía).
3. UNIVERSOS POR FECHA: en cada fin de mes solo son elegibles los valores que están
   en algún índice ese día y llevan dentro más de `meses_min` meses (3 por defecto).
4. SALTAR EL ÚLTIMO MES (opcional): `saltar_meses=1` calcula el momentum con precios
   hasta el mes anterior (estilo 12-1). Por defecto 0 (igual que v1).
5. RENDIMIENTO: top-n de cada ventana se calcula una sola vez (mismo resultado).
6. Avisos: tickers sin datos, precios congelados, empates en las posiciones nuevas y
   meses con alguna subestrategia sin dato.

Lo que NO cambia: el método de señales (pendiente del log-precio y pendiente·R²,
ventanas 2-24, ensemble jerárquico con peso 1/sqrt(w), meta-votación), el lag de un
mes, el tratamiento de empates y la ausencia de filtro de momentum absoluto.
"""

import json
import os
import warnings

import numpy as np
import pandas as pd
import yfinance as yf

# =========================================================
# PARÁMETROS GLOBALES (se pueden sobrescribir en la celda de ejecución)
# =========================================================
VERBOSE = False
RUTA_CACHE_DIVISAS = "divisas_cache.json"   # en Colab, mejor una ruta de Drive
MESES_LOOKBACK = 25                          # meses de histórico previo al start
VENTANAS = range(2, 25)                      # ventanas de momentum (meses)
DIAS_PRECIO_CONGELADO = 10                   # aviso si un ticker no cotiza desde hace más
N_RANKING = 5                                # candidatos que se muestran en el ranking de votos
UMBRAL_MARGEN = 0.10                         # ⚠️ si el margen último elegido/primero fuera es ≤ 10% del máximo de votos

# Divisas que Yahoo da en subunidades (peniques, agorot, céntimos de rand)
SUBUNIDADES = {"GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0),
               "ILA": ("ILS", 100.0), "ZAc": ("ZAR", 100.0)}


MAX_FALLO_DESCARGA = 0.40   # si falla más de este % de un grupo (≥10 tickers) → se detiene: descarga rota
HOY = None   # solo para pruebas: fija el "hoy" del programa (None = fecha real)


def _log(msg):
    if VERBOSE:
        print(msg)


def _hoy():
    return (pd.Timestamp.today() if HOY is None else pd.Timestamp(HOY)).normalize()


# =========================================================
# FECHAS
# =========================================================
def fecha_corte_por_defecto(hoy=None):
    """Día 1 del mes actual. Como `end` es exclusivo en yfinance, los datos llegan
    hasta el último día del mes anterior (último cierre de mes completo)."""
    hoy = pd.Timestamp.today() if hoy is None else pd.Timestamp(hoy)
    return hoy.normalize().replace(day=1)


def _normalizar_corte(fecha_corte):
    if fecha_corte is None:
        return fecha_corte_por_defecto()
    fc = pd.Timestamp(fecha_corte).normalize()
    if fc.day != 1:
        nuevo = fc.replace(day=1)
        warnings.warn(f"fecha_corte={fc.date()} no es día 1. Se usa {nuevo.date()} para "
                      f"no calcular señales con un mes incompleto.")
        fc = nuevo
    return fc


# =========================================================
# DESCARGA Y DIVISAS
# =========================================================
def _descargar_cierres(tickers, start, end):
    """Adj Close diario de una lista de tickers. Columnas = tickers."""
    tickers = list(dict.fromkeys(t.strip() for t in tickers))
    raw = yf.download(tickers=tickers, start=start, end=end, auto_adjust=False,
                      progress=False, multi_level_index=True)
    if raw is None or raw.empty:
        return pd.DataFrame()
    df = raw["Adj Close"]
    if isinstance(df, pd.Series):
        df = df.to_frame(tickers[0])
    df = df.copy()
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    df = df.sort_index()
    df = df[df.index < pd.Timestamp(end)]       # defensa: nunca datos del mes en curso
    return df


def _descargar_fx(divisa, start, end):
    """Serie EUR{divisa}=X  (= unidades de `divisa` por 1 EUR)."""
    par = f"EUR{divisa}=X"
    fx = _descargar_cierres([par], start, end)
    if fx.empty or par not in fx.columns or fx[par].dropna().empty:
        raise ValueError(f"No hay datos del tipo de cambio {par}: no se puede convertir {divisa} a EUR.")
    return fx[par].ffill()


def _leer_cache_divisas(ruta):
    if ruta and os.path.exists(ruta):
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _guardar_cache_divisas(cache, ruta):
    if ruta:
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(dict(sorted(cache.items())), f, indent=1, ensure_ascii=False)


def detectar_divisas(tickers, ruta_cache=None):
    """Divisa de cotización de cada ticker. Usa la caché JSON y solo consulta Yahoo
    por los que faltan. Si alguno falla, PARA (no supone USD)."""
    ruta_cache = RUTA_CACHE_DIVISAS if ruta_cache is None else ruta_cache
    cache = _leer_cache_divisas(ruta_cache)
    fallos, nuevos = [], 0
    for t in tickers:
        if t in cache:
            continue
        try:
            cur = yf.Ticker(t).fast_info["currency"]
        except Exception:
            cur = None
        if not cur:
            fallos.append(t)
            continue
        cache[t] = cur
        nuevos += 1
    if nuevos:
        _guardar_cache_divisas(cache, ruta_cache)
    if fallos:
        raise ValueError(
            f"No se ha podido detectar la divisa de: {fallos}. "
            f"Vuelve a ejecutar (puede ser un límite temporal de Yahoo) o añádela a mano "
            f'en {ruta_cache}, por ejemplo: "PLS.AX": "AUD".')
    return {t: cache[t] for t in tickers}


def convertir_a_eur(df, divisas, start, end):
    """Convierte cada columna a EUR con el tipo de cambio de ese mismo día."""
    out = df.copy()
    fx_cache, resumen = {}, {}
    for t in out.columns:
        cur = divisas[t]
        resumen.setdefault(cur, []).append(t)
        if cur == "EUR":
            continue
        base, factor = SUBUNIDADES.get(cur, (cur.upper(), 1.0))
        if base not in fx_cache:
            fx_cache[base] = _descargar_fx(base, start, end)
        out[t] = out[t] / factor / fx_cache[base].reindex(out.index, method="ffill")
    if VERBOSE:
        print("💱 Divisas: " + " | ".join(f"{k}: {len(v)}" for k, v in sorted(resumen.items())))
        otras = {k: v for k, v in resumen.items() if k not in ("EUR", "USD")}
        for k, v in sorted(otras.items()):
            print(f"   {k}: {v}")
    return out


def _avisar_precios_congelados(raw, end):
    limite = pd.Timestamp(end) - pd.Timedelta(days=DIAS_PRECIO_CONGELADO)
    ultimos = raw.apply(lambda s: s.last_valid_index())
    congelados = {t: d.date() for t, d in ultimos.items() if d is not None and d < limite}
    if congelados:
        warnings.warn(f"Tickers sin cotizar en los últimos {DIAS_PRECIO_CONGELADO} días "
                      f"(su precio queda congelado): {congelados}")


_CACHE_PRECIOS = {}   # {(tickers, eur): (start, end, df)} para no repetir descargas entre variantes


def borrar_cache_precios():
    _CACHE_PRECIOS.clear()


def load_prices(tickers, start, end, convert_to_eur=False):
    if isinstance(tickers, str):
        tickers = [tickers]
    tickers = list(dict.fromkeys(t.strip() for t in tickers))
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    clave = (tuple(sorted(tickers)), bool(convert_to_eur))
    if clave in _CACHE_PRECIOS:
        c_start, c_end, c_df = _CACHE_PRECIOS[clave]
        if c_start <= start and c_end >= end:
            _log("♻️ Precios reutilizados de la caché")
            return c_df[(c_df.index >= start) & (c_df.index < end)]
    df = _load_prices_yahoo(tickers, start, end, convert_to_eur)
    _CACHE_PRECIOS[clave] = (start, end, df)
    return df


def _load_prices_yahoo(tickers, start, end, convert_to_eur=False):
    raw = _descargar_cierres(tickers, start, end)

    sin_datos = [t for t in tickers if t not in raw.columns or raw[t].dropna().empty]
    if len(tickers) >= 10 and len(sin_datos) / len(tickers) > MAX_FALLO_DESCARGA:
        raise RuntimeError(
            f"⛔ DESCARGA ROTA: faltan {len(sin_datos)} de {len(tickers)} tickers "
            f"({len(sin_datos)/len(tickers):.0%}). Lo normal es menos del 20%. Seguramente Yahoo te ha "
            f"limitado (Too Many Requests). Espera 30-60 minutos, REINICIA LA SESIÓN y vuelve a ejecutar. "
            f"No se usa ningún resultado con estos datos.")
    if sin_datos:
        warnings.warn(f"Sin datos en Yahoo, se excluyen: {sin_datos}")
    validos = [t for t in tickers if t not in sin_datos]
    if not validos:
        raise ValueError(f"Ningún ticker con datos: {tickers}")
    raw = raw[validos]
    _avisar_precios_congelados(raw, end)

    df = raw.ffill().dropna(how="all")          # mismo orden que v1: ffill y luego divisa
    if convert_to_eur:
        divisas = detectar_divisas(validos)
        df = convertir_a_eur(df, divisas, start, end)
    return df.dropna(how="all")


# =========================================================
# UNIVERSOS POR FECHA (MEMBRESÍAS)
# =========================================================
def membresias_desde_snapshots(snapshots, indice="INDICE", altas=None):
    """
    Construye la tabla de membresías (ticker, indice, alta, baja) a partir de listas
    con fecha de vigencia: {"2026-01-01": [...], "2026-07-01": [...], ...}.

    - Si un ticker está en la primera lista, su alta es desconocida (NaT) y se considera
      dentro "desde siempre".
    - Si aparece en una lista posterior, su alta = fecha de esa lista.
    - Si desaparece, su baja = fecha de la primera lista en la que ya no está.
    - `altas` = {ticker: fecha} fija altas conocidas (sobrescribe o añade).

    OJO: la precisión es la de las fechas de las listas. Lo ideal es corregir el CSV
    exportado con las fechas reales de inclusión/exclusión.
    """
    conjuntos = {pd.Timestamp(f): {t.strip() for t in lst} for f, lst in snapshots.items()}
    fechas = sorted(conjuntos)
    todos = set().union(*conjuntos.values())
    filas = []
    for t in sorted(todos):
        dentro, alta = False, pd.NaT
        for i, f in enumerate(fechas):
            presente = t in conjuntos[f]
            if presente and not dentro:
                alta = pd.NaT if i == 0 else f
                dentro = True
            elif not presente and dentro:
                filas.append((t, indice, alta, f))
                dentro = False
        if dentro:
            filas.append((t, indice, alta, pd.NaT))
    df = pd.DataFrame(filas, columns=["ticker", "indice", "alta", "baja"])
    for col in ("alta", "baja"):
        df[col] = pd.to_datetime(df[col])
    for t, f in (altas or {}).items():
        t, f = t.strip(), pd.Timestamp(f)
        m = df["ticker"] == t
        if m.any():
            df.loc[df[m].index[-1], "alta"] = f
        else:
            df.loc[len(df)] = [t, indice, f, pd.NaT]
    for col in ("alta", "baja"):
        df[col] = pd.to_datetime(df[col])
    return df.sort_values(["ticker", "alta"], na_position="first").reset_index(drop=True)


def diagnostico_snapshots(snapshots, nombre=""):
    """Imprime altas y bajas entre listas consecutivas, para revisar las fechas."""
    conjuntos = {pd.Timestamp(f): {t.strip() for t in lst} for f, lst in snapshots.items()}
    fechas = sorted(conjuntos)
    print(f"\n🔎 Cambios entre listas {nombre}")
    print(f"   {fechas[0].date()}: {len(conjuntos[fechas[0]])} valores (lista inicial)")
    for a, b in zip(fechas[:-1], fechas[1:]):
        altas = sorted(conjuntos[b] - conjuntos[a])
        bajas = sorted(conjuntos[a] - conjuntos[b])
        print(f"   {b.date()}: {len(conjuntos[b])} valores | ALTAS {altas} | BAJAS {bajas}")


def exportar_membresias(df, ruta):
    df.to_csv(ruta, index=False, date_format="%Y-%m-%d")


def cargar_membresias(ruta):
    df = pd.read_csv(ruta, parse_dates=["alta", "baja"])
    df["ticker"] = df["ticker"].astype(str).str.strip()
    return df


def matriz_elegibilidad(membresias, fechas, columnas, meses_min=3, regla="dinamica"):
    """
    regla="dinamica": True si el ticker está en algún índice ese fin de mes y lleva dentro
                      MÁS de `meses_min` meses.
    regla="anual":    lista fija a 1 de enero. Para las posiciones de cada año vale la lista de
                      valores que estaban en algún índice el 1 de enero de ese año, todo el año
                      (aunque salgan del índice). meses_min no se usa.
    """
    fechas = pd.DatetimeIndex(fechas)
    E = pd.DataFrame(False, index=fechas, columns=list(columnas))
    if regla == "anual":
        # año de las posiciones que se deciden con esta fecha (la del mes siguiente)
        ano_pos = (fechas + pd.offsets.MonthEnd(0) + pd.offsets.MonthEnd(1)).year
        ref = pd.DatetimeIndex([pd.Timestamp(year=a, month=1, day=1) for a in ano_pos])
    elif regla != "dinamica":
        raise ValueError(f"regla desconocida: {regla}")
    for r in membresias.itertuples(index=False):
        if r.ticker not in E.columns:
            continue
        ok = np.ones(len(fechas), dtype=bool)
        if regla == "anual":
            if pd.notna(r.alta):
                ok &= r.alta <= ref
            if pd.notna(r.baja):
                ok &= r.baja > ref
        else:
            if pd.notna(r.alta):
                ok &= fechas > (r.alta + pd.DateOffset(months=meses_min))
            if pd.notna(r.baja):
                ok &= fechas < r.baja
        E.loc[ok, r.ticker] = True
    return E


# =========================================================
# MOMENTUM (idéntico a v1)
# =========================================================
def rolling_slope_vectorized(df, window):
    """Pendiente del log-precio y pendiente·R² para cada ticker sobre `window` meses.
    Mismo cálculo que la v1 (ver _rolling_slope_bucle), vectorizado con numpy."""
    v = df.values.astype(float)
    v[~(v > 0)] = np.nan          # precios ≤ 0 (errores de Yahoo) no son válidos: se ignoran
    logp = np.log(v)
    n, m = logp.shape
    slopes = np.full((n, m), np.nan)
    slopes_r2 = np.full((n, m), np.nan)
    if n >= window:
        x = np.arange(window)
        xc = x - x.mean()
        denom = (xc ** 2).sum()
        Y = np.lib.stride_tricks.sliding_window_view(logp, window, axis=0)   # (n-w+1, m, w)
        Yc = Y - Y.mean(axis=2, keepdims=True)
        cov = (Yc * xc).sum(axis=2)
        var_y = (Yc ** 2).sum(axis=2)
        with np.errstate(divide="ignore", invalid="ignore"):
            r2 = np.where(var_y != 0, cov ** 2 / (denom * var_y), 0.0)
        s = cov / denom
        slopes[window - 1:] = s
        slopes_r2[window - 1:] = s * r2
    return (pd.DataFrame(slopes, index=df.index, columns=df.columns),
            pd.DataFrame(slopes_r2, index=df.index, columns=df.columns))


def _rolling_slope_bucle(df, window):
    """Versión original (bucle) de la v1. Se conserva solo para verificar la vectorizada."""
    logp = np.log(df.values)
    n, m = logp.shape
    slopes = np.full((n, m), np.nan)
    slopes_r2 = np.full((n, m), np.nan)
    x = np.arange(window)
    x_mean = x.mean()
    denom = ((x - x_mean) ** 2).sum()
    for col in range(m):
        for i in range(window - 1, n):
            y = logp[i - window + 1:i + 1, col]
            y_mean = y.mean()
            cov = ((x - x_mean) * (y - y_mean)).sum()
            slope = cov / denom
            var_y = ((y - y_mean) ** 2).sum()
            r2 = (cov ** 2) / (denom * var_y) if var_y != 0 else 0
            slopes[i, col] = slope
            slopes_r2[i, col] = slope * r2
    return (pd.DataFrame(slopes, index=df.index, columns=df.columns),
            pd.DataFrame(slopes_r2, index=df.index, columns=df.columns))


def top_n_signals(df, n):
    """1 a los n mayores de cada fila (incluye empatados). Ignora NaN.
    Mismo resultado que la v1 (ver _top_n_bucle), vectorizado con numpy."""
    A = df.to_numpy(dtype=float)
    if A.size == 0:
        return pd.DataFrame(0, index=df.index, columns=df.columns)
    nan = np.isnan(A)
    k = (~nan).sum(axis=1)
    orden = -np.sort(-np.where(nan, -np.inf, A), axis=1)          # descendente, NaN al final
    pos = np.clip(np.minimum(n, k) - 1, 0, None)
    umbral = orden[np.arange(A.shape[0]), pos]
    with np.errstate(invalid="ignore"):
        sel = (A >= umbral[:, None]) & ~nan & (k[:, None] > 0)
    return pd.DataFrame(sel.astype(int), index=df.index, columns=df.columns)


def top_n_por_grupo(df, n, grupos, max_por_grupo):
    """Como top_n_signals pero con un máximo de `max_por_grupo` valores por grupo (tema).
    Recorre los candidatos de más a menos votos y salta los de un grupo ya lleno.
    Empates: se deshacen por orden alfabético del ticker (elige exactamente n).
    Los tickers sin grupo cuentan como grupo propio."""
    out = pd.DataFrame(0, index=df.index, columns=df.columns)
    for d in df.index:
        fila = df.loc[d].dropna()
        if fila.empty:
            continue
        orden = sorted(fila.index, key=lambda t: (-fila[t], t))
        cuenta, elegidos = {}, []
        for t in orden:
            g = grupos.get(t, t)
            if cuenta.get(g, 0) >= max_por_grupo:
                continue
            elegidos.append(t)
            cuenta[g] = cuenta.get(g, 0) + 1
            if len(elegidos) == n:
                break
        out.loc[d, elegidos] = 1
    return out


def _top_n_bucle(df, n):
    """Versión original (bucle) de la v1. Se conserva solo para verificar la vectorizada."""
    out = pd.DataFrame(0, index=df.index, columns=df.columns)
    for i in range(len(df)):
        row = df.iloc[i].dropna()
        if len(row) == 0:
            continue
        sorted_vals = row.sort_values(ascending=False)
        threshold = sorted_vals.iloc[-1] if len(sorted_vals) < n else sorted_vals.iloc[n - 1]
        selected = row[row >= threshold].index
        out.loc[df.index[i], selected] = 1
    return out


# =========================================================
# MÉTRICAS
# =========================================================
def compute_metrics(returns):
    returns = returns.dropna()
    cagr = (1 + returns).prod() ** (12 / len(returns)) - 1
    sharpe = returns.mean() / returns.std() * np.sqrt(12)
    cum = (1 + returns).cumprod()
    max_dd = (cum / cum.cummax() - 1).min()
    total = (1 + returns).prod() - 1
    return {"Meses": len(returns), "Acumulado": f"{total*100:.2f}%", "CAGR": f"{cagr*100:.2f}%",
            "Sharpe": f"{sharpe:.2f}", "MaxDD": f"{max_dd*100:.2f}%"}


# =========================================================
# BACKTEST
# =========================================================
class Backtester:

    def run(self, tickers, start, fecha_corte=None, n_top=1, convert_to_eur=False,
            membresias=None, meses_min=3, saltar_meses=0, precios=None, provisional=False,
            ventanas=None, regla="dinamica", senal="ambas", grupos=None, max_por_grupo=None):
        """
        Devuelve (strategy_returns, active_positions).
        La última fecha es el mes siguiente: rentabilidad NaN y posiciones nuevas.
        - membresias: tabla de membresías (universo por fecha) o None (lista fija).
        - saltar_meses: 0 = como v1; 1 = momentum sin el último mes.
        - ventanas: ventanas de momentum en meses (por defecto VENTANAS = 2..24).
        - regla: "dinamica" (meses_min dentro del índice) o "anual" (lista fija a 1 de enero).
          range(3, 25) quita la ventana de 2 meses (= rentabilidad del último mes).
        - senal: "ambas" (oficial: pendiente + pendiente·R²), "pendiente" (solo pendiente)
          o "r2" (solo pendiente·R²). Mismas ventanas y misma meta-votación en los tres casos.
        - grupos / max_por_grupo: {ticker: tema} y máximo de posiciones por tema en la
          selección final (None = sin límite, como en GEDA).
        Tras ejecutar quedan en el objeto:
        - self.ranking:  {mes de posiciones: votos top N, elegidos, n, máximo, tipo}
        - self.universo: {mes de posiciones: {"efectivo": [...], "sin_datos": [...]}}
        - precios: precios diarios ya descargados (para pruebas).
        - provisional: True = usa también los datos del mes en curso (hasta hoy).
            · El mes en curso lleva su rentabilidad PROVISIONAL (posiciones oficiales,
              calculadas con el cierre del mes anterior: no cambian).
            · La última fila es la PREVISIÓN del mes siguiente, calculada como si el
              mes en curso terminase hoy. Puede cambiar hasta el cierre.
            Solo se aplica si fecha_corte es None.
        """
        provisional = provisional and fecha_corte is None
        self.ranking = {}
        self.universo = {}
        ventanas = list(VENTANAS if ventanas is None else ventanas)
        if senal not in ("ambas", "pendiente", "r2"):
            raise ValueError(f"senal desconocida: {senal} (usa 'ambas', 'pendiente' o 'r2')")
        usar_pend, usar_r2 = senal in ("ambas", "pendiente"), senal in ("ambas", "r2")
        hoy = _hoy()
        fc = _normalizar_corte(fecha_corte) if not provisional else hoy + pd.Timedelta(days=1)
        start_dt = pd.to_datetime(start)
        # +1 mes fijo: cubre saltar_meses=1 y permite reutilizar la misma descarga en todas las variantes
        warmup_start = start_dt - pd.DateOffset(months=MESES_LOOKBACK + max(1, saltar_meses))

        if membresias is not None and not tickers:
            tickers = sorted(set(membresias["ticker"]))
        pedidos = list(dict.fromkeys(t.strip() for t in ([tickers] if isinstance(tickers, str) else tickers)))

        prices = precios if precios is not None else load_prices(pedidos, warmup_start, fc, convert_to_eur)
        prices = prices[prices.index < fc]
        sin_datos_yahoo = [t for t in pedidos if t not in prices.columns]

        if not provisional:
            esperado = fc - pd.offsets.MonthEnd(1)
            if prices.index[-1] < esperado - pd.Timedelta(days=4):
                warnings.warn(f"El último precio disponible es {prices.index[-1].date()}, "
                              f"pero el cierre de mes es {esperado.date()}. ¿Faltan datos?")

        monthly_prices = prices.resample("ME").last()
        if provisional:
            # Todas las subestrategias deben acabar en el mes en curso (fondos con retraso de datos)
            fin_mes = hoy + pd.offsets.MonthEnd(0)
            sin_datos_mes = [t for t in prices.columns
                             if prices[t].last_valid_index() is None
                             or prices[t].last_valid_index() < fin_mes.replace(day=1)]
            if sin_datos_mes:
                warnings.warn(f"Sin precios en el mes en curso (provisional = 0%): {sin_datos_mes}")
            monthly_prices = monthly_prices.reindex(
                pd.date_range(monthly_prices.index[0], fin_mes, freq="ME")).ffill()
        monthly_returns = monthly_prices.pct_change(fill_method=None)
        fecha_sig = monthly_prices.index[-1] + pd.offsets.MonthEnd(1)
        tickers_ok = list(monthly_prices.columns)

        # ===============================
        # Caso simple: n_top >= número de tickers → todos
        # ===============================
        if n_top >= len(tickers_ok):
            _log("⚡ Número de tickers <= n_top → usamos todos sin filtrar")
            signals = pd.DataFrame(1.0, index=monthly_prices.index, columns=tickers_ok).shift(1)
            signals.loc[fecha_sig] = 1.0
            rets = monthly_returns.reindex(signals.index)
            signals = signals.loc[signals.index >= start_dt]
            rets = rets.loc[signals.index]
            strategy_returns = (signals * rets).mean(axis=1)
            strategy_returns.loc[fecha_sig] = np.nan
            active_positions = {}
            for d in signals.index:
                if d == fecha_sig:
                    active_positions[d] = {t: np.nan for t in tickers_ok}
                else:
                    active_positions[d] = {t: rets.loc[d, t] for t in tickers_ok if not pd.isna(rets.loc[d, t])}
                self.universo[d] = {"efectivo": list(tickers_ok), "sin_datos": sin_datos_yahoo}
            return strategy_returns, active_positions

        # =====================================================
        # Universo por fecha
        # =====================================================
        fechas_senal = monthly_prices.index[monthly_prices.index >= start_dt]
        E = None
        if membresias is not None:
            fechas_E = fechas_senal
            if provisional:   # el mes en curso aún no ha terminado: se evalúa con fecha de hoy
                fechas_E = fechas_senal[:-1].append(pd.DatetimeIndex([hoy]))
            E_todos = matriz_elegibilidad(membresias, fechas_E, pedidos, meses_min, regla)
            E_todos.index = fechas_senal
            E = E_todos[tickers_ok]
            pocos = E.sum(axis=1)
            pocos = pocos[pocos < n_top]
            if len(pocos):
                warnings.warn(f"Meses con menos de {n_top} valores elegibles: {pocos.to_dict()}")

        precios_mom = monthly_prices.shift(saltar_meses) if saltar_meses else monthly_prices

        # Universo efectivo de cada mes: elegible y con precio en ese cierre y el anterior
        con_precio = precios_mom.notna() & precios_mom.shift(1).notna()
        for d in fechas_senal:
            fila = con_precio.loc[d] if E is None else (con_precio.loc[d] & E.loc[d])
            elegibles_sin_datos = ([] if E is None else
                                   [t for t in sin_datos_yahoo if E_todos.loc[d, t]])
            self.universo[d + pd.offsets.MonthEnd(1)] = {
                "efectivo": sorted(fila.index[fila].tolist()),
                "sin_datos": sorted(elegibles_sin_datos if E is not None else sin_datos_yahoo)}

        # =====================================================
        # Momentum por ventana (top-n calculado una vez por ventana)
        # =====================================================
        _log("⚡ Precomputando momentum...")
        topn_cache = {}
        for w in ventanas:
            slopes, slopes_r2 = rolling_slope_vectorized(precios_mom, w)
            slopes = slopes.loc[fechas_senal]
            slopes_r2 = slopes_r2.loc[fechas_senal]
            if E is not None:
                slopes, slopes_r2 = slopes.where(E), slopes_r2.where(E)
            topn_cache[w] = (top_n_signals(slopes, n_top), top_n_signals(slopes_r2, n_top))

        # =====================================================
        # Ensemble jerárquico (igual que v1)
        # =====================================================
        all_range_topn = []
        for i1, w1 in enumerate(ventanas):
            for w2 in ventanas[i1:]:
                sigs = []
                for w in [x for x in ventanas if w1 <= x <= w2]:
                    peso = 1 / np.sqrt(w)
                    if usar_pend:
                        sigs.append(topn_cache[w][0] * peso)
                    if usar_r2:
                        sigs.append(topn_cache[w][1] * peso)
                combined_range = sum(sigs)
                if E is not None:
                    combined_range = combined_range.where(E)
                all_range_topn.append(top_n_signals(combined_range, n_top))

        total_votes = sum(all_range_topn)
        if E is not None:
            total_votes = total_votes.where(E)
        if grupos and max_por_grupo:
            final_signals = top_n_por_grupo(total_votes, n_top, grupos, max_por_grupo)
        else:
            final_signals = top_n_signals(total_votes, n_top)

        # Ranking de votos de la meta-votación para TODOS los meses (clave = mes de las posiciones)
        filas = total_votes.index
        for i, d in enumerate(filas):
            votos = total_votes.loc[d].dropna()
            votos = votos.reindex(sorted(votos.index, key=lambda t: (-votos[t], t))).astype(int)
            if i == len(filas) - 1:
                tipo = "previsión" if provisional else "nuevas"
            elif provisional and i == len(filas) - 2:
                tipo = "oficial"
            else:
                tipo = ""
            self.ranking[d + pd.offsets.MonthEnd(1)] = {
                "votos": votos.head(max(N_RANKING, n_top + 1)),
                "seleccion": final_signals.columns[final_signals.loc[d] == 1].tolist(),
                "n": n_top, "max": len(all_range_topn), "tipo": tipo}

        # =====================================================
        # Lag de un mes + fila del mes siguiente
        # =====================================================
        signals = final_signals.shift(1)
        signals.loc[fecha_sig] = final_signals.iloc[-1]
        rets = monthly_returns.reindex(signals.index)

        strategy_returns = (signals * rets).sum(axis=1) / signals.sum(axis=1)
        strategy_returns.loc[fecha_sig] = np.nan

        n_sig = int(signals.loc[fecha_sig].sum())
        if n_sig > n_top:
            elegidos = signals.columns[signals.loc[fecha_sig] == 1].tolist()
            warnings.warn(f"EMPATE en posiciones de {fecha_sig.strftime('%b-%y')}: "
                          f"{n_sig} seleccionados para n={n_top}: {elegidos}")

        active_positions = {}
        for d in signals.index:
            active = signals.loc[d]
            selected = active[active == 1].index.tolist()
            if not selected:
                continue
            if d == fecha_sig:
                active_positions[d] = {t: np.nan for t in selected}
            else:
                active_positions[d] = {t: rets.loc[d, t] for t in selected if not pd.isna(rets.loc[d, t])}
        return strategy_returns, active_positions


def run_multi_strategy(strategies_dict, start_date, fecha_corte=None, convert_to_eur=False,
                       provisional=False):
    """
    Claves admitidas por subestrategia:
      tickers, n, weight (opcional), membresias (opcional), meses_min (3), saltar_meses (0),
      ventanas (opcional, por defecto 2..24), regla ("dinamica" | "anual"),
      senal ("ambas" | "pendiente" | "r2"),
      grupos ({ticker: tema}) y max_por_grupo (máximo por tema; None = sin límite)
    provisional=True (solo con fecha_corte=None): añade la rentabilidad provisional del mes
    en curso y la previsión de posiciones del mes siguiente. El mes provisional queda en
    total_returns.attrs["mes_provisional"].
    """
    bt = Backtester()
    sub_returns, sub_positions, rankings, universos = {}, {}, {}, {}
    total_n = sum(cfg["n"] for cfg in strategies_dict.values())

    for name, cfg in strategies_dict.items():
        n_top = cfg["n"]
        weight = cfg.get("weight", n_top / total_n)
        _log(f"\n=== Ejecutando {name.strip()} (n={n_top}, weight={100*weight:.2f}%) ===")
        returns, active_positions = bt.run(
            cfg.get("tickers"), start_date, fecha_corte, n_top, convert_to_eur,
            membresias=cfg.get("membresias"), meses_min=cfg.get("meses_min", 3),
            saltar_meses=cfg.get("saltar_meses", 0), provisional=provisional,
            ventanas=cfg.get("ventanas"), regla=cfg.get("regla", "dinamica"),
            senal=cfg.get("senal", "ambas"),
            grupos=cfg.get("grupos"), max_por_grupo=cfg.get("max_por_grupo"))
        sub_returns[name] = returns * weight
        sub_positions[name] = active_positions
        if bt.ranking:
            rankings[name] = bt.ranking
        universos[name] = bt.universo

    returns_df = pd.DataFrame(sub_returns)
    total_returns = returns_df.sum(axis=1, min_count=1)
    total_returns.attrs["rankings"] = rankings
    total_returns.attrs["universos"] = universos
    if provisional and fecha_corte is None:
        total_returns.attrs["mes_provisional"] = _hoy() + pd.offsets.MonthEnd(0)

    hist = returns_df.iloc[1:-1]                 # sin el primer mes ni el mes siguiente
    huecos = hist[hist.isna().any(axis=1) & hist.notna().any(axis=1)]
    if len(huecos):
        warnings.warn("Meses con alguna subestrategia sin dato (cuenta como 0%): "
                      + ", ".join(d.strftime('%b-%y') for d in huecos.index))

    combined_positions = {}
    all_dates = sorted(set().union(*[p.keys() for p in sub_positions.values()]))
    for date in all_dates:
        merged = {}
        for pos_dict in sub_positions.values():
            merged.update(pos_dict.get(date, {}))
        combined_positions[date] = merged

    return total_returns, combined_positions, sub_returns, sub_positions


# =========================================================
# BACKTEST COMPLETO: métricas, tabla anual, gráfico y Excel
# =========================================================
MESES_ES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


def meses_cerrados(r):
    """Solo meses cerrados: sin el mes provisional ni la fila del mes siguiente."""
    mes_prov = r.attrs.get("mes_provisional")
    r = r.dropna()
    if mes_prov is not None:
        r = r[r.index < mes_prov]
    return r


def alinear(series):
    """Recorta todas las series (meses cerrados) al periodo común, para comparar lo mismo."""
    cerr = {k: meses_cerrados(v) for k, v in series.items()}
    ini = max(s.index[0] for s in cerr.values())
    fin = min(s.index[-1] for s in cerr.values())
    return {k: s[(s.index >= ini) & (s.index <= fin)] for k, s in cerr.items()}


def metricas(r):
    """Métricas numéricas de una serie de rentabilidades mensuales (meses cerrados)."""
    r = meses_cerrados(r)
    cum = (1 + r).cumprod()
    dd = cum / cum.cummax() - 1
    anos = len(r) / 12
    return {"Desde": r.index[0].strftime("%b-%y"), "Hasta": r.index[-1].strftime("%b-%y"), "Meses": len(r),
            "Acumulado": cum.iloc[-1] - 1, "CAGR": cum.iloc[-1] ** (1 / anos) - 1 if anos > 0 else np.nan,
            "Volatilidad": r.std() * np.sqrt(12), "Sharpe": r.mean() / r.std() * np.sqrt(12) if r.std() > 0 else np.nan,
            "MaxDD": dd.min(), "Peor mes": r.min(), "Mejor mes": r.max(), "% meses +": (r > 0).mean()}


def tabla_metricas(series):
    df = pd.DataFrame({k: metricas(v) for k, v in alinear(series).items()}).T
    fmt = df.copy()
    for c in ["Acumulado", "CAGR", "Volatilidad", "MaxDD", "Peor mes", "Mejor mes", "% meses +"]:
        fmt[c] = df[c].map(lambda x: f"{x*100:.1f}%")
    fmt["Sharpe"] = df["Sharpe"].map(lambda x: f"{x:.2f}")
    return fmt


def tabla_anual(r):
    """Filas = años, columnas = meses + total del año (solo meses cerrados)."""
    r = meses_cerrados(r)
    t = pd.DataFrame({"ano": r.index.year, "mes": r.index.month, "r": r.values}).pivot(index="ano", columns="mes", values="r")
    t = t.reindex(columns=range(1, 13))
    t.columns = MESES_ES
    t["Año"] = r.groupby(r.index.year).apply(lambda x: (1 + x).prod() - 1)
    t.index.name = None
    return t


def html_tabla_anual(r, titulo=""):
    t = tabla_anual(r)
    html = f"<h3>{titulo}</h3>" if titulo else ""
    html += "<table border='1' cellpadding='4' cellspacing='0' style='border-collapse:collapse; font-size:12px;'>"
    html += "<tr><th></th>" + "".join(f"<th>{c}</th>" for c in t.columns) + "</tr>"
    for ano, fila in t.iterrows():
        html += f"<tr><td><b>{ano}</b></td>"
        for c, v in fila.items():
            if pd.isna(v):
                html += "<td></td>"
            else:
                peso = "font-weight:bold; background:#eef3ff;" if c == "Año" else ""
                html += f"<td style='text-align:right; color:{_color_ret(v)}; {peso}'>{v*100:.1f}%</td>"
        html += "</tr>"
    return html + "</table>"


def html_tabla_anual_comparada(series, titulo=""):
    """Filas = años, columnas = variantes (rentabilidad de cada año)."""
    t = pd.DataFrame({k: tabla_anual(v)["Año"] for k, v in alinear(series).items()})
    html = f"<h3>{titulo}</h3>" if titulo else ""
    html += "<table border='1' cellpadding='4' cellspacing='0' style='border-collapse:collapse; font-size:12px;'>"
    html += "<tr><th>Año</th>" + "".join(f"<th>{c}</th>" for c in t.columns) + "</tr>"
    for ano, fila in t.iterrows():
        html += f"<tr><td><b>{ano}</b></td>" + "".join(
            "<td></td>" if pd.isna(v) else f"<td style='text-align:right; color:{_color_ret(v)};'>{v*100:.1f}%</td>"
            for v in fila.values) + "</tr>"
    return html + "</table>"


def grafico_curvas(series, titulo="", ruta_png=None, log=True):
    """Evolución de 1 € invertido (meses cerrados) y drawdown."""
    import matplotlib.pyplot as plt
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    for nombre, r in alinear(series).items():
        cum = pd.concat([pd.Series([1.0], index=[r.index[0] - pd.offsets.MonthEnd(1)]), (1 + r).cumprod()])
        ax1.plot(cum.index, cum.values, label=nombre, lw=1.6)
        ax2.plot(cum.index, (cum / cum.cummax() - 1).values * 100, lw=1.0)
    if log:
        ax1.set_yscale("log")
    ax1.set_title(titulo)
    ax1.set_ylabel("Valor de 1 €")
    ax1.grid(alpha=0.3)
    ax1.legend(fontsize=9)
    ax2.set_ylabel("Drawdown %")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    if ruta_png:
        fig.savefig(ruta_png, dpi=110)
    plt.show()
    plt.close(fig)


def exportar_backtest(ruta_xlsx, series, posiciones=None):
    """Excel: métricas, rentabilidad mensual y anual de cada serie, y posiciones de cada mes."""
    series = alinear(series)
    with pd.ExcelWriter(ruta_xlsx) as xw:
        pd.DataFrame({k: metricas(v) for k, v in series.items()}).T.to_excel(xw, sheet_name="metricas")
        mens = pd.DataFrame(series)
        mens.index = mens.index.strftime("%Y-%m")
        mens.to_excel(xw, sheet_name="mensual")
        pd.DataFrame({k: tabla_anual(v)["Año"] for k, v in series.items()}).to_excel(xw, sheet_name="anual")
        for k, v in list(series.items())[:6]:
            tabla_anual(v).to_excel(xw, sheet_name=("anual_" + "".join(c for c in k if c.isalnum()))[:31])
        if posiciones:
            filas = []
            for nombre, pos in posiciones.items():
                for m, d in sorted(pos.items()):
                    filas.append({"variante": nombre, "mes": m.strftime("%Y-%m"),
                                  "posiciones": ", ".join(sorted(d)),
                                  "rentabilidades": ", ".join(f"{t} {v*100:.2f}%" for t, v in sorted(d.items())
                                                              if not pd.isna(v))})
            pd.DataFrame(filas).to_excel(xw, sheet_name="posiciones", index=False)
    return ruta_xlsx


def comparar_variantes(variantes, start_date, fecha_corte=None, n_meses=12, titulo="",
                       mostrar_posiciones=True, referencia=None, ruta_excel=None, ruta_png=None):
    """
    variantes = {"nombre": (strategies_dict, convert_to_eur), ...}
    referencia = {"nombre": (strategies_dict, convert_to_eur)} → se añade a métricas, tabla anual
                 y gráfico (no a las posiciones). Ej.: MSCI World.
    Muestra: métricas de todo el periodo, rentabilidad por año, gráfico, rentabilidad mensual y
    posiciones de los últimos n_meses. Siempre en modo cierre (solo meses cerrados).
    """
    try:
        from IPython.display import display, HTML
    except Exception:
        display, HTML = print, str
    resultados, posiciones = {}, {}
    for nombre, (estrategias, eur) in variantes.items():
        r, pos, _, _ = run_multi_strategy(estrategias, start_date, fecha_corte, eur)
        resultados[nombre] = r
        posiciones[nombre] = pos
    todas = dict(resultados)
    for nombre, (estrategias, eur) in (referencia or {}).items():
        todas[nombre], _, _, _ = run_multi_strategy(estrategias, start_date, fecha_corte, eur)

    if titulo:
        print("\n" + "=" * 70 + f"\n{titulo}\n" + "=" * 70)
    print("\n📊 MÉTRICAS DE TODO EL PERIODO (meses cerrados)")
    display(tabla_metricas(todas))
    display(HTML(html_tabla_anual_comparada(todas, "📅 Rentabilidad por año")))
    grafico_curvas(todas, titulo, ruta_png)

    print(f"\n📅 RENTABILIDAD MENSUAL (últimos {n_meses} meses)")
    tabla = pd.DataFrame({k: meses_cerrados(v) for k, v in todas.items()}).tail(n_meses)
    tabla.index = tabla.index.strftime("%b-%y")
    print((tabla * 100).round(2).astype(str).add("%").to_string())
    if ruta_excel:
        exportar_backtest(ruta_excel, todas, posiciones)
        print(f"💾 Backtest completo de la comparativa (mensual, anual, posiciones): {ruta_excel}")

    if mostrar_posiciones:
        print("\n🧾 POSICIONES DE CADA MES (* = distinta de la primera variante)")
        meses = sorted(set().union(*[set(p) for p in posiciones.values()]))[-(n_meses + 1):]
        base = list(posiciones)[0]
        filas = {}
        for m in meses:
            ref = set(posiciones[base].get(m, {}))
            fila = {}
            for nombre, pos in posiciones.items():
                s = set(pos.get(m, {}))
                fila[nombre] = ("* " if s != ref and nombre != base else "") + ", ".join(sorted(s))
            filas[m.strftime("%b-%y")] = fila
        tabla_pos = pd.DataFrame(filas).T
        try:
            from IPython.display import display
            display(tabla_pos)
        except Exception:
            print(tabla_pos.to_string())

    print("\n📌 POSICIONES DEL MES SIGUIENTE")
    for nombre, pos in posiciones.items():
        ultima = max(pos)
        print(f"   {nombre} ({ultima.strftime('%b-%y')}): {sorted(pos[ultima])}")
    return resultados, posiciones


# =========================================================
# INFORMES
# =========================================================
def _mes_provisional(serie):
    return None if serie is None else serie.attrs.get("mes_provisional")


def _etiqueta_mes(d, mes_prov, es_ultima, html=False):
    mes = pd.to_datetime(d).strftime("%b-%y").lower()
    salto = "<br>" if html else " "
    if es_ultima:
        return f"{mes}{salto}(previsión)" if mes_prov is not None else f"{mes}{salto}(nuevas)"
    if mes_prov is not None and pd.Timestamp(d) == mes_prov:
        return f"{mes}{salto}(provisional)"
    return mes


def informe(active_positions, total_returns=None, n_meses=4):
    """Texto de los últimos meses. La última línea son las posiciones nuevas (o la previsión).
    Si se pasa total_returns, el RETORNO TOTAL usa los pesos reales de la estrategia."""
    lines = []
    items = sorted(active_positions.items())[-n_meses:]
    mes_prov = _mes_provisional(total_returns)
    for i, (date, positions) in enumerate(items):
        if not positions:
            continue
        mes_str = _etiqueta_mes(date, mes_prov, i == len(items) - 1)
        tickers_part = " | ".join(
            f"{t:<8} {'-':>7}" if pd.isna(r) else f"{t:<8} {r:>7.2%}" for t, r in positions.items())
        if total_returns is not None and date in total_returns.index:
            rt = total_returns.loc[date]
        else:
            vals = [r for r in positions.values() if not pd.isna(r)]
            rt = np.mean(vals) if vals else np.nan
        retorno_str = "-" if pd.isna(rt) else f"{rt:>6.2%}"
        lines.append(f"MES: {mes_str:<20} | {tickers_part} | RETORNO TOTAL: {retorno_str}")
    return "\n".join(lines)


def _margen(info):
    """Votos del último elegido menos los del primero que queda fuera (0 = empate)."""
    v, n = info["votos"], info["n"]
    if len(v) <= n:
        return None
    return int(v.iloc[n - 1] - v.iloc[n])


def _alerta_margen(info):
    m = _margen(info)
    if m is None:
        return ""
    if m == 0 or len(info["seleccion"]) > info["n"]:
        return "EMPATE"
    return "AJUSTADO" if m <= UMBRAL_MARGEN * info["max"] else ""


def informe_ranking(total_returns, n_meses=14):
    """Texto: los N_RANKING candidatos con más votos en cada subestrategia (✔ = elegido),
    para los últimos n_meses de posiciones (los mismos que se muestran de rentabilidad)."""
    rankings = total_returns.attrs.get("rankings", {})
    if not rankings:
        return ""
    lines = []
    for nombre, por_mes in rankings.items():
        lines.append(f"  ── {nombre.strip()}")
        for mes, info in sorted(por_mes.items())[-n_meses:]:
            et = pd.Timestamp(mes).strftime('%b-%y').lower() + (f" ({info['tipo']})" if info["tipo"] else "")
            partes = [f"{'✔' if t in info['seleccion'] else ' '} {t} {v}"
                      for t, v in info["votos"].head(N_RANKING).items()]
            m = _margen(info)
            alerta = _alerta_margen(info)
            txt_m = "" if m is None else f"  → margen {info['n']}º-{info['n']+1}º: {m} votos"
            txt_a = f"  ⚠️ {alerta}" if alerta else ""
            lines.append(f"     {et:<20} | " + " | ".join(partes) + txt_m + txt_a)
    max_v = next(iter(next(iter(rankings.values())).values()))["max"]
    return f"🔢 Ranking de votos (máximo {max_v} por valor):\n" + "\n".join(lines)


def _html_ranking(total_returns, n_meses=14):
    rankings = total_returns.attrs.get("rankings", {})
    if not rankings:
        return ""
    meses = sorted({m for r in rankings.values() for m in r}, reverse=True)[:n_meses]   # más reciente primero
    max_v = next(iter(next(iter(rankings.values())).values()))["max"]
    html = (f"<h3>Ranking de votos (top {N_RANKING}, máximo {max_v} por valor)</h3>"
            "<p><i>✔ = elegido. Margen = votos del último elegido menos los del primero que queda fuera.</i></p>"
            "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;'>"
            "<tr><th>COMPONENTE</th>")
    for m in meses:
        tipo = next(r[m]["tipo"] for r in rankings.values() if m in r)
        html += f"<th>{pd.Timestamp(m).strftime('%b-%y').lower()}{'<br>(' + tipo + ')' if tipo else ''}</th>"
    html += "</tr>"
    for nombre, por_mes in rankings.items():
        html += f"<tr><td><b>{nombre}</b></td>"
        for m in meses:
            info = por_mes.get(m)
            if info is None:
                html += "<td>-</td>"
                continue
            cel = ""
            for t, v in info["votos"].head(N_RANKING).items():
                elegido = t in info["seleccion"]
                estilo = "font-weight:bold;" if elegido else "color:#666666;"
                cel += f"<div style='{estilo}'>{'✔ ' if elegido else ''}{t}: {v}</div>"
            mg, alerta = _margen(info), _alerta_margen(info)
            if mg is not None:
                color = "#FF0000" if alerta else "#008000"
                cel += (f"<div style='color:{color}; margin-top:4px;'>margen {info['n']}º-{info['n']+1}º: "
                        f"{mg}{' ⚠️ ' + alerta if alerta else ''}</div>")
            html += f"<td style='vertical-align:top;'>{cel}</td>"
        html += "</tr>"
    return html + "</table>"


# =========================================================
# UNIVERSO UTILIZADO EN CADA MES
# =========================================================
def resumen_universos(total_returns, n_meses=14):
    """Texto: nº de valores del universo de cada mes y qué entra/sale respecto al mes anterior."""
    universos = total_returns.attrs.get("universos", {})
    lines = []
    for nombre, por_mes in universos.items():
        meses = sorted(por_mes)
        if not meses or len({len(por_mes[m]["efectivo"]) for m in meses}) == 1 and \
                len(por_mes[meses[0]]["efectivo"]) <= 15 and not any(por_mes[m]["sin_datos"] for m in meses):
            # lista fija pequeña y estable: se resume en una línea
            if meses:
                lines.append(f"  ── {nombre.strip()}: {len(por_mes[meses[-1]]['efectivo'])} valores fijos "
                             f"{por_mes[meses[-1]]['efectivo']}")
            continue
        lines.append(f"  ── {nombre.strip()}")
        for i, m in enumerate(meses):
            if i < len(meses) - n_meses:
                continue
            u = set(por_mes[m]["efectivo"])
            prev = set(por_mes[meses[i - 1]]["efectivo"]) if i > 0 else None
            cambios = ""
            if prev is not None:
                altas, bajas = sorted(u - prev), sorted(prev - u)
                cambios = (f" | ENTRAN {altas}" if altas else "") + (f" | SALEN {bajas}" if bajas else "")
            sd = por_mes[m]["sin_datos"]
            txt_sd = f" | elegibles SIN PRECIO en Yahoo: {len(sd)}" if sd else ""
            lines.append(f"     {pd.Timestamp(m).strftime('%b-%y').lower():<7} {len(u):>4} valores{cambios}{txt_sd}")
    return "🌐 Universo utilizado en cada mes (mes de las posiciones):\n" + "\n".join(lines) if lines else ""


def exportar_universos(total_returns, ruta_xlsx, nombre_cartera=""):
    """Excel con la lista EXACTA de valores de cada mes por subestrategia.
    Hojas: una por subestrategia (mes, nº, lista) + 'matriz' (1 = en el universo ese mes)
    + 'sin_precio' (elegibles por índice que Yahoo no tiene, p. ej. empresas ya excluidas de bolsa)."""
    universos = total_returns.attrs.get("universos", {})
    rankings = total_returns.attrs.get("rankings", {})
    with pd.ExcelWriter(ruta_xlsx) as xw:
        filas_sd = []
        for nombre, por_mes in universos.items():
            hoja = "".join(c for c in nombre.strip() if c.isalnum() or c in " _-")[:28] or "sub"
            filas = []
            for m in sorted(por_mes):
                info_r = rankings.get(nombre, {}).get(m)
                filas.append({"mes_posiciones": pd.Timestamp(m).strftime("%Y-%m"),
                              "n_valores": len(por_mes[m]["efectivo"]),
                              "elegidos": ", ".join(info_r["seleccion"]) if info_r else "",
                              "valores": ", ".join(por_mes[m]["efectivo"]),
                              "n_sin_precio": len(por_mes[m]["sin_datos"])})
                for t in por_mes[m]["sin_datos"]:
                    filas_sd.append({"subestrategia": nombre.strip(), "mes_posiciones": pd.Timestamp(m).strftime("%Y-%m"),
                                     "ticker": t})
            pd.DataFrame(filas).to_excel(xw, sheet_name=hoja, index=False)
            todos = sorted(set().union(*[set(v["efectivo"]) for v in por_mes.values()])) if por_mes else []
            matriz = pd.DataFrame(0, index=[pd.Timestamp(m).strftime("%Y-%m") for m in sorted(por_mes)], columns=todos)
            for m in sorted(por_mes):
                matriz.loc[pd.Timestamp(m).strftime("%Y-%m"), por_mes[m]["efectivo"]] = 1
            matriz.T.to_excel(xw, sheet_name=(hoja[:22] + "_matriz"))
        pd.DataFrame(filas_sd or [{"subestrategia": "", "mes_posiciones": "", "ticker": ""}]).to_excel(
            xw, sheet_name="sin_precio", index=False)
    return ruta_xlsx


def _color_ret(r):
    if r is None or pd.isna(r):
        return "#999999"
    return "#008000" if r >= 0 else "#FF0000"


def generate_email_report(sub_positions, strategy_returns, portfolio_name, n_meses):
    """La primera columna es el mes siguiente (posiciones nuevas o previsión)."""
    last_dates = list(reversed(strategy_returns.index[-n_meses:]))
    mes_prov = _mes_provisional(strategy_returns)
    col_names = [_etiqueta_mes(d, mes_prov, i == 0, html=True) for i, d in enumerate(last_dates)]

    html = f"<html><body><h2>{portfolio_name}</h2>"
    html += "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;'>"
    html += "<tr><th>COMPONENTES</th>" + "".join(f"<th>{c}</th>" for c in col_names) + "</tr>"

    for strat_name, data in sub_positions.items():
        html += f"<tr><td><b>{strat_name}</b></td>"
        for d in last_dates:
            tickers = data.get(d, {})
            if not tickers:
                html += "<td style='text-align:center;'>-</td>"
                continue
            cell = ""
            for t, r in tickers.items():
                r_pct = None if pd.isna(r) else 100 * r
                val = "-" if r_pct is None else f"{r_pct:.2f}%"
                cell += f"<div style='color:{_color_ret(r_pct)}; font-weight:bold;'>{t}<br>{val}</div>"
            html += f"<td style='text-align:center; vertical-align:middle;'>{cell}</td>"
        html += "</tr>"

    html += "<tr style='background-color:#cce5ff;'><td><b>TOTAL</b></td>"
    for d in last_dates:
        r = strategy_returns.get(d, np.nan)
        r = None if pd.isna(r) else 100 * r
        val = "-" if r is None else f"{r:.2f}%"
        html += f"<td style='text-align:center; font-weight:bold; color:{_color_ret(r)};'>{val}</td>"
    html += "</tr></table>"

    # Movimientos: mes siguiente frente al mes actual
    if mes_prov is not None:
        sig = pd.to_datetime(last_dates[0]).strftime("%b-%y").lower()
        html += (f"<h3>Previsión de cambios para {sig}</h3>"
                 f"<p><i>Provisional: calculada con datos hasta hoy. Puede cambiar hasta el cierre de mes.</i></p><ul>")
    else:
        html += "<h3>Posiciones previstas para el próximo mes</h3><ul>"
    changes = False
    for strat_name, data in sub_positions.items():
        dates_sorted = sorted(data.keys())
        if len(dates_sorted) < 2:
            continue
        last_set = set(data[dates_sorted[-1]].keys())
        prev_set = set(data[dates_sorted[-2]].keys())
        for t in sorted(last_set - prev_set):
            html += f"<li>{strat_name} - {t}: <span style='color:green;'>ENTRA</span></li>"
            changes = True
        for t in sorted(prev_set - last_set):
            html += f"<li>{strat_name} - {t}: <span style='color:red;'>SALE</span></li>"
            changes = True
    if not changes:
        html += "<li><i>Sin cambios previstos para el próximo mes</i></li>"
    html += "</ul>" + _html_ranking(strategy_returns, n_meses) + "</body></html>"
    return html


def generate_portfolio_summary(portfolios, n_meses):
    """Resumen de los últimos n_meses (sin el mes siguiente). Marca el mes provisional si lo hay."""
    any_series = next(iter(portfolios.values()))
    mes_prov = _mes_provisional(any_series)
    last_dates = list(reversed(any_series.index[:-1][-n_meses:]))
    html = "<h2>Resumen de Portfolios</h2>"
    html += "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;'>"
    html += "<tr><th>PORTFOLIO</th>" + "".join(
        f"<th style='text-align:center;'>{_etiqueta_mes(d, mes_prov, False, html=True)}</th>"
        for d in last_dates) + "</tr>"
    for name, series in portfolios.items():
        html += f"<tr><td><b>{name}</b></td>"
        for d in last_dates:
            r = series.get(d, np.nan)
            r = None if pd.isna(r) else 100 * r
            val = "-" if r is None else f"{r:.2f}%"
            html += f"<td style='text-align:center; font-weight:bold; color:{_color_ret(r)};'>{val}</td>"
        html += "</tr>"
    html += "</table>"
    return html
