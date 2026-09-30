# Changelog

Formato: [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).
El detalle y el *por qué* de cada decisión están en los mensajes de commit (`git log`).

## [0.8.0] — 2026-09-30

Sale de la revisión del 30/09 (`PLAN.md`): integridad de datos entre los dos procesos, memoria y
cuota al transcribir, un acta más estable, la Minuta, y la UX que faltaba alrededor.

### Corregido

- **`os.replace` fallaba con `WinError 5` si el otro proceso tenía el JSON abierto.** La ventana
  relee los JSON en cada poll, así que durante una transcripción pasaba seguido; si le tocaba al
  patch final, la sesión quedaba `pending` para siempre y con los canales ya borrados. Ahora
  `_replace()` reintenta, y el resultado se guarda **antes** de borrar los canales crudos: si
  falla, la sesión queda en error reintentable.
- **Lectura-modificación-escritura sin lock entre procesos.** Notas, favorito, renombrar,
  reintentar y chat pasan por `_patch`, y `_patch` toma un mutex nombrado
  (`Local\TakeMyNotes.patch`): el `threading.Lock` de antes solo valía dentro de un proceso.
- Borrar una sesión mientras se transcribe ya no deja un `.wav` ni canales huérfanos; borrarla
  antes de que arranque no tira un traceback.
- `chat()` con `content=None` o `choices=[]` (Gemini bloqueando, gpt-oss sin tope) fallaba con
  `'NoneType' object has no attribute 'strip'`; ahora es un error normal y entra el relevo.
- Dos preguntas seguidas al chat perdían una respuesta; el input se apaga mientras piensa.
- **Probar** validaba siempre el modelo por defecto de Groq, no el elegido.
- El poll repintaba el detalle mientras escribías notas, chat o el título (se iba el foco), y
  podía solaparse consigo mismo.
- El reproductor de la transcripción se veía por encima del modal de Configuración (`z-index`).
- Si DPAPI falla, leer la configuración ya no la reescribe en cada lectura.
- **Eliminar podía decir que funcionó sin haber borrado nada**, y el borrado no tomaba el mutex
  (un `_patch` en curso podía volver a escribir la sesión). Ahora `remove_session()` borra bajo el
  mutex, reintenta si el archivo está en uso y, si no puede, la ventana lo avisa.
- **El reintento automático y uno manual podían arrancar a la vez** y subir el audio dos veces.
  `claim()` comprueba y reclama la sesión en el mismo lock.

### Cambiado

- **Memoria al transcribir: de ~2,5 GB a ~120 MB** en la reunión de 1 h 45. El audio se lee y
  normaliza por trozos de 10 min, y el `.wav` de auditoría se mezcla en streaming.
- **Los trozos mudos no se suben a Whisper**: ahorra cuota de audio (que con dos canales se gasta
  el doble) y alucinaciones.
- **Resumen por tramos más rápido en Groq**: tope de salida de 2048 en los tramos y
  `reasoning_effort=low` para gpt-oss; el título sale del acta (un request menos, justo antes del
  resumen). `temperature=0.3`.
- **Acta mejor informada**: cada tramo recibe las notas del usuario y quién es quién de los tramos
  anteriores, y el acta recibe la fecha para escribir fechas absolutas.
- La lista cachea por archivo y solo relee los JSON que cambiaron.
- Los errores de la API se muestran en castellano ("Cuota del día agotada…"), no como JSON crudo.
- `groq` se importa al usarse: el widget arranca más liviano.

### Añadido

- **Instalador** (`installer.iss`, Inno Setup 6; lo compila `build.bat` si lo encuentra): por
  usuario y sin admin, con carpeta en el menú Inicio — TakeMyNotes, Sesiones, Configuración,
  Cerrar, Carpeta de notas, Desinstalar — y opcionales escritorio e inicio con Windows.
  Argumentos nuevos del `.exe`: `--settings` y `--quit` (cierra con WM_CLOSE, así el widget
  pregunta si está grabando). `--window` con la ventana ya abierta la trae al frente.
- **Minuta**: pestaña nueva, documento formal para enviar, generado a pedido desde el acta.
- **Acciones con tilde** en el acta y vista **Pendientes** (☑) con las abiertas de todas las
  sesiones, por responsable.
- **Nivel de audio en vivo** en el widget y aviso ámbar si un canal lleva 60 s sin señal; botón
  **Probar micro y audio de la PC** en Configuración.
- **Ctrl+Shift+R** graba/detiene desde cualquier app; tooltips en los botones del widget.
- **⧉ Copiar acta**, **⧉ Copiar minuta** y **⤓ Exportar .md**.
- Ventana: `Ctrl+F`, `Esc`, `↑/↓`, `Ctrl+1…7`, `Supr`, `Espacio`; lista agrupada por fecha,
  contador y ✕ en el buscador, punto ámbar en las sesiones sin acta, toast de estado.
- Notas con hora intercaladas en la transcripción, clic para saltar el audio.
- Renombrar «Los demás» (o cualquier hablante) por sesión.
- Configuración en dos bloques y el aviso de privacidad de Gemini plegado.
- Accesibilidad: foco visible, `aria-label` en los botones de glifo, contraste de `--muted-48`.
- `docs/`: cómo está hecha la app por dentro, y el registro de esta revisión ítem por ítem.

## [0.7.4] — 2026-08-20

Un día sin actas. Las dos cuotas gratuitas agotadas a la vez y el modal de Configuración
inalcanzable: los dos bugs los destapó la misma tarde.

### Corregido

- **Un resumen fallido gastaba 20 requests, y la cuota diaria de Gemini son 20.** `chat_client()`
  construía los dos clientes con `max_retries=4` y `chat()` reintenta otras 4 veces por encima:
  5 × 4 = **20 peticiones por cada llamada que falla**. El plan gratuito de Gemini da 20 al día
  **por modelo**, así que el primer 429 del día se llevaba el día entero. Medido en el log del
  20/08: **120 requests de chat entre la ventana y el widget, 10 con respuesta 200**; del lado de
  Gemini, 60 intentos — 3 con 503 y 57 con 429 — para lo que tenía que haber sido un request.
  Ahora los clientes de chat van con `max_retries=0` y los reintentos que sirven, con esperas de
  verdad, los sigue haciendo `chat()`. Los clientes de Whisper se quedan con sus 4: la
  transcripción no gasta de esa cuota.
