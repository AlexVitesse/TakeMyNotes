# De la grabación al acta

```
grabar ──► _mic.wav / _loop.wav ──► wav_chunks (10 min) ──► Whisper por canal ──► drop_echo
  (Recorder, en vivo a disco)        tramo_mudo? no se sube      (2 en paralelo)       │
                                                                                       ▼
 Minuta ◄── Api.minuta ◄── acta (summary) ◄── _summarize ◄── transcript + turns ◄── dialog_turns
 (a pedido)                  TÍTULO → name     (tramos si no cabe)
```

## 1. Grabación — `Recorder`

Micro y loopback de la salida (WASAPI) a 16 kHz mono, escritos a `notas/_<id>_mic.wav` y
`_<id>_loop.wav` **mientras** se graba: si la app se cae, el audio está en disco. Por bloque se
mide el **rms** (no el pico: un chasquido deja el pico alto para siempre) y se guarda:

- cuántos bloques tuvieron voz (`> VOICE`, 0.01), para decidir al terminar si un canal está mudo
  (`canal_mudo`: menos del 15 % de bloques con voz);
- el nivel del último bloque (`mic_level`, `loop_level`), que el widget dibuja en vivo;
- el instante del último sonido, para el aviso de inactividad (`idle_state`).

Pausar y cortar el micro siguen leyendo el dispositivo (si no, se atrasa) pero no escriben.

## 2. Transcripción — `_do_transcription`

**Por trozos, nunca entero.** `wav_chunks(rutas)` devuelve trozos crudos de `CHUNK_SECS` (600 s,
38 MB en float32). Con dos rutas los mezcla trozo a trozo. Antes se cargaba cada canal entero y se
duplicaba normalizado: la reunión de 1 h 45 llegaba a ~2,5 GB de pico.

Por cada trozo, `transcribe_channel`:

1. `tramo_mudo(raw)`: si ninguna ventana de 30 s supera `SILENCE` (0.004), **no se sube**. Ahorra
   19 MB de subida y cuota de audio por hora de Groq, que con dos canales se gasta el doble.
2. Sube una copia normalizada (`cleanup`: quita DC y normaliza el pico, en sitio).
3. Descarta los segmentos cuyo tramo **crudo** no tiene voz (`has_voice`): Whisper inventa
   «Gracias.» sobre el silencio y ningún campo de la respuesta lo delata.
4. Suma el tiempo base del trozo a cada segmento.

Con **Identificar hablantes** activo, micro → «Yo» (o tu nombre) y loopback → «Los demás», en
paralelo; se salta el canal que quedó mudo. Si está apagado, se transcribe la mezcla sin
etiquetas.

**Eco** (`drop_echo`): con parlantes, el micro repite lo que dicen los demás. Se descarta la copia
del micro comparando intervalos de tiempo y palabras. Si casi todo el micro era eco y no aporta
nada propio, se queda solo el loopback, sin etiquetas, con un aviso.

Resultado: `turns` (`[{t, who, text}]`, con el segundo de cada turno) y `transcript` (el mismo
diálogo en texto plano, que es lo que se indexa y lo que va al modelo).

## 3. Acta — `auto_summary`, `summarize_text`, `_summarize`

Arranca sola al terminar la transcripción. `chat_client()` elige el cliente: Gemini si hay key
(con Groq de relevo), Groq si no. `con_relevo` repite **todo** el trabajo con el otro proveedor si
el primero falla (cuota del día, key mala, caída).

- **Si la transcripción cabe** en un request (`trozo`: 12 000 chars en Groq, 400 000 en Gemini),
  una sola llamada con `SUM_SYS`.
- **Si no cabe**, por tramos: `split_text` corta por párrafo (cambio de hablante); cada tramo pasa
  por `TRAMO_SYS` y después se escribe el acta con los resúmenes de todos. Cada tramo recibe:
  - las **notas** del usuario (guían qué buscar);
  - `HASTA AHORA:` con las líneas **PERSONAS** de los tramos anteriores, para que quién es quién no
    se reinicie cada 12 000 caracteres;
  - en Groq, tope de salida `TRAMO_OUT` (2048) y `reasoning_effort="low"` para gpt-oss: Groq
    cuenta entrada **más** tope de salida contra los 8 000 tokens por minuto, y con 4096 cada tramo
    esperaba ~55 s al siguiente.
