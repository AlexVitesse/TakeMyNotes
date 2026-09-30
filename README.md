<div align="center">

# 🎙️ TakeMyNotes

<p align="center">
  <img src="https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4?logo=windows&logoColor=white" alt="Windows 10 | 11">
  <img src="https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/Groq-Whisper-F55036" alt="Groq Whisper">
  <img src="https://img.shields.io/badge/Gemini-opcional-8E75B2?logo=googlegemini&logoColor=white" alt="Gemini opcional">
  <img src="https://img.shields.io/badge/SQLite-FTS5-003B57?logo=sqlite&logoColor=white" alt="SQLite FTS5">
</p>

<p align="center">
  <strong>Graba · Transcribe · Resume · Pregunta</strong>
</p>

</div>

---

Tomador de notas de reuniones para **Windows**. Graba tu micrófono **y** el audio de la computadora
a la vez, transcribe con **Groq Whisper**, arma un acta separada por decisiones, propuestas y
tareas con dueño, y te deja **chatear** sobre lo que se dijo. Un widget flotante nativo que no
estorba y abre la ventana grande solo cuando la necesitas. Todo queda en tu disco como JSON.

> [!TIP]
> **Usa auriculares.** Con parlantes el micro capta el audio de la PC y la app tiene que descartar
> el eco por texto; con auriculares cada canal llega limpio y la separación "Yo / Los demás" sale
> perfecta.