- **El 429 del día se trataba como el 429 del minuto.** Son el mismo número y no tienen nada que
  ver: el del minuto se rellena esperando, el del día no vuelve hasta que Groq dice
  `try again in 47m` o hasta medianoche en Gemini. Se esperaban 20 + 40 + 60 s por proveedor
  antes de llegar al relevo, que es lo único que podía salvar el acta: **seis minutos tirados por
  reunión, dos veces**. `_agotado_el_dia()` lo distingue por el texto, que es lo único que los
  separa — Groq escribe `tokens per day (TPD)` y Gemini nombra la cuota
  `GenerateRequestsPerDayPerProjectPerModel` — y `_retryable()` devuelve `False`: se va derecho al
  relevo. Selftest nuevo para los dos casos y para el 503.
- **Los cortes de red los reintenta ahora `chat()`.** Antes los cubrían los reintentos del SDK que
  acabamos de quitar; `_retryable()` acepta `APIConnectionError` y `APITimeoutError` para que
  quitar los reintentos del SDK no convierta un bache de red en un salto de proveedor.
- **Configuración no se podía cerrar.** La ventana mide 680 px de alto como mucho
  (`window_size()`); el modal, desde que le entraron la key de Gemini con su aviso y el campo de
  modelo, pasa de **1100 px**. Con `.mask{align-items:center}` y sin `overflow`, se centraba
  desbordando por arriba y por abajo, y la fila de botones — Cancelar incluido — quedaba fuera de
  la pantalla. La única salida era el clic en la franja lateral del backdrop, que no parece un
  botón. Ahora el modal tiene `max-height` con scroll propio y la `.mrow` va `sticky` abajo, así
  que los botones se ven siempre. Y `Escape` cierra lo que esté encima: antes solo llamaba a
  `closeConfirm()`, nunca a `cancelSettings()`.
- **El log decía "Groq rate limit" también cuando el que limitaba era Gemini.** Ahora nombra el
  modelo. Con dos proveedores en juego, un log que miente sobre cuál falló cuesta media hora.

### Nota

- El acta de la reunión del 20/08 16:48 (`20260820_164846`) **está escrita a mano**, no la generó
  el pipeline: ese día las dos cuotas estaban agotadas y la transcripción — 16.011 chars — se
  habría quedado sin resumen. El propio texto lo dice en su última línea. Los arreglos de arriba
  hacen que la próxima vez el relevo entre en segundos en vez de en seis minutos, pero no
  inventan cuota: con las dos gratuitas agotadas no hay acta automática. La salida sigue siendo
  la que ya documenta la UI — cambiar el modelo en Configuración, porque la cuota de Gemini es
  por modelo.

## [0.7.3] — 2026-08-20

Pasada sobre el resumidor con seis reuniones reales delante, a partir de una revisión del acta
del 19 de agosto hecha a mano. Dos de los tres arreglos son bugs que se veían todos los días.

### Corregido

- **El acta salía cortada a media palabra, y de forma distinta en cada corrida.** `chat()` no
  pedía tope de salida, así que Groq aplicaba el suyo por defecto — **3072 tokens** — y en un
  modelo de razonamiento como `gpt-oss-120b` el "pensar" gasta de ese mismo balde. Medido sobre
  la reunión del 19: `finish_reason=length`, `out=3072`, con 8438 chars de contenido y 4863 de
  razonamiento; en otra corrida del mismo texto el contenido se quedó en 1103 chars. Ahora el
  tope se pide explícito (`CHAT_OUT`) y, si aun así se corta, el texto lo dice en vez de fingir
  que la reunión terminó ahí. De paso, el TPM real de la cuenta es **8000**, no los 12000 que
  decía el comentario, y Groq cuenta la entrada **más el tope de salida que pidas**: `CHAT_CHARS`
  y `CHAT_OUT` tienen que sumar por debajo de eso o el request ni sale (413).
- **La hora de la lista era la del final de la reunión, no la del principio.** El `id` de sesión
  se genera al apretar grabar, pero `date` y `time` se calculaban con `now()` en
  `_save_session()`, que corre al parar. Una junta de una hora aparecía una hora tarde: la
  reunión del 19 empezó a las 12:04 y se listaba a las 13:03. Ahora la hora sale de
  `session_start(sid)`, que es el instante del inicio y ya estaba ahí. Las 27 sesiones guardadas
  quedaron corregidas; la hora de fin sigue siendo deducible como inicio + `dur`.
- **No se podía rehacer un resumen sin perderlo.** El botón de generar solo se pintaba cuando
  *no* había resumen. Ahora también con uno hecho: el viejo se queda en pantalla hasta que llega
  el nuevo, el error de un reintento fallido se ve (esa rama del render no lo mostraba) y deja de
  quedarse pegado cuando el siguiente intento funciona.

### Cambiado

- **`SUM_SYS` y `TRAMO_SYS` reescritos.** El acta ya no es «resumen + bullets + tareas», que
  producía cincuenta tareas sin dueño donde «añadir espacios publicitarios» pesaba lo mismo que
  el acuerdo de cierre. Ahora se separa por **modalidad**: DECIDIDO / PROPUESTO (sin respuesta) /
  DESCARTADO con su motivo / YA ENTREGADO, más una tabla de QUIÉNES al principio, una sección de
  **preguntas que quedaron sin responder** — que es donde suele estar lo que bloquea todo lo
  demás — y un tope de **10 acciones** con dueño o `SIN DUEÑO - ASIGNAR` y `Bloqueada por:`.
  Reglas duras: prohibido escribir un nombre, marca o tecnología que no esté literal en la
  transcripción (inventaba «Sigfox», que aparece 0 veces), lo mal transcrito se copia con `[sic?]`
  en vez de racionalizarlo (de «lo del cárcamos» salió un «proyecto C» que no existe), y no se
  convierte una restricción del cliente en tarea propia, ni un «podríamos» en un plan, ni una
  anécdota sobre un excliente en un entregable. Lo que se está vendiendo no es lo que ya existe.
- **Los tramos ahora rescatan lo que el pase final no puede ver.** Cada tramo devuelve una línea
  `PERSONAS:` con la frase textual que delata a cada quien (cómo se presentó, cómo lo llamaron,
  si dice «ustedes» o «nosotros»), copia entera cualquier enumeración de acuerdos y marca los
  rechazos con la frase con la que se tumbó la propuesta. Sin eso, el pase final decidía quién
  era quién sin haber oído las presentaciones.

### Añadido

- **Reunión larguísima**: si ni los resúmenes de los tramos entran en un request, se resumen otra
  vez por el mismo camino. Había una sesión de 99.908 chars en `notas/` que reventaba el pase
  final con un 413.

### Añadido — Gemini como motor de resumen, opcional

