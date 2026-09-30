# Empaquetado e instalador

## Generarlos

```bat
build.bat
```

1. Crea `.venv` si no existe e instala `requirements.txt` + PyInstaller 6.21.0.
2. Genera `dist\TakeMyNotes.exe` (un solo archivo, sin consola).
3. Si encuentra **Inno Setup 6** (`ISCC.exe` en `Program Files (x86)`, `Program Files` o
   `%LOCALAPPDATA%\Programs`), compila `installer.iss` y deja
   `dist\TakeMyNotes-Setup-<versión>.exe`. Si no lo encuentra, lo dice y termina bien.

Para tener el instalador:

```bat
winget install JRSoftware.InnoSetup
build.bat
```

## Qué hace el instalador (`installer.iss`)

- Instala **por usuario** en `%LOCALAPPDATA%\Programs\TakeMyNotes`, **sin pedir admin**
  (`PrivilegesRequired=lowest`). No es por comodidad: la app guarda `notas\` y `settings.json`
  junto al `.exe` (`BASE` en `takemynotes.py`), y en `Program Files` no podría escribirlos.
- Crea la carpeta **TakeMyNotes** en el menú Inicio:

| Acceso | Ejecuta | Qué hace |
|--------|---------|----------|
| TakeMyNotes | `TakeMyNotes.exe` | Muestra el widget (si ya está abierto, no abre otro) |
| Sesiones | `--window` | Abre la ventana; si ya está abierta, la trae al frente |
| Configuración | `--settings` | Abre la ventana directo en ⚙ |
| Cerrar TakeMyNotes | `--quit` | Cierra widget y ventana; pregunta si estás grabando |
| Carpeta de notas | `{app}\notas` | La abre en el Explorador |
| Desinstalar TakeMyNotes | desinstalador | Quita la app |

- Tareas opcionales: acceso en el **escritorio** y **abrir el widget al iniciar Windows** (acceso en
  la carpeta Inicio del usuario).
- Al terminar ofrece abrir la app.
- **Al actualizar**, `PrepareToInstall` ejecuta el `.exe` instalado con `--quit` antes de
  reemplazarlo. **Al desinstalar**, `[UninstallRun]` hace lo mismo antes de borrarlo. Si no, el
  `.exe` quedaría en uso. `CloseApplications=no` porque el Restart Manager de Windows no sabe
  preguntarle al widget si está grabando.
- **El desinstalador no borra tus datos**: `notas\` (marcada `uninsneveruninstall`) y
  `settings.json` quedan en `%LOCALAPPDATA%\Programs\TakeMyNotes`.

Mantenimiento:

- **`AppId` no se cambia nunca**: identifica la app entre versiones; cambiarlo instalaría otra al
  lado.
- **`AppVersion`** se sube a mano en `installer.iss`, junto con la entrada del CHANGELOG.

## Argumentos del `.exe`

| Argumento | Qué hace | Implementación |
|-----------|----------|----------------|
| (ninguno) | Abre el widget; si ya hay uno (mutex `Local\TakeMyNotes.widget`), no hace nada | final de `takemynotes.py` |
| `--window` | Abre la ventana; si ya hay una, la trae al frente | `window_main`, `focus_window` |
| `--settings` | Abre la ventana en Configuración | `launch_window("settings")` → archivo `.action` |
| `--quit` | Cierra widget y ventana y espera (hasta 15 s) a que terminen | `quit_all` |
| `--selftest` | Corre los checks | `selftest` |

### Cómo cierra `--quit`

`quit_all()` busca las ventanas de la app en otros procesos (`_app_windows`: título
«TakeMyNotes» y clase `WindowsForms*` para la ventana o `TkTopLevel` para el widget), les manda
`WM_CLOSE` y espera a que se liberen los mutex de los dos procesos, que Windows suelta al morir cada
uno. `WM_CLOSE` y no matar el proceso: el widget lo recibe por su protocolo `WM_DELETE_WINDOW` y
pasa por `_quit`, que pregunta si estás grabando o si quedan transcripciones. Probado en vivo:
cierra el widget en menos de un segundo.

## Dónde quedan los datos

| Cómo corre | Carpeta de datos |
|------------|------------------|
| `run.bat` / `python takemynotes.py` | Junto a `takemynotes.py` |
| `.exe` suelto | Junto al `.exe` |
| Instalado | `%LOCALAPPDATA%\Programs\TakeMyNotes` |

Si esa carpeta está dentro de OneDrive, cada reunión (y su audio) se sube a la nube: ver
Privacidad en el README. Mover los datos a `%LOCALAPPDATA%` independientemente de dónde esté el
`.exe` es el ítem 6.6 del plan, todavía pendiente.