> [!IMPORTANT]
> El audio **sale de tu equipo** hacia Groq (y la transcripción hacia Gemini si lo activas). En el
> plan gratuito de Gemini, Google entrena con lo que le mandes. Lee [Privacidad](#-privacidad)
> antes de grabar reuniones con información sensible, y avisa a los demás que grabas.

## ✨ Qué hace

<table>
<tr>
<td width="50%" valign="top">

### 🎧 Captura
- **Micro + audio del sistema** a la vez (WASAPI loopback, sin cables virtuales).
- **Cortar tu micro** o **pausar** sin parar la grabación.
- **Capturas de pantalla** de todos los monitores, colgadas de la sesión.
- **Notas manuales en vivo** que la IA fusiona con la transcripción.
- **Aviso de inactividad**: 5 min sin voz → *¿seguir?*; 2 min más → detiene y transcribe.
- **Grabar la siguiente** mientras la anterior se transcribe.

</td>
<td width="50%" valign="top">

### ✍️ Transcripción
- **Groq Whisper** (`whisper-large-v3-turbo`) por canal, fusionado por tiempo.
- **"Yo" vs "Los demás"**, con tu nombre en cada turno y en ámbar cuando te mencionan.
- **Descarta el eco** del micro y las **alucinaciones sobre el silencio** («Gracias.» cada 30 s).
- **Sincronizada con el audio**: clic en una línea y el audio salta ahí.
- **Progreso real**: "Transcribiendo… 3/12".

</td>
</tr>
<tr>
<td width="50%" valign="top">

### ✦ Acta y chat
- **Acta automática**: decidido, propuesto sin respuesta, descartado y por qué, ya entregado,
  preguntas abiertas y hasta 10 acciones **con dueño** (o `sin dueño`, nunca inventado).
- **Groq** (`openai/gpt-oss-120b`) o **Gemini** (`gemini-3.7-flash`), uno de respaldo del otro.
- **Chat** por sesión: "¿qué acordamos sobre X?".

</td>
<td width="50%" valign="top">

### 📚 Librería
- **Buscador de texto completo** (SQLite FTS5): prefijos, sin acentos, fragmento resaltado,
  salta al punto exacto de la transcripción.
- **Sugerencias por significado**: buscas "engaño", aparece "fraude".
- **Nombre generado por la IA**, favoritos, renombrar, eliminar.
- **Tema claro / oscuro / auto** y API keys **cifradas con DPAPI**.

</td>
</tr>
</table>

## 🛠️ Stack

| Categoría | Tecnología |
|-----------|-----------|
| Lenguaje | Python 3.12+ (un solo archivo: `takemynotes.py`) |
| Widget flotante | Tkinter (transparencia nativa real) |
| Ventana | pywebview + WebView2, HTML/CSS/JS sin framework |
| Audio | `soundcard` (WASAPI loopback) + `numpy` |
| Transcripción | Groq Whisper `whisper-large-v3-turbo` |
| Resumen / chat | Groq `openai/gpt-oss-120b` · Gemini vía endpoint compatible con OpenAI |
| Búsqueda | SQLite FTS5 (stdlib) |
| Empaquetado | PyInstaller → un solo `.exe` |

## 🚀 Empezar

### Requisitos

- **Windows 10/11** (usa el runtime WebView2 de Edge, ya incluido en Win11).
- **Python 3.12+** (solo para correrlo como script; el `.exe` no lo necesita).
- Una **API key gratuita de Groq** → [console.groq.com](https://console.groq.com).
- Opcional, para el resumen: una **API key de Gemini** → [aistudio.google.com](https://aistudio.google.com/apikey).

### Instalación

```bash
git clone https://github.com/AlexVitesse/TakeMyNotes.git
cd TakeMyNotes
run.bat
```

`run.bat` crea un `.venv`, instala las dependencias pineadas y lanza la app sin consola. A mano:

```bash
pip install -r requirements.txt
python takemynotes.py
```

Aparece el **widget flotante**. Abre la ventana grande (⤡), entra a **⚙ Configuración**, pega tu
key de Groq, dale **Probar** y **Guardar**.

### API keys

Se configuran **dentro de la app** y se guardan cifradas. Como respaldo (sin cifrar) también se
leen de un `.env` o del entorno:

| Variable | Qué hace | Requerida |
|----------|----------|-----------|
| `GROQ_API_KEY` | Transcripción (siempre Groq) y resumen por defecto | Sí |
| `GEMINI_API_KEY` | Resumen y chat con Gemini; hace de respaldo de Groq | No |

```bash
copy .env.example .env
```

### Empaquetar a `.exe`

```bash
build.bat
```

Genera `dist\TakeMyNotes.exe`: un solo archivo, sin consola, sin instalar Python. El `.spec` de
PyInstaller es generado y está en `.gitignore`; `build.bat` es la única fuente de verdad de los
flags (`--add-data ui;ui`, `--collect-all webview`).

## 🎮 Uso

**Widget** (esquina inferior derecha, siempre encima, arrastrable):

| Botón | Qué hace |
|-------|----------|
| 🔵 / 🔴 ■ | Grabar / detener (con cronómetro) |
| **✎** | Bloc de notas de la sesión en curso (o la próxima) |
| 🎤 | Cortar tu micro — solo mientras grabas |
| 📷 | Captura de pantalla a la sesión — solo mientras grabas |
| **⏸ / ▶** | Pausar los dos canales — solo mientras grabas |
| **⤡** · **⚙** | Abrir la ventana de sesiones · Configuración |
| **✕** | Cerrar la app (pregunta si estás grabando o transcribiendo) |

Al detener se abre la **ventana grande**: la sesión se transcribe y se resume sola. La transcripción
se puede leer en cuanto está, sin esperar el resumen. Pestañas:

| Pestaña | Contenido |
|---------|-----------|
| `Resumen` | El acta, con markdown renderizado |
| `Transcripción` | Reproductor arriba, cada turno con su minuto; clic = el audio salta ahí; **⧉ Copiar** |
| `Media` | Capturas de pantalla de la reunión |
| `Notas` | **+ Nueva nota** con la hora; se guarda sola |
| `Chat` | Preguntas sobre la sesión |
| `Info` | Rutas en disco del JSON y del `.wav`, **▶ Escuchar**, **Borrar audio** |

Barra superior de la ventana: **⚙** Configuración y **🎙**, que vuelve a mostrar el widget si lo
cerraste. Es el único sitio desde donde se graba; nunca hay dos widgets a la vez.

<details>
<summary>🧩 <b>Problemas comunes</b></summary>

- **La transcripción repite frases o todo sale como texto corrido** — grabaste con parlantes y el
  micro captó el audio de la PC. La app descarta ese eco; si el micro no aporta nada propio, entrega
  texto sin etiquetas con un aviso. Usa auriculares.
- **«No se pudo resumir»** con un 429 — agotaste la cuota del día (Groq: 200k tokens; Gemini: ~20
  resúmenes por modelo). Cambia el modelo en ⚙ o espera; el botón **▶ Generar resumen** reintenta.
- **«model_not_found»** — Groq retiró el modelo. La app prueba sola un relevo; el arreglo definitivo
  es cambiar el modelo en ⚙.
- **Se cortan frases de un micro muy bajo** — el filtro de alucinaciones usa un umbral fijo
  (`SILENCE`). Sube la ganancia de entrada en Windows o baja la constante.
- **El `.exe` no abre y no dice nada** — mira `notas/takemynotes-widget.log` y `-window.log`.
- **La UI se corta a 150% de escala** — ya resuelto con `SetProcessDpiAwareness`; si reaparece,
  revisa que corras la última versión.

</details>

## ⚙️ Configuración (`settings.json`)

| Campo            | Default | Qué hace                                                        |
|------------------|---------|-----------------------------------------------------------------|
| `key_enc`        | —       | API key de Groq **cifrada con DPAPI**.                          |
| `gemini_key_enc` | —       | API key de Gemini, cifrada igual. Si está, resume ella.         |
| `chat_model`     | —       | Modelo del resumen. Vacío = el del proveedor activo.            |
| `keep_audio`     | `true`  | Conservar el `.wav` tras transcribir (~2 MB/min) para auditar.  |
| `label_speakers` | `true`  | Etiquetar "Yo" vs "Los demás".                                  |
| `theme`          | `auto`  | `auto` (sigue a Windows) · `light` · `dark`.                    |
| `name`           | —       | Tu nombre: etiqueta tus turnos y marca tus menciones.           |

La key se cifra con **DPAPI** (`CryptProtectData`, vía `ctypes`): solo la puede descifrar **tu
cuenta de Windows en esa máquina**. Copiar `settings.json` a otra PC no filtra la key. Un
`settings.json` viejo con la key en claro se migra solo al primer arranque.

`chat_model` existe porque los catálogos cambian sin avisar —Groq retiró `llama-3.3-70b-versatile`
de un día para otro— y porque **la cuota gratuita de Gemini es por modelo**: agotado
`gemini-3.7-flash` se sigue con `gemini-3.5-flash` cambiando una palabra.

## 🔒 Privacidad

- **El audio sale de tu equipo**: los WAV van a Groq para transcribir, y la transcripción completa
  vuelve a salir al resumir o chatear. Nada se sube a ningún otro sitio.
- **Gemini gratuito entrena con tus datos**: Google usa lo que le mandes para sus productos y
  **revisores humanos pueden leerlo** ([términos](https://ai.google.dev/gemini-api/terms)). En el
  plan de pago, no. Si el acta trae nombres de clientes o cifras, paga el plan o deja el resumen en
  Groq. La app lo advierte en ⚙ y **Quitar Gemini** borra la key.
- **El audio queda en `notas/`** por defecto (`keep_audio`), ~2 MB por minuto, y no se limpia solo.
  Si el proyecto vive en **OneDrive** u otra carpeta sincronizada, **cada reunión se sube a la
  nube**. Apaga `keep_audio` en ⚙ o borra el `.wav` desde **Info**.
- **Grabar sin consentimiento** no es legal en varias jurisdicciones. Avisa.

## 📂 Estructura

```
TakeMyNotes/
├── takemynotes.py      # backend + widget Tkinter + ventana pywebview
├── ui/
│   ├── index.html      # ventana grande (UI + lógica JS)
│   └── app.css         # estilos
├── check_ui.js         # check de la lógica JS (node check_ui.js)
├── requirements.txt    # versiones pineadas
├── run.bat             # crea .venv, instala y lanza
├── build.bat           # empaqueta a dist/TakeMyNotes.exe
├── .env.example        # respaldo de API keys (opcional)
├── CHANGELOG.md        # qué cambió y por qué
├── settings.json       # (generado, ignorado por git) configuración y keys cifradas
└── notas/              # (generado, ignorado por git) una sesión por archivo
    ├── <id>.json       # nombre, fecha, transcript, turns, notes, summary, chat…
    ├── <id>.wav        # audio mezclado (si keep_audio)
    ├── <id>_shotNN.png # capturas de pantalla
    ├── _<id>_mic.wav   # canales crudos: viven mientras la sesión está pendiente o en
    ├── _<id>_loop.wav  #   error, para poder reintentar; se borran al transcribir bien
    ├── .search.db      # índice FTS5; se puede borrar, se reconstruye solo
    └── *.log           # log rotativo por proceso, 1 MB × 3
```

`notas/` es **plana a propósito**: el id de la sesión (`AAAAMMDD_HHMMSS`) es la única clave y el
nombre del archivo *es* la relación, así que no hay nada que sincronizar entre procesos.

## 🧪 Checks

Sin frameworks ni fixtures:

```bash
python takemynotes.py --selftest   # audio, diálogo, eco, DPAPI, índice, servidor de medios, widget…
node check_ui.js                   # lógica pura de ui/index.html
```

## 🏗️ Arquitectura

Dos procesos independientes que se comunican **solo por archivos** en `notas/` (sin llamadas
entre hilos → sin cuelgues):

```
┌──────────────────────────┐        ┌───────────────────────────┐
│  WIDGET (Tkinter)        │        │  VENTANA (pywebview)      │
│  proceso siempre activo  │        │  proceso a demanda        │
│                          │ notas/ │                           │
│  · graba mic + sistema   │◄──────►│  · lee/edita sesiones     │
│  · transcribe (Groq)     │ *.json │  · resume / chatea        │
│  · nombra la sesión      │ *.wav  │  · busca / configura      │
│  · escribe <id>.json     │        │  · reintenta transcripción│
└──────────────────────────┘        └───────────────────────────┘
```

- El **widget** es lo único que corre siempre (~15–70 MB). Es Tkinter porque WebView2 **no**
  soporta transparencia real en Windows.
- La **ventana** solo pesa cuando la abres. Un **mutex nombrado de Windows** por proceso evita
  duplicados y le deja a cada uno saber si el otro sigue vivo, sin locks huérfanos.

<details>
<summary><b>Por dentro</b> — eco, alucinaciones, resumen por tramos, servidor de medios…</summary>

- **Audio**: 16 kHz mono, escrito a disco **en vivo** (a prueba de crash). Se limpia (quita DC +
  normaliza el pico) antes de transcribir.
- **Transcripción por canales**: micro → "Yo", loopback → "Los demás", en **paralelo**
  (2 llamadas Groq), fusionadas por timestamp. Archivos > ~13 min se parten en trozos de 10 min
  (límite de 25 MB de Groq).
- **Eco del micro**: el loopback es un tap digital de la salida, así que nunca contiene tu micro;
  el micro sí capta los parlantes. `drop_echo()` empareja los segmentos de los dos canales por
  **solape de intervalos** —el eco suena a la vez que el audio que lo produce, y el timestamp de
  un segmento de Whisper es el de su principio, así que comparar arranques deja fuera el eco de
  una frase dicha al final de un segmento largo— con `ECHO_JIT` de holgura por la deriva entre
  canales, más el solape de palabras (`ECHO_SIM`); descarta la copia del micro y devuelve
  dos medidas **en palabras**, no en segmentos: cuánto del micro era eco y cuánto del texto final
  aporta solo el micro. Con eco > `ECHO_DUP` (60%) y aporte < `ECHO_MIN` (15%) el micro no trae
  nada propio: se descarta el canal entero y queda un texto sin etiquetas más un aviso. Los
  fragmentos cortos (1-3 palabras) se descartan aparte cuando son **subconjunto** de lo que el otro
  canal dijo cerca. El log deja la medición de cada sesión (`eco en el micro 93%, aporte 7%`).
- **Alucinaciones sobre el silencio**: Whisper, en una ventana de 30 s sin voz, devuelve una
  muletilla de subtítulos («Gracias.», «¡Suscríbete al canal!»). No lo delata ningún campo de la
  respuesta (`no_speech_prob` viene en **0** y el `avg_logprob` es normal), así que la única señal
  es el audio: `has_voice()` descarta el segmento si su tramo no supera `SILENCE` en el canal
  **crudo** — crudo y no el de `cleanup()`, que normaliza el pico y le sube el volumen al ruido.
- **Turnos**: la sesión guarda `turns` `[{t, who, text}]` —con el segundo en que arranca cada
  turno— y el mismo diálogo en texto plano `Etiqueta: lo que dijo`, que es lo que se indexa en FTS5
  y lo que va al modelo. Las dos cosas salen de `dialog_turns()`. Las sesiones anteriores a la
  v0.6 no tienen `turns`: la UI ofrece **Sincronizar con el audio** (`Api.resync`).
- **Resumen por tramos**: el tier gratis de Groq acepta 8 000 tokens por minuto —y cuenta la
  entrada **más el tope de salida**— mientras que una reunión de una hora son ~13 000. Con Gemini
  cabe entera y el acta sale de una sola pasada, que es la diferencia entre capturar o perder un
  descarte cuya razón se dijo veinte minutos después. `split_text()` corta por párrafo en trozos
  de `CHAT_CHARS`, se resume cada tramo y después los resúmenes. `chat()` respeta `retry-after`
  en los 429. Los clientes se crean con `max_retries=0` **a propósito**: los reintentos del SDK
  multiplicados por los de `chat()` gastaban 20 peticiones, y la cuota gratuita de Gemini son 20
  al día por modelo. El 429 **del día** (`tokens per day (TPD)` en Groq,
  `GenerateRequestsPerDayPerProjectPerModel` en Gemini) salta al relevo sin esperar.
- **Qué le exige `SUM_SYS` al modelo**: `gpt-oss-120b` devolvía tareas en tabla, resúmenes de
  hasta el 95% del largo de la transcripción, y se **inventaba responsables**. El prompt pide
  párrafos y bullets de un nivel; una tarea **solo si alguien se comprometió**; `sin dueño` cuando
  nadie se hizo cargo; y nada de expandir siglas. Al cambiar de modelo, revisar con una reunión real.
- **Progreso**: el campo `stage` de la sesión lo escribe quien trabaja y lo lee la ventana en su
  poll. Mismo canal que todo: un archivo.
- **Inactividad**: el grabador guarda el último bloque cuyo pico supera `VOICE` (0.05) en
  cualquier canal; el widget compara contra `IDLE_WARN` (300 s) e `IDLE_WARN + IDLE_STOP` (420 s).
  La decisión vive en `idle_state()`, una función pura.
- **Persistencia**: escritura **atómica** (`.tmp` + `os.replace`). Los cambios de campos sueltos van
  por `_patch(sid, **campos)`, que aplica sobre lo que hay en disco: transcribir tarda minutos y en
  ese rato la ventana puede estar guardando notas.
- **DPI**: la ventana pide `SetProcessDpiAwareness` antes de crearse; sin eso, a 150% el viewport
  CSS quedaba en 693 px. El widget Tk se deja virtualizado a propósito.
- **Servidor de medios**: la ventana se sirve en `http://127.0.0.1:PORT`, así que Chromium rechaza
  `file:///` para audio e imágenes. Por eso la ventana levanta **su propio servidor** para `notas/`
  (`start_media_server`) con **soporte de Range** (mover la aguja sin bajar 128 MB). Solo loopback,
  puerto efímero, **token aleatorio en la ruta**, solo archivos directamente en `notas/` y sin
  cabeceras CORS: el `<audio>` reproduce y ninguna página web puede leer los bytes.
- **Búsqueda**: índice FTS5 en `notas/.search.db`, sincronizado **solo al buscar** comparando el
  `mtime` de cada JSON. Tokenizador `unicode61 remove_diacritics 2`; la consulta se convierte a
  tokens citados con prefijo (`"presu"* "puesto"*`), AND y a prueba de sintaxis FTS.
- **Borrado**: `delete_session` quita JSON + `.wav` + canales crudos + fila del índice. Si borras
  una sesión mientras se transcribe, el hilo del widget **no** la resucita.

</details>

<details>
<summary><b>Robustez</b></summary>

- **Cero pérdida de datos**: el audio se escribe a disco mientras grabas; el WAV se cierra en
  `finally` aunque el dispositivo falle a mitad.
- **Transcripción reintentable**: si falla (red / key / Groq), el audio queda y aparece
  **Reintentar**; si vuelve la red, reintenta sola.
- **Si Groq retira el modelo de chat, la app no se cae**: ante un **404**, `switch_chat()` pregunta
  por `models.list()` y reintenta con el primer relevo de `CHAT_ALT` que esa key vea.
- **Sin sesiones zombis**: cada `pending` guarda el PID del proceso que la transcribe; si murió, la
  sesión pasa a error con **Reintentar** y el barrido automático la recoge al abrir la app.
- **Una transcripción a la vez**: dos en paralelo serían ~2 GB de WAV en `float32` en memoria y el
  doble de tokens por minuto. La que espera se muestra "En cola…".
- **Índice desechable**: `notas/.search.db` es caché, no datos. Bórralo y se reconstruye.
- **Log diagnosticable**: tracebacks completos en `notas/*.log`, incluido cualquier crash del `.exe`.

</details>

## 📝 Notas

### Limitaciones conocidas

- **No distingue entre varios remotos**: solo separa "Yo" (tu micro) vs "Los demás" (audio del
  sistema). La diarización por persona requiere `pyannote`, descartado por peso.
- **El filtro de eco compara texto, no audio**: si Whisper transcribe la misma frase distinta en
  cada canal, la copia sobrevive. Cuando descarta el canal del micro entero se pierde lo poco que
  hayas dicho (en la prueba: 59 de 7752 palabras). **Con auriculares no existe el problema.**
- **Umbrales fijos** para silencio (`SILENCE`) e inactividad (`VOICE`): con un micro muy flojo o
  muy ruidoso hay que ajustar la constante.
- **Tus menciones se marcan por coincidencia de texto**: si te llamas Eric, "Erica" también se marca.
- **Lo semántico es por sinónimos, no por vectores**, y necesita red; la búsqueda literal funciona
  sin ella. El `<mark>` de la UI no ignora acentos aunque la búsqueda sí.
- **El markdown renderizado es un subconjunto** (títulos, negrita, itálica, `código`, listas); por
  eso `SUM_SYS` le prohíbe tablas al modelo.
- **Sin streaming** de texto ni atajo global de teclado.
- La librería relee los JSON en cada listado: bien para decenas de sesiones, no para miles.

### Decisiones

- **Groq** en vez de Whisper local → la transcripción corre en la nube, no fríe tu equipo.
- **Tkinter para el widget** → transparencia nativa y peso mínimo; **pywebview para la ventana** →
  diseño fiel en CSS, y solo pesa a demanda.
- **Comunicación por archivos** entre procesos → eliminó de raíz los cuelgues por llamadas a
  WebView2 desde hilos de fondo.
- **SQLite FTS5 en vez de BD vectorial** → Groq no expone embeddings, y vectorizar sumaría una
  dependencia y 90–400 MB de pesos al `.exe`. Lo semántico se resuelve expandiendo la consulta con
  el LLM que ya está en el proyecto.
- **Eco por texto en vez de cancelación por señal** → no depende del volumen de cada PC, no pierde
  audio y se prueba sin micrófono. Se paga transcribir dos veces, que con Whisper turbo es barato.
- **DPAPI en vez de `keyring`** → ctypes puro, ~15 líneas, sin dependencia ni `hiddenimport`.

---

<div align="center">

**Hecho por [AlexVitesse](https://github.com/AlexVitesse)**

</div>
