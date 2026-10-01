# Concurrencia: dos procesos, los mismos JSON

TakeMyNotes son **dos procesos** que no se hablan entre sí salvo por archivos en `notas/`:

- el **widget** (Tkinter), siempre vivo: graba, transcribe, resume y reintenta solo;
- la **ventana** (pywebview), a demanda: lee, edita, resume a mano, chatea, borra.

Los dos escriben el mismo `notas/<id>.json`. Todo lo de este documento existe para que eso no
pierda datos en Windows.

## Las cuatro piezas

### 1. Escritura atómica que aguanta lectores — `store_session`, `_aguanta`

Se escribe a `<id>.json.tmp` y se reemplaza con `os.replace`. En Windows ese rename **falla** con
`PermissionError [WinError 5]` si otro proceso tiene el destino abierto: Python abre sin
`FILE_SHARE_DELETE`, y la ventana relee los JSON en cada poll (y OneDrive los bloquea al subirlos).
Esas lecturas duran milisegundos, así que `_aguanta(op, …)` reintenta 10 veces con 20 ms de espera.
Lo usan `store_session`, `save_settings` y el borrado.

### 2. Un mutex entre procesos — `_patch_lock`

`Local\TakeMyNotes.patch`, un mutex **nombrado** de Windows (`CreateMutexW` +
`WaitForSingleObject`). Un `threading.Lock` solo vale dentro de un proceso; este vale entre el
widget y la ventana, y también entre hilos del mismo proceso (el dueño de un mutex de Windows es el
hilo). Si alguien lo retiene más de 5 s se sigue sin él y queda un warning en el log: una carrera
improbable es mejor que la app colgada. Si el dueño muere con el mutex tomado, Windows lo entrega
como `WAIT_ABANDONED`, que se trata como adquirido.

### 3. Cambios sobre lo que hay en disco — `_patch(sid, fn=None, **campos)`

**Toda** escritura de una sesión existente pasa por acá. Dentro del mutex:

1. si el JSON ya no existe, devuelve `None` (no resucita sesiones borradas);
2. lo lee **del disco**, no de una copia en memoria;
3. si hay `fn`, la llama con la sesión. Si `fn` devuelve `False`, **aborta sin escribir** y
   devuelve `None`;
4. aplica `campos` (un valor `None` borra el campo);
5. escribe con `store_session`.

Los casos de `fn`: alternar favorito, sumar al chat, alternar una acción hecha, renombrar un
hablante, y `claim`.

Antes, `save_notes`, `toggle_fav`, `rename_session` y `retry_transcription` hacían
`load_session` → modificar → `store_session` sin lock: si el widget escribía el resultado de una
transcripción en el medio, la ventana lo pisaba con su copia vieja.

### 4. Comprobar y reclamar de una vez — `claim(sid, manual=False)`

Pasa una sesión a `pending` con este proceso como dueño (`pid`), **solo si nadie la está
transcribiendo ya**. La comprobación va dentro del mismo `_patch` que la escritura. Con la
comprobación afuera, el reintento automático del widget y un **Reintentar** de la ventana en el
mismo segundo arrancaban los dos: el audio se subía dos veces y el último en terminar pisaba al
otro.

- Nunca reclama una sesión `pending` cuyo proceso sigue vivo (`_stale`).
- El automático (`manual=False`) además exige que siga en error (vista por `_view`, así una
  `pending` huérfana cuenta como error) y que queden intentos (`retries < AUTO_RETRIES`).
- El manual devuelve todos los intentos (`retries = 0`).

## Borrar — `remove_session`, `Api.delete_session`

`remove_session` borra el JSON **bajo el mismo mutex** y con `_aguanta`. Afuera del mutex, un
`_patch` que ya había comprobado que la sesión existía la volvía a escribir justo después del
borrado.

`delete_session` borra primero el JSON, que **es** la sesión. Si no pudo, devuelve
`{"ok": False, "msg": …}` sin tocar nada más, y la ventana deja la sesión seleccionada y lo avisa.
Antes el error se tragaba y la UI la daba por borrada. El resto (`.wav`, `.md`, canales, capturas,
fila del índice) se borra a mejor esfuerzo: si la transcripción tiene un canal abierto, ese borrado
falla, pero el `_patch` final de la transcripción ve que la sesión ya no está y limpia los canales.

## Transcribir sin perder nada — final de `_do_transcription`

El orden importa:

1. `_patch` con el resultado (transcripción, estado, avisos).
   - Si **falla**: segundo intento marcando `status="error"`; los canales crudos siguen en disco,
     así que la sesión se puede reintentar. Se sale.
   - Si devuelve `None`: la borraron mientras se transcribía.
2. Si la sesión sigue y se guarda audio, se escribe el `.wav` mezclado.
3. Se borran los canales crudos (en éxito, y también si la sesión fue borrada).
4. Si la sesión sigue, arranca el resumen.

Antes los canales se borraban **antes** del patch: si el patch fallaba, la sesión quedaba `pending`
con el pid vivo para siempre, sin transcripción y sin nada que reintentar.

## Una transcripción a la vez — `do_transcription`

`_TX_LOCK` (de proceso) hace que el widget transcriba de a una: la que espera se ve "En cola…".
Entre procesos, lo que evita dos transcripciones de la misma sesión es `claim`.

## Sesiones huérfanas — `_stale`, `_view`

Cada sesión en curso guarda el `pid` del proceso que la trabaja. Si ese proceso murió (se cerró la
app), `_view` la presenta como error reintentable, o le quita el "Resumiendo…" que se quedaría
girando. Windows recicla PIDs, así que un PID reusado se ve vivo: en el peor caso la sesión se ve
"transcribiendo…" de más, nunca se pierde.

## Configuración — `save_settings`, `load_settings`

`settings.json` se escribe igual de atómico. El widget lo relee cuando cambia su `mtime` (para el
tema y el borde del notch, `edge`: si cambió, `_place()` lo muda sin reiniciar). Si DPAPI falla, la key se guarda en claro y se marca `"_plain": true`, para que leer la
configuración no intente migrarla (y reescribir el archivo) en cada lectura.

## Cómo se prueba

En `selftest()`:

- un hilo abre el JSON 100 ms mientras el principal lo reescribe (`_aguanta`);
- 200 `_patch` en un hilo contra 50 `save_notes` en otro: los dos cambios llegan;
- cuatro `claim` simultáneos (dos automáticos, dos manuales): gana exactamente uno;
- `claim` automático sin intentos restantes no reclama; el manual sí y devuelve los intentos;
- `_patch` con `fn` que devuelve `False` no escribe;
- borrar con el JSON abierto por otro hilo funciona, y después ni `claim` ni `_patch` la resucitan;
- transcripción de punta a punta donde la sesión se borra en medio: no quedan canales ni `.wav`.

En `check_ui.js`: si el backend no pudo borrar, la sesión sigue seleccionada y aparece el aviso.