- **Key de Gemini en Configuración.** Si está puesta, se resume y se chatea con
  `gemini-3.7-flash`; si no, todo sigue en Groq. La transcripción **siempre** es Groq, que es
  quien tiene Whisper. Gemini expone un endpoint con la misma forma que el de OpenAI, así que
  se le habla con el SDK de OpenAI (`openai`, **dependencia nueva**, pineada en
  requirements.txt). El de Groq **no** sirve aunque el endpoint sea compatible: pega
  `/openai/v1/chat/completions` a la `base_url` y Gemini espera `/chat/completions` — devuelve
  404 con cualquier combinación de barras.
- **Con Gemini no se trocea, y se nota.** Medido sobre una reunión real de 50.514 chars:
  **1 request y 19 s**, contra 6 requests y 235 s por tramos. De los cuatro fallos que el troceo
  causaba, tres desaparecen en la primera corrida: el descarte de T1 sale entero **con su motivo**
  ("está en proceso de cambio y migración… la nueva solución ya incorpora monitoreo nativo"), los
  cuatro puntos del cierre salen enumerados en DECIDIDO, y YA ENTREGADO deja de contradecirse
  (dice "temperatura y humedad", que es lo que hay, y no el pitch de "electricidad y vibración").
  El cuarto —quién es quién— mejora pero sigue fallando: unifica bien a un participante con su apodo
  y coloca bien a otro del lado del cliente, pero confunde los roles de otros dos.
  El modelo, el tamaño de tramo y el tope de salida viajan pegados
  al cliente que devuelve `chat_client()`, así que los ocho sitios que llaman a `chat()` no se
  enteran de con quién hablan, y `summarize_text()` entra por la rama de un solo request que ya
  existía. Se va el troceo, y con él los descartes sin referente y los participantes mal
  atribuidos (ver *Sabido* más abajo).
