# BotBolsa: bot de trading por noticias (paper trading)

Cada 15 minutos, GitHub Actions ejecuta el bot en la nube (no necesita tu PC):

1. **Salidas por riesgo:** cierra posiciones con stop loss (-5 %), toma de ganancia (+10 %) o más de 5 días abiertas.
2. **Noticias:** descarga las noticias nuevas de acciones y cripto desde Alpaca (fuente: Benzinga).
3. **Análisis:** un modelo de lenguaje (por defecto `gpt-oss-120b` en el plan gratis de Groq; opcionalmente Claude) lee las noticias y devuelve señales `buy`/`sell` con confianza, impacto y justificación.
4. **Órdenes:** compra el 5 % del portafolio si la confianza es ≥ 75 % y el impacto no es bajo; vende si hay una noticia negativa sobre algo que ya tienes. Solo opera en largo (nunca en corto).
5. **Dashboard:** guarda el resultado en `docs/data.json`, que se publica con GitHub Pages.

Las acciones solo se operan con el mercado de EE.UU. abierto. Las señales que llegan con el mercado cerrado quedan en cola (16 h como máximo) y se ejecutan en la apertura. La cripto se opera 24/7.

> **Solo paper trading.** La URL de Alpaca está fija en `bot/config.py` (`paper-api.alpaca.markets`) y no se puede cambiar con variables. Pasar a dinero real requiere editar el código a propósito.

## Puesta en marcha (unos 15 minutos)

### 1. Cuentas y llaves

| Servicio | Qué hacer |
|---|---|
| **Alpaca** | Crea una cuenta en alpaca.markets (no hay que depositar dinero). En el panel, cambia a **Paper Trading** y genera tus *API Keys* (Key ID y Secret). |
| **Groq** (gratis) | Regístrate en console.groq.com (con tu correo o con tu cuenta de Google o GitHub). En **API Keys → Create API Key** crea una llave (empieza por `gsk_`). No pide tarjeta. |
| **GitHub** | Crea un repositorio nuevo, por ejemplo `botbolsa`. |

### 2. Subir el código

Desde esta carpeta (`trading-bot/`):

```bash
git init -b main
git add .
git commit -m "Bot de trading por noticias"
git remote add origin https://github.com/TU_USUARIO/botbolsa.git
git push -u origin main
```

### 3. Secrets en GitHub

En el repo, ve a **Settings → Secrets and variables → Actions → New repository secret** y crea:

- `ALPACA_API_KEY`
- `ALPACA_SECRET_KEY`
- `GROQ_API_KEY`

Las llaves quedan cifradas: nadie las ve, aunque el repo sea público.

### 4. Activar el dashboard

En **Settings → Pages**, elige *Deploy from a branch*, rama `main` y carpeta `/docs`. El dashboard queda en `https://TU_USUARIO.github.io/botbolsa/`. Puedes agregarlo a la pantalla de inicio del iPhone desde Safari.

### 5. Primera ejecución

En **Actions → Trading bot → Run workflow**. A partir de ahí, corre solo cada 15 minutos.

Si quieres ver primero qué haría el bot sin enviar órdenes, crea la variable `DRY_RUN=true` (paso siguiente) y bórrala cuando estés conforme.

## Ajustar las reglas (sin tocar código)

En **Settings → Secrets and variables → Actions → Variables**:

| Variable | Por defecto | Significado |
|---|---|---|
| `MIN_CONFIDENCE` | `0.75` | Confianza mínima del modelo para operar |
| `POSITION_PCT` | `0.05` | Porcentaje del portafolio por operación |
| `MAX_POSITIONS` | `10` | Máximo de posiciones abiertas |
| `STOP_LOSS_PCT` | `0.05` | Cierra si la posición pierde un 5 % |
| `TAKE_PROFIT_PCT` | `0.10` | Cierra si la posición gana un 10 % |
| `MAX_HOLD_DAYS` | `5` | Cierra posiciones con más días abiertas |
| `PENDING_MAX_HOURS` | `16` | Horas que una señal de acciones espera en cola con el mercado cerrado (`0` desactiva la cola) |
| `MAX_CHASE_PCT` | `0.03` | Al abrir el mercado, no compra una señal en cola si el precio ya subió más de esto desde la señal |
| `TRADE_STOCKS` / `TRADE_CRYPTO` | `true` | Activa o desactiva cada mercado |
| `DRY_RUN` | `false` | Analiza y registra sin enviar órdenes |
| `ANALYZER` | `groq` | `claude` para usar la API de Anthropic (de pago; requiere el secret `ANTHROPIC_API_KEY`) |
| `MODEL` | `openai/gpt-oss-120b` (Groq) / `claude-haiku-5-5` (Claude) | Modelo que usa el analizador |

## Costos

- **GitHub Actions y Pages:** gratis en un repo **público**. En un repo privado, Pages requiere un plan de pago y el plan gratis da 2 000 minutos de Actions al mes. Cada 15 minutos son unos 2 900 minutos al mes, así que en privado hay que cambiar el cron a `*/30 * * * *`. Ten en cuenta que en un repo público cualquiera puede ver las operaciones de prueba y el dashboard, pero no tus llaves.
- **Groq:** gratis, con límites (1.000 peticiones y 200.000 tokens al día, 8.000 tokens por minuto para `gpt-oss-120b`). El bot divide las noticias en lotes, descarta los resúmenes de mercado con más de 3 símbolos y salta las noticias de más de 6 horas. Si se alcanza el límite, la ejecución registra el error en el dashboard y las noticias pendientes se reintentan después.
- **Claude (opcional):** si algún día quieres comparar, con `ANALYZER=claude` y Haiku cuesta menos de 1 USD al mes.
- **Alpaca paper:** gratis.

## Desarrollo local

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt pytest
.venv/Scripts/python -m pytest -q
```

Para correrlo una vez en local, exporta las tres llaves como variables de entorno y ejecuta `python -m bot.main`.

## Limitaciones conocidas

- El cron de GitHub no es exacto: en horas de mucha carga puede retrasarse entre 5 y 15 minutos. Para reaccionar en segundos a una noticia haría falta un servidor siempre encendido.
- El stop loss y la toma de ganancia se revisan en cada ejecución, no en tiempo real, así que una pérdida puede pasar del -5 % entre dos revisiones.
- Algunas cuentas de Alpaca (según el país) no tienen cripto habilitado. Si es tu caso, las órdenes fallan y el error aparece en el dashboard. Pon `TRADE_CRYPTO=false`.
- Esto es un experimento. Que gane en paper trading no garantiza nada con dinero real: en paper no hay deslizamiento real ni problemas de liquidez.

## Estructura

```
bot/config.py     configuración y URL fija de paper trading
bot/alpaca.py     cliente REST de Alpaca (cuenta, órdenes, noticias)
bot/analyzer.py   prompt y salida estructurada (Groq o Claude)
bot/strategy.py   reglas de entrada y salida (sin red, con pruebas)
bot/main.py       orquesta una ejecución
data/state.json   estado entre ejecuciones (lo guarda el workflow)
docs/             dashboard (GitHub Pages) y sus datos
```
