# Interfaz: widget y ventana

## Widget (`class Widget`, Tkinter)

Un notch estilo [codenotch](https://github.com/vinzdg/codenotch) pegado al centro de un borde de
la pantalla, siempre encima. El borde se elige en Configuración → «Posición del widget» (`edge` en
`settings.json`): arriba, abajo (sobre la barra de tareas), izquierda o derecha. En los lados va en
columna, con reloj, barritas y estado apilados. Detalle y por qué de cada decisión en
[notch-2026-09-30.md](notch-2026-09-30.md).

- **Plegado:** una pestañita de 79 × 10 con un punto de estado (rojo grabando, ámbar aviso, nada
  parado).
- **Desplegado:** 400 × 54, plano del lado del borde, esquinas de 20 px del otro y orejas cóncavas
  donde toca el borde.
- **Se abre** al acercar el mouse, o solo durante 2,5 s cuando hay algo nuevo (`_peek`).
- **No se pliega** mientras `_busy()`: aviso, sin señal, mensaje, pausa, transcribiendo o bloc
  abierto. Clic derecho lo deja fijo abierto.
- **El asa** lo arrastra solo a lo largo de su borde. Tooltips y bloc de notas salen del lado de
  adentro de la pantalla.

La distribución es fija: los botones de sesión se dibujan siempre y se apagan cuando no se graba
(el notch no cambia de forma). Las coordenadas de cada control están en la clase (`MIC`, `REC`,
`LVL`…) medidas **a lo largo** del notch; `_at` / `_pt` las llevan al borde que toque, y
`selftest` comprueba que ningún halo se pise con otro ni se salga del cuerpo.

| Control | Qué hace |
|---------|----------|
| Reloj | Tiempo grabado (sin contar pausas) |
| Barritas ▮▮ (`LVL`) | Nivel del último bloque del micro y de la PC; verdes cuando hay voz. Solo grabando |
| Segunda línea | Estado, por prioridad: «¿seguir grabando?» → «en pausa» → mensaje efímero → **«sin audio de la PC» / «micrófono sin señal»** en ámbar (tras 60 s sin voz desde el arranque) → «transcribiendo N» |
| 🎤 📷 ⏸ | Cortar micro, captura de pantalla, pausar. Sin grabar dicen «dale grabar primero» |
| ✎ ⚙ ⤡ | Bloc de notas, Configuración, abrir la ventana |
| ● / ■ | Grabar / detener. Global: **Ctrl+Shift+R** |
| ✕ | Cerrar (pregunta si graba o transcribe) |

**Tooltips** (`_tip_at`, `_set_tip`, `_show_tip`): aparecen a los 600 ms. Se calculan por
coordenadas del mouse y no con `<Enter>` en los items, porque grabando `_draw()` borra y recrea
todo cada 500 ms y el tooltip parpadearía. Los textos están en `TIPS`.

**Atajo global** (`_hotkey`): `RegisterHotKey` manda `WM_HOTKEY` a la cola del hilo que lo
registró, y el `mainloop` de Tk la descartaría. Por eso corre en un hilo propio con su bucle de
mensajes, que solo levanta `_hot`; `_loop` lo atiende en el hilo de Tk (hasta 500 ms de demora).
Si otra app ya tiene el atajo, no hay atajo y queda un warning en el log.

**Cerrar desde afuera**: `root.protocol("WM_DELETE_WINDOW")` lleva a `_quit`, así el
`WM_CLOSE` de `--quit` (menú Inicio, instalador) pregunta igual que el ✕. `_quit` destruye la
ventana con `after_idle`: hacerlo dentro del clic del ✕ corrompía la memoria de Tcl y el proceso
moría al cerrar.

## Ventana (`ui/index.html`, pywebview)

### Barra superior

⚙ Configuración · 🎙 volver a mostrar el widget · ☑ **Pendientes**.

### Lista de sesiones

- Agrupada por fecha: Hoy, Ayer, Esta semana, y por mes (`groupOf`).
- Al buscar: orden por relevancia, contador de resultados y ✕ para limpiar.
- Punto ámbar en las sesiones **sin acta** (falló el resumen): campo `nosum` de `Api._meta`.
- Menú ⋯: favorito, renombrar, eliminar.

### Pestañas

| # | Pestaña | Contenido |
|---|---------|-----------|
| 1 | Resumen | El acta con acciones tildables; ⧉ Copiar acta; ↻ Rehacer. Mientras resume, avisa que la transcripción ya se puede leer |
| 2 | Minuta | Vacía hasta que se pide: ▶ Generar minuta; después ⧉ Copiar y ↻ Rehacer |
| 3 | Transcripción | Reproductor fijo arriba; filas por turno con su minuto; clic = salta el audio. Las notas con hora aparecen en su minuto. Clic en la etiqueta de un hablante = renombrarlo en la sesión |
| 4 | Media | Capturas de pantalla |
| 5 | Notas | «+ Nueva nota» agrega `— hh:mm —`; se guarda sola |
| 6 | Chat | Preguntas; el input se apaga mientras responde |
| 7 | Info | Rutas en disco, ⤓ Exportar .md, escuchar o borrar el audio |

### Teclado

| Tecla | Acción |
|-------|--------|
| `Ctrl+F` | Ir al buscador |
| `Esc` | Cierra el modal abierto; en el buscador, lo vacía; si no, cierra el menú |
| `↑` / `↓` | Sesión anterior / siguiente |
| `Ctrl+1` … `Ctrl+7` | Pestaña por número |
| `Supr` | Eliminar la sesión (con confirmación) |
| `Espacio` | Reproducir / pausar el audio de la transcripción |

Las teclas sueltas no actúan si el foco está en un campo, un botón o el reproductor.

### Pendientes

Todas las acciones sin tildar de todas las sesiones, agrupadas por responsable, cada una con el
enlace a su sesión. Tildarla acá la marca en el acta de su sesión.

### Notas con hora (`noteMarks`)

Una entrada `— 10:32 —` (o `— 10:32:15 —`) de las notas se convierte en segundo de la sesión
restando la hora de inicio, que es el id. Si cae dentro de la reunión, se intercala en la
transcripción como una fila «Nota», clickeable. No se guarda nada nuevo. Una pausa en la
grabación corre las notas posteriores lo que duró la pausa.

### Renombrar un hablante (`renameWho`, `Api.rename_speaker`)

Cambia la etiqueta en `turns` y en los inicios de párrafo de `transcript`, nunca dentro de lo que
se dijo. El próximo resumen ya usa el nombre; el acta existente no cambia hasta rehacerla.

### Detalles que evitan bugs

- **El poll no repinta mientras escribes** (`typing`, `freshCur`): con el foco en las notas, el
  chat o el título, trae los datos nuevos a `cur` pero no reemplaza el DOM. Las notas que todavía
  no se guardaron (hay 600 ms de espera) se conservan.
- **El poll no se solapa** consigo mismo (`polling`).
- **Nada irreversible sin confirmación**: el modal propio (`askConfirm`) y no `confirm()`.
  `askPrompt` es el mismo modal con un campo de texto.
- **Si eliminar falla**, la sesión sigue seleccionada y aparece el aviso.
- **Toast** abajo a la derecha para confirmaciones y errores (`toast`).

### Configuración

Dos bloques:

- **General**: guardar audio, identificar hablantes, tema, tu nombre y **Probar micro y audio de
  la PC** (`Api.test_audio`: 5 s de cada canal, sin guardar nada; muestra el nivel y si hubo voz).
- **Inteligencia artificial**: key de Groq, key de Gemini (con una línea de privacidad visible y
  el detalle plegado en «¿Por qué opcional?»), y modelo para los resúmenes.

Se abre sola en el primer arranque, cuando falta la key de Groq.

### Accesibilidad

Foco visible en botones, interruptores y checkboxes; `aria-label` en los botones de glifo;
interruptores con `role="switch"` operables con Espacio o Enter; pestañas con `role="tab"`;
contraste del gris secundario de 4.6:1.
