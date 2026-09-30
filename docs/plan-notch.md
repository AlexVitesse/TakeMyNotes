# Plan: el widget como notch (estilo codenotch)

Estado de partida: `4f79e41` (0.8.0), `--selftest` en verde. Objetivo: que el widget se vea y se
comporte como el notch de [vinzdg/codenotch](https://github.com/vinzdg/codenotch): una pastilla
pegada al borde **superior** de la pantalla, plana arriba, esquinas inferiores redondeadas y
esquinas cóncavas («orejas») donde toca el borde; descansa **plegada** como una pestañita y se
**despliega** cuando el mouse se acerca o cuando hay algo nuevo que mostrar.

Decisiones ya tomadas:

- Plegado como codenotch. Se abre solo ~2,5 s cuando pasa algo (arrancó/paró, mensaje efímero,
  «¿seguir grabando?», sin señal) y se queda abierto mientras haya algo que el usuario deba ver.
- Los colores respetan el tema: oscuro `#000` con borde `#2e2e2e` (codenotch), claro `#f5f5f7`
  con borde `#d2d2d7`. Sigue el ajuste Auto / Claro / Oscuro de `settings.json`.
- **Mismo stack.** Tkinter con `overrideredirect` + `-transparentcolor` y polígonos `smooth=True`,
  como hoy. Sin dependencias nuevas. Los píxeles color-llave son click-through en Windows
  (ventana *layered* con color key), así que la ventana queda **siempre del tamaño abierto** y
  solo cambia lo dibujado: no se redimensiona nada al plegar.

## Referencia (codenotch, `windows/codenotch/ui/notch.html`)

| Qué | Valor |
|-----|-------|
| Ventana (Tauri) | `transparent, decorations:false, alwaysOnTop, skipTaskbar, shadow:false, focusable:false` |
| Pill borde superior | `border-radius: 0 0 20px 20px; border: 1px solid var(--edge); border-top: none` |
| Plegado (`#rest`) | `79 × 10 px`, `border-radius: 0 0 6px 6px`, borde 1 px sin borde superior |
| Transición | `.36s cubic-bezier(.32,.72,.24,1)` |
| Paleta oscura | `--pill:#000 --edge:#2e2e2e --ink:#fff` |
| Paleta clara | `--pill:#f5f5f7 --edge:#d2d2d7 --ink:#1d1d1f` |
| Fuente | `"Segoe UI", system-ui, sans-serif` |
| Comportamiento | Se despliega al acercar el puntero; se pliega al irse; clic derecho = «Keep open» |

Las «orejas» cóncavas se ven en `docs/design/frame-124-hover-tooltip.png` del repo: el cuerpo se
funde con el borde de la pantalla con una curva hacia afuera a cada lado.

---

## Cambios

Todo en `takemynotes.py`, `class Widget` (`:1679-2198`), salvo la sección de docs.
Las líneas citadas son las de `4f79e41`.

### 1. Paleta y constantes (`:1636-1646`, `:1683-1694`)

- `DARK`: `"pill": "#000000"`, `"edge": "#2e2e2e"`. `LIGHT`: `"edge": "#d2d2d7"`. El resto igual.
- `R` pasa de 24 a **20**. Solo se redondean las esquinas **inferiores**; arriba queda plano.
- Constantes nuevas de clase:

  ```python
  EAR = 12            # radio de la oreja cóncava a cada lado, fuera del cuerpo
  TAB_W, TAB_H = 79, 10   # pestañita plegada (codenotch: #rest)
  ANIM = 360          # ms del plegado/desplegado
  PEEK = 2500         # ms que queda abierto solo cuando hay algo nuevo
  FOLD_DELAY = 700    # ms sin el mouse encima antes de plegarse
  ```

- `in_pill(x, y, w, h, r)` (`:1649`): el arco solo aplica en las esquinas de **abajo**
  (`y > y2 - r`); arriba es rectángulo hasta `y1 = 0`.

### 2. Ventana arriba al centro, con lugar para las orejas (`__init__`, `:1696-1743`)

- Geometría: ancho `W + 2*EAR`, alto `H`, en `+{(sw - W) // 2 - EAR}+0`.
- Canvas de `W + 2*EAR` de ancho con `scrollregion=(-EAR, 0, W + EAR, H)`. Con esto las
  coordenadas de los controles (`GRIP`…`QUIT`) **no cambian**: el canvas muestra desde `x=-EAR`
  y las orejas se dibujan en `x<0` y `x>W`. Dejar un comentario que lo diga.
- `_motion` (`:1776`): pasar `self.c.canvasx(e.x)` a `_tip_at`. Los `tag_bind` de clic no
  dependen de coordenadas.
- `self.c.bind("<Enter>", lambda e: self._unfold())`: entrar con el mouse despliega al instante.
- Clic derecho (`<Button-3>`) sobre `pill` y `drag`: `self._pinned = not self._pinned; self._draw()`.
  Es el «Keep open» de codenotch. Sin menú contextual.
- Estado nuevo:

  ```python
  self._open = 0.0        # 0 plegado … 1 abierto (fracción, para la animación)
  self._anim = None       # job de after() de la animación en curso
  self._fold_job = None   # job de after() que va a plegar
  self._pinned = False    # clic derecho: no se pliega
  ```

- Arranca plegado y hace `_peek()` para que se vea que arrancó. Arrancar el sondeo `_hover()`.

### 3. Forma: cuerpo, orejas y pestañita (`_draw`, `:1926-2018`)

Reemplazar `self._round(2, 2, self.W - 2, self.H - 2, 24, ...)` por `self._body()`, que dibuja
según `self._open` y devuelve `True` si hay que dibujar los controles:

- **`_open == 0` (plegado):** rectángulo `TAB_W × TAB_H` centrado en `W/2`, pegado a `y=0`,
  esquinas inferiores radio 6, `fill=pill, outline=edge`, tag `pill`. En el centro un punto de
  5 px con el color de `dot` (rojo grabando, ámbar aviso, ninguno si está parado). Devuelve
  `False`: **no se dibuja ningún control**.
- **`0 < _open < 1` (animando):** cuerpo interpolado con *ease-out* `t = 1 - (1 - _open) ** 3`:
  ancho `TAB_W → W`, alto `TAB_H → H`, radio `6 → R`, centrado. Sin controles. Devuelve `False`.
- **`_open == 1` (abierto):** polígono plano arriba y radio `R` abajo, `outline=edge`. Para que
  no se vea borde arriba, el cuerpo se dibuja desde `y = -R` (esa parte queda fuera de la
  ventana). **Orejas**, una por lado:
  1. rectángulo `EAR × EAR` de color `pill`, sin borde, pegado al cuerpo en `y ∈ [0, EAR]`:
     izquierda `x ∈ [-EAR, 0]`, derecha `x ∈ [W, W + EAR]`;
  2. encima, un óvalo de radio `EAR` centrado en `(-EAR, EAR)` (izq.) y `(W + EAR, EAR)` (der.)
     con `fill=KEYCOLOR, outline=edge`. El color-llave recorta la curva cóncava y el `outline`
     del óvalo pinta el borde de 1 px sobre el arco.

  Devuelve `True` y `_draw` sigue con el asa, reloj, niveles y botones exactamente como hoy.
- Sacar los `2` de margen: el cuerpo va de `0` a `W`/`H`. Las `y` de los controles (14…40) no
  cambian porque `H` no cambia.

Comprobación visual de la forma: `polígono con puntos [(0,-R), (W,-R), (W,H-R), (W,H), (W-R,H),
(R,H), (0,H), (0,H-R)]` con `smooth=True` da la esquina inferior redondeada; arriba, al estar
fuera de la ventana, no se ve nada.

### 4. Plegar y desplegar

Métodos nuevos, todos en el hilo de Tk:

```python
def _busy(self):
    """Hay algo que el usuario tiene que ver: no se pliega."""
    return bool(self.idle == "warn" or self._no_signal() or self.label or self._pressed
                or self.pause_t0 or self.jobs
                or (self.notes_win and self.notes_win.winfo_viewable()))

def _unfold(self):
    """Abre (si no está abierto) y cancela un plegado pendiente."""
    self._cancel_fold()
    if self._open < 1:
        self._animate(1)

def _fold(self):
    """Cierra, salvo que esté fijado, ocupado o en medio de un arrastre."""
    self._fold_job = None
    if self._pinned or self._busy() or self._dragging:
        return
    if self._open > 0:
        self._animate(0)

def _peek(self):
    """Algo nuevo que mostrar: abre y programa el plegado a los PEEK ms."""
    self._unfold()
    self._arm_fold(self.PEEK)

def _arm_fold(self, ms):
    self._cancel_fold()
    self._fold_job = self.root.after(ms, self._fold)

def _cancel_fold(self):
    if self._fold_job:
        self.root.after_cancel(self._fold_job)
        self._fold_job = None

def _animate(self, target, instant=False):
    """Pasos de 16 ms hasta target (0 o 1). instant=True salta al final (selftest)."""
```

`_animate`: cancela `self._anim` si había; en cada paso mueve `_open` en
`±16 / ANIM`, lo acota a `[0, 1]`, llama `_draw()` y se reprograma con `after(16)` hasta llegar.
Si `_dragging`, salta el paso (igual que `_loop`).

- **Sondeo del mouse** `_hover()`, `after(100)`:

  ```python
  x, y = self.root.winfo_pointerxy()
  wx, wy = self.root.winfo_x(), self.root.winfo_y()
  if self._open:   rect = (wx, wy, wx + self.W + 2*self.EAR, wy + self.H)
  else:            rect = pestañita ± 24 px de margen (así se abre «al acercarse»)
  dentro = rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]
  if dentro: self._unfold()                       # y cancela el plegado pendiente
  elif self._open and not self._fold_job: self._arm_fold(self.FOLD_DELAY)
  self.root.after(100, self._hover)
  ```

  Se sondea `GetCursorPos` (eso hace `winfo_pointerxy`) en vez de confiar en `<Leave>`: con
  color-llave el mouse «sale» de la ventana al pasar por un píxel transparente y el evento no
  es confiable. Diez lecturas por segundo no cuestan nada.

- **Algo nuevo que ver → `_peek()`**:
  - en `_loop` (`:2193`): si `_state()` cambió respecto de `_shown`, además de `_draw()`, llamar
    `_peek()`. El reloj no está en `_state()`, así que grabando no lo dispara cada tick;
  - en `toggle` (`:2118`), tras cambiar `recording`: cubre el atajo global desde otra app;
  - en `_flash` (`:1821`).
- `_state()` (`:1921`) agrega al final `round(self._open, 2)` y `self._pinned`: un paso de
  animación o el pin repintan, y selftest los ve.
- Con `_pinned`, el punto gris del asa (`DOT`, color `#9a9aa0` parado) se pinta de `p["ink"]`
  para que se note que está fijado. Nada más.

### 5. Lo que se rompe al mudarse al borde superior

| Dónde | Hoy | Cambio |
|-------|-----|--------|
| `_move` (`:2024`) | libre en x e y | solo por el borde: `geometry(f"+{x}+0")` con `x` acotada a `[-EAR, sw - W - EAR]` |
| `_show_tip` (`:1801-1802`) | arriba del pill (`winfo_y() - alto - 4`), que arriba queda fuera de pantalla | **debajo**: `y = winfo_y() + H + 4`; `x = winfo_x() + EAR + X_control - ancho // 2` |
| `_open_notes` (`:1856`) | 270 px arriba del pill | debajo: `+{winfo_x() + EAR}+{H + 10}` |
| `_tip_at` (`:1761`) | siempre busca controles | `if self._open < 1: return None` (plegado o animando no hay controles) |
| `_shot` (`:2063-2076`) | guarda y repone `x, y` | nada: ya funciona |
| `_hit` (`:1811`) | — | nada: la pestañita solo tiene tag `pill`, y `<Enter>` ya abre |

### 6. selftest (`:2866-2883` y `:3290-3379`)

- Geometría (`:2868-2883`): sigue igual con el `R` y el `in_pill` nuevos. Sumar
  `assert Widget.TAB_W < Widget.W and Widget.EAR <= Widget.R`.
- Comportamiento (`:3290-3379`): la tupla `_shown` tiene dos campos más al final.
  - `:3293`: agregar `0.0, False` al final del `==` (arranca plegado y sin pin).
  - `:3297`: `[:8]` sigue valiendo.
  - Antes del bloque de botones (`:3301`): `w._animate(1, instant=True)`; los controles solo
    existen abiertos. Todo lo que sigue corre abierto.
  - Sumar, al final del bloque del widget y antes de `w.root.destroy()`:

    ```python
    # notch: plegado no hay controles, solo la pestañita
    w._animate(0, instant=True)
    assert w.c.find_withtag("pill") and not w.c.find_withtag("rec")
    assert w._tip_at(Widget.REC, 26) is None, "plegado no hay tooltips"
    # algo nuevo que ver lo abre y arma el plegado
    w._peek(); assert w._anim and w._fold_job
    w._animate(1, instant=True); w._cancel_fold()
    # ocupado no se pliega; limpio sí; fijado nunca
    w.idle = "warn"; w._fold(); assert w._open == 1
    w.idle = ""; w._fold(); w._animate(0, instant=True) if w._anim else None
    assert w._open == 0
    w._animate(1, instant=True); w._pinned = True; w._fold(); assert w._open == 1
    w._pinned = False
    ```

    (`instant=True` en `_animate` pone `_open = target`, cancela `_anim` y dibuja; así el test
    no espera 360 ms.)

### 7. Docs

- `docs/interfaz.md:3-8`: reescribir el párrafo del widget: notch arriba al centro, plegado
  (pestañita 79×10 con punto de estado) / desplegado (400×54 + orejas), se abre al acercar el
  mouse o cuando hay algo nuevo (`_peek`, 2,5 s), no se pliega mientras `_busy()`, clic derecho
  lo fija, arrastre solo por el borde superior, tooltips y bloc de notas **debajo**.
- `README.md:182`: «**Widget** (notch arriba al centro: se despliega al acercar el mouse,
  arrastrable por el borde superior, clic derecho lo deja fijo)».
- `CHANGELOG.md`: `## [Unreleased]` → `### Cambiado` → «El widget es un notch pegado al borde
  superior, estilo codenotch: plegado a una pestañita, se abre al acercar el mouse o cuando hay
  algo nuevo. Negro puro en tema oscuro.»

---

## Lo que NO entra (y cuándo entraría)

- **Antialiasing.** Las curvas son polígonos de Tk, como hoy. Sobre negro casi no se nota; si
  molesta, renderizar la forma con Pillow (ya instalado) a un `PhotoImage`.
- **Pill adaptativo** de codenotch (leer el fondo para elegir claro/oscuro): sigue el tema.
- **Guardar la x del arrastre.** Arranca centrado, como hoy arranca en la esquina. `notch_x` en
  `settings.json` si alguien lo pide.
- **Otros bordes / otros monitores.** Solo borde superior del monitor principal.
- **Escala.** Sigue sin ser DPI-aware a propósito (`set_dpi_aware`, `:2663`): Windows escala.

## Orden de trabajo

1. §1 y §2 (constantes, ventana, scrollregion) → `--selftest` sigue en verde con `_open = 1`.
2. §3 (forma abierta con orejas) → mirar a ojo en los dos temas.
3. §5 (tooltips, bloc, arrastre) → probar a mano.
4. §4 (plegado, peek, hover, pin) → §6 (tests).
5. §7 (docs) y CHANGELOG.

## Verificación

1. `python takemynotes.py --selftest` en verde.
2. `python takemynotes.py`: arranca plegado arriba al centro, hace el *peek* de 2,5 s y se pliega.
   Acercar el mouse a la pestañita lo abre en ~0,36 s con la forma de notch (plano arriba,
   orejas cóncavas, esquinas de 20 px); alejarlo lo pliega a los 0,7 s.
3. Grabar (botón y `Ctrl+Shift+R` desde otra app): se abre solo, muestra reloj y niveles, se
   pliega y la pestañita queda con el punto rojo. Pausar: queda abierto («en pausa» es `_busy`).
   Micro cortado 60 s → «micrófono sin señal» lo mantiene abierto en ámbar.
4. Tooltips debajo de cada control; el bloc de notas abre debajo del notch.
5. Arrastrar: se desliza solo por el borde superior, no se despega ni se sale de la pantalla.
   Clic derecho: queda fijo; otro clic derecho lo suelta.
6. Tema Auto / Claro / Oscuro desde la ventana: `#000` en oscuro, `#f5f5f7` en claro, sin reiniciar.
7. Captura de pantalla: el notch no sale en la imagen y vuelve a su sitio.
8. `build.bat`: PyInstaller empaqueta igual (no hay imports nuevos).
