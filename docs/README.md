# Documentación de TakeMyNotes

El [README](../README.md) explica qué hace la app y cómo usarla. Estos documentos explican **cómo
está hecha por dentro** y **por qué**, para quien vaya a tocar el código.

| Documento | De qué trata |
|-----------|--------------|
| [revision-2026-09-30.md](revision-2026-09-30.md) | Qué se hizo a partir de la revisión del 30/09: cada ítem del plan, dónde quedó en el código, cómo se prueba y qué quedó pendiente |
| [concurrencia.md](concurrencia.md) | Dos procesos sobre los mismos JSON: mutex, `_patch`, `claim`, borrado y reintentos de disco |
| [pipeline.md](pipeline.md) | De la grabación al acta: audio por trozos, Whisper, eco, resumen por tramos, título, Minuta y acciones |
| [interfaz.md](interfaz.md) | Widget y ventana: controles, pestañas, atajos de teclado, vista Pendientes |
| [instalador.md](instalador.md) | `build.bat`, el instalador de Inno Setup, el menú Inicio y los argumentos del `.exe` |
| [pruebas.md](pruebas.md) | Qué cubre `--selftest` y qué cubre `check_ui.js`, y lo que hay que probar a mano |
| [plan-2026-09-30.md](plan-2026-09-30.md) | El plan original de la revisión, tal como se escribió (registro histórico) |

Regla del proyecto, que vale para cualquier cambio: **cada arreglo deja un `assert`** en
`selftest()` o en `check_ui.js`. Sin check no está terminado.
