# Pruebas

Sin frameworks ni fixtures: dos scripts que fallan en el primer `assert` roto.

```bash
python takemynotes.py --selftest   # backend, widget y API de la ventana
node check_ui.js                   # lógica de ui/index.html
```

Regla: **cada arreglo deja un `assert`**. Sin check no está terminado.

## `python takemynotes.py --selftest`

Tarda unos 20 s. Trabaja en una carpeta temporal (`NOTAS` y `SETTINGS_PATH` se redirigen), así que
no toca tus notas. Hace una captura real del escritorio y levanta un servidor de medios en
loopback. No llama a Groq ni a Gemini: los clientes son falsos (`SimpleNamespace`), y `Groq` y
`_env_key` se reemplazan durante la prueba de punta a punta.

Qué cubre, por área:

| Área | Checks |
|------|--------|
| Modelos | relevo ante 404; 429 del día vs 429 del minuto vs 503; `humano()` por código |
| `chat()` | `content=None` con corte → aviso; vacío o `choices=[]` → error; `temperature`, tope de salida y `reasoning_effort` que se mandan |
| Resumen | tramos con notas y `HASTA AHORA`; fecha en el acta; `split_title`; `store_summary` no pisa el nombre y borra los tildes; `cuando()`; `con_relevo` |
| Acciones | `acciones()` sobre la misma acta que `check_ui.js`; `toggle_done`; `pending()` |
| Minuta | un solo request, con el acta y la duración calculada; error guardado en `min_error` |
| Audio | `rms`, `has_voice`, `canal_mudo`, `cleanup`; **25 min reales en 3 trozos** con su tiempo base; mezcla en streaming de 25 + 12 min; trozo mudo no se sube |
| Transcripción de punta a punta | Groq falso: estado `done`, nombre desde el acta, `.wav` escrito, canales borrados; y el caso en que la sesión se borra en medio (1.3); y una sesión que ya no existe (3.1) |
| Diálogo | `dialog_turns`, `format_dialog`, tope de turno, eco (`drop_echo`) con casos reales |
| Concurrencia | ver [concurrencia.md](concurrencia.md#cómo-se-prueba) |
| Persistencia | DPAPI ida y vuelta; key nunca en claro; `_plain` sin bucle de escritura |
| Índice | FTS5: AND, prefijo, título, notas, borrado |
| API de la ventana | lista (con caché y `nosum`), búsqueda, notas, renombrar sesión y hablante, favoritos, reintentos, exportar `.md`, rutas, servidor de medios con Range y sus rechazos (token, `..`, subcarpetas) |
| Widget | geometría del pill; clics; arrastre; mute y pausa; niveles en vivo; aviso de canal mudo; tooltips; atajo global (con `toggle` falso); protocolo de cierre |

## `node check_ui.js`

Evalúa el `<script>` real de `ui/index.html` con un DOM de mentira (objetos memorizados por
selector) y un `pywebview.api` falso.

| Área | Checks |
|------|--------|
| Resaltado | tokens del buscador y tu nombre en una pasada, a prueba de RegExp y de HTML |
| Transcripción | filas con minuto y `seek`; sesiones viejas sin tiempos; etiqueta renombrable; notas con hora intercaladas (`noteMarks`) |
| Markdown | subconjunto, escapado; listas numeradas con `value`; títulos del acta; **7 títulos de la Minuta**; checkboxes solo en ACCIONES y con el estado de `done` |
| Lista | skeleton en sesiones en curso; punto de «sin acta»; grupos por fecha |
| Confirmaciones | eliminar, borrar audio y sincronizar no llaman al backend sin confirmar; si eliminar falla, la sesión sigue |
| Poll | el último tramo llega al detalle; **escribiendo no se repinta** y no se pierden las notas sin guardar |
| Chat | el input se apaga en vuelo y una segunda pregunta se ignora |
| Varios | renombrar título (normalización), `jsarg` con rutas de Windows, «+ Nueva nota», widget, Configuración |

## Lo que hay que probar a mano

Lo que depende de layout, de hardware o de servicios reales:

- **3.5**: pestaña Transcripción con audio → ⚙. El reproductor tiene que quedar velado.
- **Nivel en vivo y Probar audio**: hablar y poner algo a sonar.
- **Ctrl+Shift+R** desde otra app.
- **Instalador**: instalar, usar cada acceso del menú Inicio, actualizar con la app abierta,
  desinstalar y comprobar que `notas\` sigue.
- **Calidad del acta y de la minuta**: con 3 reuniones reales (una corta, una con eco, una larga),
  comparando contra las actas anteriores. Ningún test automático dice si el acta es buena.
