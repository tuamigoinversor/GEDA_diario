# GEDA — seguimiento diario

Cada mañana (martes a sábado) calcula GEDA con la configuración cerrada (oct-2026), guarda el informe en este repositorio (carpeta `informe/`) y lo envía por correo.

El informe tiene cuatro apartados:

1. **Previsión del mes siguiente:** posiciones, cambios y avisos de margen ajustado.
2. **Mes en curso:** posiciones oficiales, rentabilidad provisional y gráfica diaria (total y cada posición).
3. **Dos meses anteriores cerrados:** posiciones y rentabilidades.
4. **Votos** de los cuatro meses.

## Ficheros

| Fichero | Qué es |
|---|---|
| `motor.py` | El motor de GEDA_limpio, sin cambios. |
| `geda_diario.py` | El cálculo y los informes. |
| `enviar_email.py` | El envío del correo. |
| `datos/membresias_usa_desde2015.csv`, `datos/membresias_europa_desde2015.csv` | Membresías de los índices. **Sustitúyelas por las de tu Drive** (`Ficheros_auxiliares`). |
| `datos/divisas_cache.json` | Divisa de cada valor. **Sustitúyelo por el de tu Drive.** El incluido se ha hecho por sufijo: EE. UU. en USD, Europa en EUR y `.L` en GBp. |
| `informe/` | El último informe; lo crea el programa. El historial de días anteriores está en el historial de commits. |
| `.github/workflows/geda_diario.yml` | La programación en GitHub. |

## Puesta en marcha (una sola vez)

1. Crea un repositorio **privado** nuevo y sube todo, incluida la carpeta `.github`.
2. Copia en `datos/` los dos CSV de membresías y `divisas_cache.json` de tu Drive.
3. **Settings → Actions → General → Workflow permissions:** «Read and write permissions».
4. **Correo** (Gmail):
   1. En la cuenta que envía, activa la verificación en dos pasos.
   2. Crea una contraseña de aplicación en https://myaccount.google.com/apppasswords.
   3. En el repositorio, ve a **Settings → Secrets and variables → Actions → New repository secret** y crea:
      - `SMTP_USUARIO`: la cuenta que envía (p. ej. `tuamigoinversor@gmail.com`).
      - `SMTP_CLAVE`: la contraseña de aplicación (16 letras), no tu contraseña normal.
      - `EMAIL_DESTINO`: los destinatarios, separados por comas. Van en copia oculta.
   4. Si no creas estos secretos, el informe se guarda igual y no se envía nada.
5. **Actions → GEDA diario → Run workflow** para la primera ejecución. Comprueba que el informe cuadra con tu notebook.

Para cambiar los destinatarios, edita el secreto `EMAIL_DESTINO`.

## Avisos

- **Membresías:** la regla dinámica depende de que los CSV estén al día. Cuando un índice cambie su composición (S&P y Nasdaq, cada trimestre; los europeos, en sus revisiones), actualiza el CSV. Las bajas cuentan desde el primer día.
- **Yahoo:** desde GitHub las descargas fallan a veces por límite de peticiones.
  - El programa reintenta 3 veces, con 10 minutos entre intentos.
  - Si aun así falla, la ejecución sale en rojo y GitHub te avisa por correo. Ese día no hay informe nuevo.
- **Hora:** GitHub programa en UTC. Se ejecuta hacia las 6:47 en invierno y las 7:47 en verano (hora de Madrid), a veces con unos minutos de retraso.
- **Coste:** en un repositorio privado gratuito tienes 2.000 minutos de Actions al mes. Cada ejecución gasta unos 5-10.