- **La advertencia está en el diálogo, no en la documentación.** Con el plan gratuito, Google
  usa la transcripción y el resumen para entrenar sus productos y revisores humanos pueden
  leerlos ([términos](https://ai.google.dev/gemini-api/terms)); en el de pago, no. En estas
  transcripciones hay vulnerabilidades de clientes con nombre, cifras contra meta y salidas de
  personal, así que la advertencia dice eso y no un «tus datos podrían usarse» genérico.
- Se guarda cifrada con DPAPI igual que la de Groq, y también se puede dar por `.env`
  (`GEMINI_API_KEY`). **Vaciar el campo no la borra** — el diálogo se abre siempre en blanco y
  guardar cualquier otro ajuste la tiraría: para quitarla está el botón *Quitar Gemini*.
- *Probar* comprueba las dos keys por separado: si la de Gemini está mal, el error se vería
  recién al terminar una reunión entera.
- **No hay selector de proveedor, y a propósito.** Con las dos keys puestas, la de Groq queda de
  `relevo` colgada del cliente: si Gemini no puede — cuota del día agotada, key mala, servicio
  caído — `con_relevo()` **repite el trabajo entero** con Groq y la reunión termina con su acta.
  Entero y no desde donde falló, porque el troceo depende de cuánto le cabe a cada proveedor: lo
  que Gemini resume de una pieza, Groq lo tiene que partir en tramos. Los dos planes gratuitos
  tienen tope diario, y el día que uno se agota es justo el día que no querés quedarte sin acta.
  Si no hay relevo, el error sube tal cual: nunca se traga en silencio.
- **El modelo del resumen se elige en Configuración.** Campo de texto libre: vacío usa el del
  proveedor activo. Sirve para seguir el catálogo sin tocar código cuando retiran un modelo —ya
  pasó con `llama-3.3`— y, sobre todo, porque **la cuota gratuita de Gemini es por modelo**: son
  ~20 requests al día de *cada* uno, así que agotado `gemini-3.7-flash` se sigue con
  `gemini-3.5-flash` cambiando una palabra. *Probar* valida que el nombre exista, y el relevo
  entra siempre con su modelo por defecto, que es el único que se sabe que está.
- **`GEMINI_OUT` sube de 8192 a 32768.** Ese 8192 venía de Groq, donde lo limita el TPM; en
  Gemini no hay TPM que lo pague y los flash razonan largo del mismo balde de salida:
  `gemini-3.5-flash` truncaba el acta en 4 de 6 llamadas. Con el tope arriba, la misma reunión
  pasa de 2041 chars sin sección DESCARTADO a 5224 chars con el descarte de T1 completo.
- **`_retryable()` ahora cubre los 5xx, no solo el 429.** Gemini contesta "model is currently
  overloaded" con un 503 bastante seguido, y como cualquier excepción disparaba el relevo, un
  bache de un minuto hacía que la reunión se resumiera con el proveedor equivocado. Un 5xx se
  reintenta con el mismo proveedor; el relevo queda para cuando de verdad no puede.

### Probado y descartado

- **Tramos de 20k chars a cambio de bajarles la salida a 2048.** El cuello de botella del tramo
  es la salida, no la entrada: con 20k que comprimir en 2048 tokens devolvió DECIDIDO, DESCARTADO
  y YA ENTREGADO **vacíos**. Manda la salida.
- **Solape de 1000 chars entre tramos**, para que una propuesta y el «no» que le llega tres
  turnos después quedaran juntos en algún tramo. No mostró mejora medible y la única comparación
  directa lo pone en contra: con solape, DESCARTADO pasó de traer la cita del rechazo a decir
  «ninguna propuesta fue descartada».
- **`groq/compound` para meter la reunión entera en un request.** Su cabecera declara TPM 70000,
  pero por dentro enruta a `meta-llama/llama-4-scout-17b-16e-instruct`, cuyo contador aparece
  saturado (26.866 de 30.000) y no baja: un request de 12,6k tokens no entra ni esperando.

### Sabido

- **El troceo es el techo de calidad del acta, y no se arregla con prompts.** Cada tramo se
  resume a ciegas, así que lo que cruza un corte se pierde: el referente de un descarte, la
  enumeración del cierre, quién es quién. Los cuatro modelos que la cuenta ve hoy están a 8000
  TPM, y la reunión entera son ~13k tokens. Con un TPM que acepte ~17k, `summarize_text()` deja
  de trocear y se ejecuta la rama de un solo request que ya está escrita. Está marcado con un
  comentario `ponytail:` en el código.

## [0.7.2] — 2026-08-19

Primera revisión de lo que devuelve el modelo nuevo, con dos sesiones reales delante.

### Corregido

- **Las tareas salían como tabla de markdown y la ventana no renderiza tablas.** `gpt-oss-120b`
  formatea distinto que `llama-3.3-70b`: en las cuatro sesiones resumidas con él aparecen tablas
  (`| Tarea | Responsable |`), encabezados `###` y separadores `---`. `md()` de la ventana cubre
  bullets, negrita, código y títulos, así que la tabla se veía como sopa de pipes con la fila de
  guiones incluida. Se le prohíbe en `SUM_SYS`: párrafos y bullets de un nivel, nada más.
- **Resúmenes que no resumían.** Con el modelo viejo el resumen pesaba entre el 2% y el 17% de la
  transcripción; con el nuevo, entre el 25% y el **95%** (una reunión de 4 minutos devolvía un
  resumen tan largo como lo que se dijo). Y las tareas se inflaban solas: siete tareas con
  responsable en una reunión donde solo se comprometió una, dueños inventados («Equipo de datos»,
  «Ingeniero (no nombrado)») y nombres propios sacados de una mención de pasada. `SUM_SYS` ahora
  exige compromiso explícito para listar una tarea, `sin dueño` cuando nadie se hizo cargo, y
  prohíbe expandir siglas (inventaba que ICM era «Interface Customer Management»).
- **Eco del micro que sobrevivía al filtro y quedaba atribuido a quien graba.** `drop_echo()`
  emparejaba las dos copias de una frase por la **distancia entre los arranques** de los segmentos
  (±`ECHO_WIN`, 6 s). Whisper devuelve segmentos de hasta medio minuto y el timestamp es el del
  principio, así que el eco de la última frase de un segmento largo del loopback aparece en el
  micro 10-15 s después de ese arranque y no entraba en la ventana: en la reunión del 19 de agosto
  se colaron ~8 de los 34 turnos del micro. Ahora `_segments()` conserva también el **fin** de
  cada segmento y el emparejamiento es por **solape de intervalos** (con `ECHO_JIT` de holgura por
  la deriva entre canales), que además no puede dar positivo hacia atrás: el eco nunca suena antes
  que el audio que lo produce. Vale solo para las sesiones nuevas: los canales por separado se
  borran al terminar de transcribir y el `.wav` que queda es la mezcla, así que lo ya transcrito
  no se puede volver a separar.

## [0.7.1] — 2026-08-18

Groq retiró el modelo de chat de un día para otro y la configuración se veía torcida.

### Corregido

- **«No se pudo resumir: model_not_found».** Groq dio de baja `llama-3.3-70b-versatile` y todas
  las sesiones terminaban en error al resumir (también fallaban el chat sobre la sesión y los
  términos relacionados de la búsqueda, que salen del mismo `chat()`). El modelo pasa a ser
  **`openai/gpt-oss-120b`**: de los que la cuenta ve hoy es el más capaz, y devuelve su
  razonamiento en un campo aparte, así que `content` llega limpio — `qwen/qwen3.6-27b` escupe su
  bloque `<think>` dentro del propio texto y se colaría en los resúmenes.
- **El interruptor de «Guardar audio» aparecía aplastado** en ⚙ Configuración, con la bolita
  saliéndose del carril. Las filas de la ventana de ajustes son flex y los items encogen por
  defecto: la descripción de dos líneas de esa fila —la más larga de todas— le robaba ancho al
  `.sw` de 46 px. Ahora el control de cada fila lleva `flex:none` (arregla de paso el segmentado
  de *Tema*) y hay separación entre el texto y el control.

### Añadido

- **Relevo automático de modelo** (`switch_chat()`): ante un **404** de Groq se pide
  `models.list()` —la única fuente fiable de qué modelos ve *esta* key— y se salta al primer
  relevo disponible de `CHAT_ALT`, reintentando la misma llamada. Vale para el resumen, el chat y
  el botón **Probar** de la configuración. El relevo dura lo que dure el proceso: no se guarda en
  disco, es un parche para que una retirada de Groq no deje la app inservible hasta el siguiente
  release; el arreglo de verdad sigue siendo editar `CHAT`. Si no queda ningún relevo, el error
  sube como antes y la sesión queda reintentable.

## [0.7.0] — 2026-08-13

Revisión sobre la sesión del 7 de agosto y cuatro cambios de uso diario.

### Corregido

- **Restos de eco cortos que seguían en la transcripción.** `drop_echo()` descartaba el eco del
  micro cuando la misma frase aparecía en el loopback y tenían **4 o más palabras**: las
  muletillas de 1-3 palabras («entonces», «no sé», «ahorita», «ya sabes») pasaban de largo porque
  con tan pocas palabras la bolsa casi nunca llegaba al 50% de solape con lo del otro canal. Ahora
  también se descartan los fragmentos cortos cuyo puñado de palabras es **subconjunto** de lo que
  el otro canal dijo cerca (±`ECHO_WIN`). El arreglo vale para las sesiones nuevas; para la que ya
  estaba grabada se aplicó a mano con el mismo criterio sobre los turnos guardados.
- **La sesión 20260807_110321 (AO REUNION 7/8/26) quedó limpia y re-resumida.** El log la marcaba
  con **78% de eco en el micro** (19% de aporte propio), y a nivel de turnos los restos eran 113
  de 228. Con el filtro de subconjunto (y una segunda pasada de ventana ±15 s para los turnos
  cortos, que por cómo se agrupan no quedan tan pegados a su fuente como los segmentos crudos) la
  sesión pasó de 457 a 311 turnos sin tocar una sola frase larga real de Eric. **El resumen se
  regeneró desde la transcripción corregida**: el anterior se había hecho sobre el texto con eco
  y repetía lo que los demás decían como si fuera de Eric.

### Añadido

- **⧉ Copiar transcripción** en la pestaña *Transcripción*: copia el diálogo completo (con la
  etiqueta de cada turno) al portapapeles. pywebview no expone portapapeles y `navigator.clipboard`
  no está garantizado en WebView2 sobre `file://`, así que el nuevo `Api.copy_text()` va por las
  API de Win32 (`OpenClipboard`/`SetClipboardData` con `CF_UNICODETEXT`) y el estado se avisa en
  la barra de status («Transcripción copiada ✓»).
- **Fecha contextual en el sidebar**: si la reunión fue hoy alcanza con la hora; si fue otro día
  de este año se muestra `DIA-MES` («07-08») y solo de otro año la fecha completa. La hora sola no
  dice nada de una reunión de hace tres días. (`whenText()` en la UI.)

### Revisado

- **Que borrar una conversación no deja rastros**: `delete_session()` ya borra el `.wav` de
  auditoría, los canales crudos `_<id>_mic.wav` / `_<id>_loop.wav` y las capturas, y saca la sesión
  del índice FTS5. Confirmado por código, sin cambios: el texto, el resumen y el audio viven todos
  bajo el mismo `<id>` y se van juntos.
- **El resumen lo hace `llama-3.3-70b-versatile`** (Groq), por tramos para no chocar con el límite
  de tokens del tier gratis; la transcripción usa `whisper-large-v3-turbo`. La medida quedó
  anotada al inicio de `takemynotes.py` junto con el resto de constantes.

## [0.6.1] — 2026-08-05

Tres cosas que aparecieron usando la app, no leyendo código.

### Corregido

- **«Gracias» que nadie dijo en las transcripciones.** Whisper alucina sobre el silencio: en cada
  ventana de 30 s sin voz devuelve una muletilla de subtítulos. En la reunión del 4 se veía el
  patrón exacto — `Gracias.` en el segundo 0, 30, 60, 90, 120 y 150 del canal del micro, mientras
  hablaban los demás. **La respuesta no lo delata**: reproducido contra Groq con 60 s de ruido
  flojo devuelve «¡Suscríbete al canal!» con `no_speech_prob` en **0** y `avg_logprob` normal, así
  que filtrar por esos campos no sirve. La única señal fiable es el audio: `has_voice()` compara el
  tramo de cada segmento contra el canal **crudo** y lo descarta si no supera `SILENCE`. Crudo y no
  el de `cleanup()`, que normaliza el pico del canal entero y por lo tanto le sube el volumen al
  ruido justo donde no hay voz. Se aplica igual al *Sincronizar con el audio*.
- **El resumen se quedaba girando en «Resumiendo… 3/3»** hasta que cambiabas de sesión. El último
  `_patch` borra el `stage` y cambia el `mtime` de `notas/`, así que el poll releía la lista —ya
  sin `stage`—, la sesión salía de los pendientes y el bucle que refresca el detalle abierto nunca
  llegaba a mirarla. Pasaba **siempre** con el último tramo, de transcripción o de resumen. Ahora
  el detalle también se relee cuando la lista dice que esa sesión ya terminó.

### Añadido

- **🎙 en la barra superior de la ventana: vuelve a mostrar el widget.** Su **✕** cierra el
  proceso del widget, que es el único sitio desde donde se graba, así que quedaba solo la ventana
  y había que reabrir la app a mano. El mutex nombrado dice si sigue vivo (`lock_held`), así que un
  segundo clic no deja dos pills flotando — y, de paso, abrir la app dos veces tampoco. El botón
  está también en el estado vacío («Sin sesiones todavía»), que es donde se lo busca la primera vez.

### Revisado

- **Cómo quedan los archivos en `notas/`**: nada que reordenar. 9 sesiones, esquema consistente
  (solo falta `turns` en las 7 anteriores a 0.6, que la UI reconstruye del texto plano), **cero
  huérfanos**: ningún canal crudo `_<id>_mic.wav` sin borrar, ningún `.tmp` suelto, ningún `.wav`
  ni captura sin su JSON. La carpeta es plana con el id como única clave a propósito — el nombre
  del archivo *es* la relación, así que no hay índice que pueda quedar desfasado entre los dos
  procesos. Queda documentado en el README, junto con los canales crudos, que estaban en el código
  pero no en el árbol de archivos.
- **Lo único que sí conviene mirar es el tamaño**: 558 MB de `.wav` de auditoría para 8 sesiones,
  y `notas/` vive dentro de OneDrive, así que **cada reunión se sincroniza a la nube** — justo lo
  que el resto del diseño evita. No se cambió ningún default (el audio es la única forma de
  auditar si la separación de hablantes acertó); se documentó, con las dos salidas que ya existen:
  apagar `keep_audio` en ⚙, o borrar el `.wav` por sesión desde **Info**.

## [0.6.0] — 2026-08-03

### Corregido

- **El reloj del widget quedaba 7 px arriba** de la fila de botones. Al pasar el estado a una
  segunda línea el reloj se dejó fijo en `y=20`, así que sin estado —lo normal— flotaba arriba con
  un hueco vacío debajo. Ahora, si no hay segunda línea, el bloque del reloj se centra con los
  botones.
- **El halo del ✕ se salía del pill** por la esquina redondeada (2 px de mordisco durante los
  140 ms que dura el feedback de apretado). El pill pasa a 400 px, el ✕ se corre hacia adentro y
  `selftest` verifica ahora la geometría: que ningún halo se pise con otro, que ninguno se salga
  del pill redondeado (`in_pill()`), y que el separador quede centrado entre los dos grupos. No
  había solapes reales, pero a ojo no se distingue y el check es geometría pura.
- **Las sesiones nuevas no aparecían en la lista.** El poll de la ventana solo refrescaba las
  sesiones que ya estaban en pantalla, así que la que el widget acababa de grabar no se veía
  hasta reabrir la ventana o escribir algo en el buscador. Ahora el poll compara el `mtime` de
  `notas/` (un `stat`, no listar y parsear todos los JSON) y relee la lista cuando cambia.
- **Casi no se podía mover el widget mientras grababa.** No era el tamaño del área de agarre:
  `_draw()` hace `delete("all")` y Tk entrega los `<B1-Motion>` al item que recibió el press, así
  que al repintar el cronómetro cada 500 ms el arrastre se cortaba en seco. Parado no se repinta
  y por eso ahí sí funcionaba. Ahora no se repinta mientras se arrastra, y el release se escucha
  en la ventana y no en un tag del canvas.

### Añadido

- **Transcripción sincronizada con el audio, estilo tl;dv.** Cada turno lleva su minuto, el clic
  en una línea salta el audio a ese momento, y la línea que suena se marca con barra azul,
  fondo teñido y `▸`, atenuando lo que todavía no se dijo. Reproductor pegado arriba del texto.
  Para eso la transcripción ahora se guarda también como `turns` `[{t, who, text}]`: Whisper
  devolvía los tiempos y `format_dialog` los tiraba.
- **Reproductor dentro de la app**, sin depender del reproductor de Windows. Se sirve `notas/`
  por HTTP desde el proceso de la ventana **con soporte de Range**, que no es opcional: sin él
  mover la aguja obliga a bajar los ~128 MB del WAV antes de oír algo. Solo loopback, puerto
  efímero y un token aleatorio en la ruta (a un puerto de 127.0.0.1 llega cualquier proceso o
  cualquier página), solo archivos que estén directamente en `notas/`, y sin CORS: el `<audio>`
  reproduce igual y nadie puede leer los bytes.
- **Sección Media** con las capturas de la sesión. Al haber servidor HTTP se sirven directo, así
  que se fue el `shot_thumbs` que las mandaba en JPEG por el puente.
- **«Sincronizar con el audio»** para las sesiones anteriores a esta versión: vuelve a transcribir
  el `.wav` guardado solo para obtener los tiempos. La transcripción de esas sesiones no los tiene
  y no hay forma de deducirlos. El `.wav` es la mezcla de los dos canales, así que si la sesión sí
  tenía hablantes separados los pierde — el aviso lo dice antes de dejar confirmar, y no toca
  notas, resumen ni chat.
- **Skeleton en la lista** para la sesión que se está transcribiendo o resumiendo: se ve el
  proceso desde la barra lateral y no parece que salió vacía.

### Cambiado

- **Rediseño del pill.** Los botones de sesión (micro, cámara, pausa) se dibujan siempre y solo
  se apagan cuando no se graba: antes aparecían y desaparecían y quedaba un hueco de 78 px que se
  veía disparejo. Se agregó asa de arrastre (los puntitos de la izquierda) y un separador entre
  los controles de la sesión y los de la app. Tocar un botón apagado dice «dale grabar primero»
  en vez de no hacer nada.
- Las burbujas por hablante se fueron: con una reunión de una hora, filas con marca de tiempo se
  leen mejor que burbujas alternadas. El «yo vs los demás» queda en el color del nombre.

## [0.5.0] — 2026-08-03

Todo esto salió de usar la app seis reuniones seguidas.

### Corregido

- **Transcripciones que parecían perdidas.** Si cerrabas la app mientras una sesión se
  transcribía, el JSON quedaba en disco marcado `pending` con el PID de un proceso muerto. La
  ventana la mostraba como *interrumpida* con su botón *Reintentar*, pero el barrido automático
  filtraba por `status == "error"` y nunca la tocaba: si no ibas a apretar el botón a mano, se
  quedaba ahí para siempre. `retryable_sessions()` ahora filtra por `_view()`, que es el único
  lugar donde se decide qué significa una sesión huérfana, así que las recoge igual que un error
  de red. Ninguna se había perdido de verdad —el audio crudo se conserva hasta que la
  transcripción sale bien—, pero había que rescatarlas a mano.
- **Al cerrar con transcripciones en curso** el widget avisa en vez de matar los hilos en
  silencio. Con lo de arriba, la próxima vez que abras la app se reintentan solas.

### Añadido

- **Cortar tu micro sin parar la reunión** (botón del micrófono, solo grabando; tachado en rojo
  cuando está cortado). Mismo mecanismo que la pausa pero de un solo canal: se sigue leyendo el
  device y no se escribe, así que ese tramo no existe ni en el `.wav` ni en la transcripción ni
  se sube a Groq. Los demás se siguen grabando por el loopback. Si el micro quedó mudo toda la
  sesión el aviso dice *"micrófono desactivado"* y no *"micrófono mudo"*.
- **Capturas de pantalla para las notas** (botón de la cámara, solo grabando). Van a
  `notas/<sesión>_shotNN.png`, el índice en el nombre del archivo para no tener que sincronizar
  nada entre los dos procesos. El widget se esconde durante la captura. Con `all_screens` toma
  todos los monitores, y se pone y se saca el contexto de DPI en el hilo: el proceso del widget
  a propósito no es DPI-aware y sin eso la captura salía escalada y borrosa.
- **Grabar la reunión siguiente mientras la anterior se transcribe.** El botón de grabar ya
  quedaba libre (el trabajo va en un hilo), pero no se veía: ahora el pill dice
  *"transcribiendo N"*. Las transcripciones se hacen **de una a la vez** (`_TX_LOCK`): dos
  juntas son ~2 GB de WAV en `float32` en memoria y el doble de tokens por minuto contra el
  mismo límite de Groq. La que espera se muestra *"En cola…"*.
- **Pestaña Info en el detalle**: la ruta real del JSON de la sesión y del `.wav`, clic para
  mostrarlas en el Explorador, **▶ Escuchar** (abre el reproductor de Windows), **Borrar audio**
  (con confirmación, y no toca el texto) y las capturas de la sesión en miniatura.
  Nada de esto usa `file://`: pywebview no sirve la ventana por `file:` sino por un bottle en
  `http://127.0.0.1:PORT` con raíz en `ui/`, así que Chromium rechaza cualquier `src="file:///…"`
  por cross-scheme (*"Media load rejected by URL safety check"*) y `notas/` ni siquiera está bajo
  esa raíz — mover la raíz tampoco serviría, porque en el `.exe` empaquetado `ui/` vive en el
  temporal de PyInstaller y `notas/` al lado del exe. El audio se abre con el reproductor del
  sistema (que además tiene avance y velocidad de verdad, y no carga 110 MB en el webview) y las
  miniaturas viajan por el puente como `data:` URI en JPEG a 320 px (~30 KB cada una, y solo al
  abrir la pestaña).
- **`keep_audio` arranca activado.** El audio es la única forma de auditar si la separación de
  hablantes acertó. Son ~110 MB por hora, así que el botón de borrar de la pestaña Info es parte
  de la misma decisión.

### Cambiado

- El widget se ensancha por los dos botones nuevos, y el estado baja a una segunda línea debajo
  del cronómetro: donde estaba antes le quedaban 47 px y *"transcribiendo 2"* no entraba.
- Nueva dependencia: **pillow**, solo por `ImageGrab` (una línea contra ~35 de ctypes + GDI +
  zlib para escribir el PNG a mano).

## [0.4.0] — 2026-07-31

### Añadido

- **Botón de pausa en el widget** (⏸/▶, solo visible grabando). Los dos canales dejan de escribir
  al WAV —se siguen leyendo, si no el device se atrasa—, el cronómetro se congela y el aviso de
  inactividad no cuenta la pausa. El widget pasa de 300 a 326 px de ancho para el hueco.
- **Reintento automático cuando vuelve la red.** Grabar nunca necesitó internet, pero si Groq no
  contestaba al detener, la sesión quedaba en rojo hasta que alguien apretara *Reintentar*. Ahora
  el widget barre cada 2 min las sesiones en error que todavía tienen el audio crudo y, si un TCP
  a `api.groq.com:443` responde, las reintenta solo. Tope de 5 intentos por sesión: no todo error
  es de red y una key inválida falla igual siempre. El botón manual sigue estando y le devuelve
  los 5 intentos.

## [0.3.0] — 2026-07-30

Primera reunión real de una hora, y salieron tres cosas de la prueba.

### Corregido

- **"Yo" decía cosas que no dijo yo.** Con parlantes (sin auriculares) el micro capta el audio
  de la PC, así que la voz de los demás llega por los **dos** canales, Whisper la transcribe dos
  veces y `format_dialog` intercalaba las dos copias como si fueran dos personas hablando por
  turnos: la sesión de prueba tenía 1005 bloques "Yo" y 1005 "Los demás", perfectamente
  alternados y con el mismo contenido. Ahora `drop_echo()` descarta la copia del micro cuando la
  misma frase aparece en el loopback casi al mismo tiempo (bolsa de palabras, ±6 s), y mide qué
  fracción del micro era eco, **en palabras y no en segmentos** (son de tamaños muy distintos).
  Si el micro venía 60% contaminado y aporta menos del 15% del texto, no hay dos hablantes que
  separar: se tira el canal del micro entero y queda **transcripción plana** con el aviso de usar
  auriculares, en vez de inventar quién dijo qué. Medido sobre la sesión de prueba: 93% de eco,
  7% de aporte, 2010 bloques → 1005, 100k → 41k chars. Contar segmentos daba 80/20 y dejaba 202
  restos de eco sueltos ("legales.", "Para rastrear") mezclados en el texto.
  Descartar el canal se lleva también lo poco propio que hubiera (59 de 7752 palabras en esa
  reunión, 5 frases entre 197 restos de 1 a 3 palabras): es un corte deliberado, anotado con la
  alternativa en el código, porque `ECHO_MIN` ya evita llegar a ese modo si hablaste de verdad.
- **No se resumía nada.** El log tenía `413 rate_limit_exceeded`: la transcripción duplicada eran
  29 845 tokens en un solo request y el tier gratis de Groq acepta 12 000 por minuto. Ahora el
  resumen va **por tramos** (`split_text` + mapa/reducción, ≤14 000 chars por request) y `chat()`
  espera de verdad cuando Groq contesta 429 (los reintentos del SDK son de ~8 s; el balde de
  tokens se rellena por minuto). La misma sesión que fallaba ahora se resume en 119 s.
- **Las notas escritas mientras transcribía se perdían.** `do_transcription` leía la sesión al
  empezar y la escribía completa minutos después, pisando lo que la ventana hubiera guardado.
  Ahora todo escribe con `_patch(sid, **campos)`, que aplica cambios sobre lo que hay en disco.

### Añadido

- **Transcripción por burbujas**: un turno por hablante, tus burbujas a la derecha en azul y las
  de los demás a la izquierda sobre pergamino, con la etiqueta arriba. Mismo vocabulario que las
  burbujas del Chat. Cuando no se pudieron separar hablantes queda texto corrido, sin color: la
  UI no finge una separación que no hay.
- **Tu nombre** en Configuración: etiqueta tus turnos con él en vez de "Yo" (`Eric:` en la
  transcripción) y **marca en ámbar cuándo los demás te mencionan**, distinto del amarillo del
  buscador. Se aplica a las sesiones nuevas; las viejas siguen con "Yo" y se muestran igual.
- **Resumen automático** al terminar de transcribir (`auto_summary`), sin esperar un clic. Si
  falla queda `sum_error` y el botón **▶ Generar resumen** para reintentar.
- **Progreso real** en vez de skeleton para lo que se está generando: la sesión lleva un campo
  `stage` ("Transcribiendo… 3/12", "Resumiendo… 2/5") que el widget escribe por cada trozo de
  audio y por cada tramo de resumen, y la ventana muestra con spinner en la lista y en el panel.
  El skeleton queda para lo que ya existe y se está leyendo del disco.
- **Confirmación propia al eliminar**, en vez de `confirm()`: el diálogo nativo depende de que
  WebView2 permita script dialogs, bloquea el hilo de la vista y se ve como una alerta del
  sistema en una app con estética Apple. El modal reusa `.mask`/`.modal` de Configuración, tiene
  el botón de borrar en rojo, se cierra con Escape o clic fuera, y el callback vive en una
  variable y no en el DOM: no puede quedar apuntando a otra sesión. Con check que verifica que
  `del()` no llama al backend hasta confirmar.
- **Chat sobre sesiones largas**: `ask()` tenía el mismo 413. Si la transcripción no cabe se
  pregunta tramo por tramo y se unifican las respuestas que trajeron algo.
- Checks nuevos en `--selftest`: `drop_echo` (eco parcial, canales gemelos, micro sucio pero con
  algo propio, interjecciones cortas), `split_text`, `n_chunks`, `speaker_label`, `_patch` (no
  pisa al otro proceso, no resucita borradas) y `_stale`/`_view` con un resumen huérfano.
- Checks nuevos en `check_ui.js`: burbujas por hablante (tus turnos, los ajenos, sin etiquetas y
  sin inyectar HTML), marcado de menciones en una sola pasada, y el flujo de eliminar.

## [0.2.0] — 2026-07-24

Repositorio inicializado y una pasada completa de mejoras sobre la versión que ya funcionaba.
Diez commits, +1056 / −240 líneas.

### Añadido

- **Aviso de inactividad**: a los 5 min sin voz en ningún canal el widget se pone ámbar y ofrece
  **¿seguir?**; 2 min más sin respuesta detiene la grabación, la transcribe y la marca
  "detenida por inactividad". La decisión vive en `idle_state()`, función pura y testeable.
- **Notas escritas desde el widget** (**✎**): bloc aparte cuyo texto entra en la sesión que se
  está grabando (o en la próxima).
- **Cerrar la app desde el widget** (**✕**). Grabando pregunta primero y guarda la sesión de
  forma síncrona antes de salir: queda para reintentar desde la ventana, no se descarta.
- **Feedback de presionado** en todos los botones del widget (halo, color y el botón de grabar
  se encoge 140 ms).
- **Tema claro / oscuro / auto**, en el widget y en la ventana. `auto` lo resuelve Python
  leyendo `AppsUseLightTheme` del registro, así los dos procesos nunca discrepan. El widget
  relee el tema por `mtime` de `settings.json`: cambiarlo en la ventana se ve sin reiniciarlo.
- **Favoritos** (★) y filtro para ver solo esos.
- **Menú ⋯ por sesión**: favorito, renombrar y eliminar sin entrar al detalle.
- **Sugerencias por significado en el buscador**: cuando la búsqueda literal trae poco, se le
  piden términos relacionados al modelo de chat y se corre otra consulta FTS5 con `OR`
  ("engaño" → estafa, fraude, timo). Aparecen aparte, bajo *Relacionados con: …*.
- **El buscador salta al fragmento** dentro de la transcripción, no solo a la sesión.
- **Markdown renderizado** en Resumen y Chat (títulos, negrita, itálica, `código`, listas),
  siempre sobre texto ya escapado.
- **Notas de la sesión**: botón **+ Nueva nota** (entrada con la hora), textarea con ancho de
  lectura y un **•** en la pestaña cuando hay contenido.
- **Cancelar** en Configuración, que deshace todo lo tocado incluido el tema en vivo.
- **Log rotativo** en `notas/takemynotes-{widget,window}.log` (1 MB × 3, uno por proceso) con
  los tracebacks completos que la UI resume en una línea, y un handler de crash: el `.exe` es
  `--noconsole` y antes moría en silencio.
- **Checks ejecutables**: `python takemynotes.py --selftest` (audio, diálogo, `idle_state`,
  DPAPI, settings por disco, escritura atómica, mutex, `_stale`, índice FTS5 y la capa `Api`
  completa) y `node check_ui.js` (resaltado, markdown, título editable, `+ Nueva nota`).
- **`.gitignore`** y **`CHANGELOG.md`**.

### Cambiado

- **Ventana única por mutex nombrado de Windows** en vez de un lockfile con PID: lo libera el
  SO al morir el proceso, así que dejan de existir locks huérfanos.
- **La API key se guarda cifrada** (`key_enc`, DPAPI vía `ctypes`) y migra sola desde el
  `settings.json` en claro. Se descartó `keyring` por ser una dependencia y un `hiddenimport`
  más en PyInstaller.
- **`build.bat` usa un `.venv` propio** y no el Python global. Efecto medido: el `.exe` bajó de
  **66 MB a 33 MB**, porque ya no arrastra el `site-packages` del sistema.
- **`requirements.txt` pineado** a lo probado (soundcard 0.4.6, numpy 2.2.6, groq 0.37.1,
  pywebview 6.2.1).
- **Errores de Groq** a 500 caracteres en la sesión (antes 200, que cortaba justo lo útil) y
  traceback completo al log.
- El botón 🗑 rojo del detalle se reemplazó por **☆** y **⋯**, consistentes con la lista.
- `poll()` refresca solo las sesiones en curso en vez de releer la lista entera cada 2,5 s, y
  `select()` pinta un skeleton antes de esperar al backend.

### Corregido

- **Guardar el `.wav` de respaldo ya no puede tumbar una transcripción buena**: va en su propio
  `try` y, si falla, la sesión queda `done` con un aviso en vez de en error.
- **Sesiones zombi**: una `pending` cuyo proceso murió se muestra como error reintentable en vez
  de quedarse "Transcribiendo…" para siempre. El pid lo estampa `do_transcription`, que es quien
  realmente hace el trabajo.
- **El resaltado del buscador partía las palabras acentuadas** (`\w` de JS es ASCII: "reunión"
  se marcaba como `Reuni` + `n`). Ahora tokeniza con `\p{L}\p{N}`.
- **Los tokens del buscador entraban sin escapar en un `RegExp`.**
- **Cualquier `.json` ajeno en `notas/` tumbaba la lista de sesiones** con un `TypeError`.
- **DPI**: el proceso de la ventana no pedía DPI awareness, así que con Windows al 150% el
  viewport CSS quedaba en 693 px y la UI se cortaba a la derecha.
- **Los botones ⚙ y ⤡ del widget no hacían nada** si la ventana ya estaba abierta detrás:
  `launch_window()` salía temprano. Ahora la trae al frente.
- **`focus_window` elegía la ventana solo por título**, y una ventana del Explorador abierta en
  esta carpeta se llama igual: se llevaba el foco. Ahora exige además la clase WinForms.
- **El widget se quedaba en "procesando…" para siempre** tras terminar: el hilo de fondo limpiaba
  la etiqueta y nadie repintaba (Tk solo dibuja desde su hilo; ahora lo hace el tick).
- **El feedback de presionado rompía los clics**: redibujar el canvas en el `<ButtonPress>` le
  quitaba el `<ButtonRelease>` al item recreado.
- **`save_settings` no escribía atómico** siendo ya canal entre procesos: una lectura a medias
  dejaba al widget con el tema viejo o marcaba una sesión como "falta la API key".
- **El título generado por el modelo no pasaba por ninguna normalización** (un salto de línea
  entraba tal cual al JSON). `clean_name()` es ahora la única regla, la escriba quien la escriba.
- El título editable ya no acepta Enter ni pega saltos de línea, y corta a 80 como el backend.
- `load_settings` cerraba el `.env` (fuga de descriptor) y `save_settings` su archivo.

### Eliminado

- `TakeMyNotes.spec` del repo: PyInstaller lo regenera y el versionado no tenía `--add-data
  ui;ui` ni `--collect-all webview`, o sea producía un `.exe` que no encontraba `ui/index.html`.
  `build.bat` es la única fuente de verdad de los flags.
- Los `*.log` sueltos en la raíz (eran de un lanzamiento manual con redirección).
- El caché de sinónimos en disco: pasó a memoria, y con eso `notas/` volvió a contener solo
  sesiones.

### Seguridad

- La API key ya no queda en claro en `settings.json` (ver *Cambiado*).
- **Sección Privacidad en el README**: el audio y la transcripción se envían a Groq; si grabás a
  otras personas, avisales.
- `.env` y `notas/` quedaron fuera del repositorio desde el primer commit.

---

## Cómo se hizo

Ocho fases, un commit cada una, más una pasada final de limpieza:

| Commit | Qué |
|--------|-----|
| `f0222dd` | Commit inicial (repo + `.gitignore` con la key y las notas fuera) |
| `e2d30f3` | Fase 1 — correctitud del backend: `.wav`, sesiones zombi, mutex, globals |
| `0c5d693` | Fase 2 — aviso de inactividad |
| `804ef30` | Fase 3 — logging rotativo y limpieza |
| `d0ab662` | Fase 4 — key cifrada con DPAPI + privacidad |
| `6afa8b3` | Fase 5 — UI: resaltado, título, `poll()`, skeleton |
| `780c660` | Fase 6 — empaquetado: `.venv`, versiones pineadas, fuera el `.spec` |
| `0b95de3` | Fase 7 — checks (`--selftest` hasta la capa `Api`, `check_ui.js`) |
| `0f89457` | Fase 8 — feedback de uso: botones del widget, temas, favoritos, markdown, DPI, buscador semántico |
| `379947e` | Limpieza tras cuatro revisiones (reuso, simplificación, eficiencia, altura) |

Tres bugs no aparecieron leyendo código sino **corriendo la app**: el widget colgado en
"procesando…", el recorte por DPI y los clics que se perdían con el feedback de presionado.
Otros tres los encontraron los checks nuevos al escribirlos: el `\w` ASCII del resaltado, el
`.json` ajeno que tumbaba la lista, y el `settings.json` no atómico.
