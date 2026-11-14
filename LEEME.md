# GEDA · seguimiento diario

Cada día laborable, a las 23:30 de Madrid (00:30 en verano), GitHub ejecuta `geda_diario.py`, que saca:

1. **Resumen:** los 3 últimos meses cerrados, el mes en curso (provisional) y la previsión del mes siguiente, con GEDA frente al MSCI World.
2. **Posiciones, rentabilidad y votos:** cada bloque, mes a mes, con el top 5 de votos y el margen. Marca ⚠️ AJUSTADO o EMPATE.
3. **Gráfico del mes en curso, día a día:** la cartera, cada posición y el MSCI World, en EUR.

Resultados, en la carpeta `salida/`:

| Fichero | Qué es |
|---|---|
| `GEDA_hoy.html` | Informe del último día (descárgalo y ábrelo en el navegador) |
| `historico/GEDA_AAAA-MM-DD.html` | Informe de cada día. El del último día del mes sirve de registro de la salida oficial |
| `GEDA_grafico_diario_AAAA-MM.png` | Gráfico del mes. Se sobrescribe cada día, así que el último del mes queda como cierre |
| `geda_seguimiento_diario.csv` | Una línea por día: rentabilidad del mes, posiciones, previsión, entradas y salidas, y alertas de votos |

En la pestaña **Actions** de GitHub, cada ejecución muestra también un resumen corto.

## Puesta en marcha (una sola vez)

1. **Crea un repositorio PRIVADO.** Si es público, cualquiera ve las posiciones y la previsión por las que cobras.
2. **Sube estos ficheros** tal cual, incluida la carpeta oculta `.github`.
3. **Copia desde Drive/Ficheros_auxiliares a la carpeta `datos/`:**
   - `membresias_usa_desde2015.csv` (obligatorio)
   - `membresias_europa_desde2015.csv` (obligatorio)
   - `divisas_cache.json` (recomendado: ahorra consultas a Yahoo)
4. **Permisos:** ve a Settings → Actions → General → Workflow permissions y marca "Read and write permissions".
5. **Prueba:** en Actions → GEDA diario → Run workflow.
6. **Correo (opcional):** en Settings → Secrets and variables → Actions crea estos tres secretos:
   - `SMTP_USER`: tu Gmail
   - `SMTP_PASSWORD`: una *contraseña de aplicación* de Google, no tu contraseña normal
   - `MAIL_TO`: el destino; admite varios separados por comas

   Si no los creas, no se envía nada y el informe queda solo en el repo.

## Si algo falla
- **Yahoo bloquea o faltan datos** (descarga rota, divisas, algún índice o sector sin precio, o más de un 5 % de acciones sin precio): el programa no escribe nada y vuelve a intentarlo a los 30 minutos. Si falla otra vez, la ejecución sale en rojo y GitHub te manda un correo. Ese día no hay informe, y es intencionado: no se guardan cifras con datos rotos.
- **Código de salida 3:** el informe se ha generado y guardado, pero no se ha podido enviar el correo. Revisa los secretos.

## Mantenimiento
- **Membresías:** cuando cambie la composición de un índice, actualiza el CSV en `datos/` igual que en Drive. Si no lo actualizas, el script sigue funcionando, pero con un universo equivocado.
- **Motor:** `motor_geda.py` es una copia literal de la celda "Motor" de `GEDA_limpio.ipynb`. Si cambias el motor del notebook, vuelve a copiarlo entero.
- **Cartera real (opcional):** en `datos/cartera_real.csv` puedes anotar lo que tienes de verdad cada mes (`2026-11,QQQ`, una fila por ticker).
  - Si ese mes tiene filas, el gráfico y la tabla del mes en curso usan tu cartera, con pesos iguales.
  - Además, avisa si no coincide con el método.
  - Los meses cerrados y la previsión siempre son los del método.

## Cómo se calcula
- Configuración cerrada de GEDA (oct-2026):
  - 1 índice, 2 sectores, 2 acciones de USA y 2 de Europa, con el mismo peso;
  - señal "ambas", ventanas de 2 a 24 meses;
  - regla dinámica: más de 3 meses dentro del índice.
- **Mes en curso:** las posiciones oficiales (calculadas con el cierre del mes anterior), compradas a ese cierre y mantenidas todo el mes. El script comprueba que el gráfico cuadra con el motor (tolerancia de 0,1 puntos).
- **Previsión:** como si el mes terminase hoy. Puede cambiar hasta el último cierre del mes.
- Todo en EUR, con el tipo de cambio de cada día.

---

# GEN-Z · seguimiento diario

Mismo repositorio, mismos secretos de correo y mismas membresías que GEDA. Corre a las 23:00 UTC de lunes a viernes, media hora después de GEDA. Las dos ejecuciones nunca coinciden, porque comparten grupo en GitHub.

**Ficheros:** `genz_diario.py`, `motor_genz.py` y `.github/workflows/genz_diario.yml`. En `datos/` usa además:

| Fichero | Qué es |
|---|---|
| `genz_posiciones.csv` | `mes,ticker,rentab_publicada`. Hasta oct-2026 es lo publicado (v2) y se usa tal cual. Desde nov-2026, si un mes tiene filas, el gráfico usa esa cartera real y avisa si no coincide con el método |
| `industrias_yahoo_cache.json` | Cópialo de Drive/Ficheros_auxiliares. Si falta, la primera ejecución consulta unas 1.000 industrias en Yahoo (lenta y fácil de bloquear) |
| `genz_temas_etf.csv` | `ticker,tema` con tema `ROBOTICA_IA` o `CUANTICA`: el top 25 de BOTZ y WQTM. Cuenta desde las posiciones de dic-2026. Mientras esté vacío, la previsión de diciembre sale con un aviso |

**Correo, en este orden:** gráfico del mes, resumen (3 meses publicados, mes en curso y previsión, frente al MSCI World y SMH), votos de la versión definitiva y comprobación de lo publicado con Yahoo.

**Cada mes:**
1. Al cerrar el mes, añade a `genz_posiciones.csv` sus 4 filas con la rentabilidad publicada. Si falta, el script se para cuando ese mes pase a ser un mes cerrado.
2. Anota las 4 posiciones del mes nuevo.

**Cada trimestre** (fin de enero, abril, julio y octubre): renueva `genz_temas_etf.csv`.