- El acta recibe la **fecha** (`cuando()`: `30/09/2026 10:15 (miércoles)`) para pasar «el
  viernes» a fecha absoluta.
- Todas las llamadas van con `temperature` = 0.3 (`TEMP`): es extracción, no escritura creativa.

`SUM_SYS` pide que la **primera línea** sea `TÍTULO: …`. `split_title` la separa y
`store_summary` guarda el acta y usa el título como nombre **solo si la sesión no tenía uno**. Así
se ahorra el request aparte de `name_session`, que en Groq salía justo antes del resumen y del
mismo balde por minuto. Si el resumen falla, se llama a `name_session` como antes, para que la
sesión no quede sin nombre.

`chat()` es el único sitio que habla con el modelo: reintenta 429 del minuto y 5xx con esperas de
verdad, salta al relevo sin esperar ante el 429 del día, cambia de modelo de Groq ante un 404
(`switch_chat`), avisa `[…cortado…]` si el texto se truncó y trata la respuesta vacía como error.
Los errores que ve el usuario pasan por `humano()`.

## 4. Acciones con estado — `acciones`, `md(texto, done)`

El acta no cambia: los tildes se guardan aparte, como **índices** de los bullets de la sección
ACCIONES, en `s["done"]`. Python (`acciones`) y JS (`md`) cuentan igual: los ítems (`-`, `*`, `•`
o `1.`) bajo un título ACCIONES, en orden. El responsable es la última línea en negrita suelta
(`**Eric**`) que los encabeza. `selftest` y `check_ui.js` prueban **la misma acta** en los dos
lados, para que un cambio en uno no le ponga el tilde a otra acción.

Al rehacer el acta se borran los tildes (`done=None`): los índices ya no señalarían lo mismo.

`Api.pending()` junta las acciones sin tildar de todas las sesiones para la vista **Pendientes**.

## 5. Minuta — `Api.minuta`, `MINUTA_SYS`

Es el documento formal para enviar: encabezado, asistentes, orden del día, desarrollo, acuerdos
numerados con responsable y fecha, pendientes y próxima reunión. **No relee la transcripción**:
sale del acta + notas + datos que se calculan en Python (fecha, hora de inicio y fin, duración),
en **un solo request** que entra en Groq sin trocear y gasta una sola llamada diaria de Gemini.

- Si no hay acta, primero se genera el acta.
- No es automática: se pide cuando se va a enviar.
- Se guarda en `s["minuta"]` (errores en `s["min_error"]`) y se indexa en la búsqueda.
- El prompt exige «No se definió» en vez de inventar, y «(inferido)» en lo deducido.

## 6. Chat — `ask_text`, `_ask`

Si la transcripción cabe, una llamada. Si no, se pregunta tramo por tramo, se descartan los que
responden `SIN DATOS` y se unifican las respuestas.

## 7. Búsqueda — `sync_index`

SQLite FTS5 en `notas/.search.db`, reindexado solo para los JSON cuyo `mtime` cambió. Indexa
nombre, transcripción, notas, acta y minuta. Las sugerencias «por significado» piden sinónimos al
modelo y buscan con OR.

## Campos de la sesión (`notas/<id>.json`)

| Campo | Qué es |
|-------|--------|
| `id` | `AAAAMMDD_HHMMSS`: el instante en que se apretó grabar |
| `name`, `date`, `time`, `dur` | Nombre (del acta o del usuario), fecha y hora de inicio, duración en s |
| `status` | `pending` · `done` · `error` |
| `stage` | Progreso visible («Transcribiendo… 3/12»); vacío si no hay trabajo en curso |
| `pid` | Proceso dueño del trabajo en curso (ver [concurrencia.md](concurrencia.md)) |
| `transcript`, `turns` | Transcripción en texto plano y por turnos con tiempo |
| `notes` | Notas del usuario |
| `summary`, `sum_error` | Acta y último error al resumir |
| `done` | Índices de las acciones tildadas |
| `minuta`, `min_error` | Minuta y último error al redactarla |
| `chat` | `[{q, a}]` |
| `fav`, `warn`, `error`, `retries` | Favorito, avisos de audio, error de transcripción, intentos automáticos usados |
| `mic_silent`, `loop_silent` | Canales que llegaron mudos (no se transcriben) |
