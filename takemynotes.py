"""TakeMyNotes
- Sin argumentos: widget flotante NATIVO (Tkinter, transparente, ligero). Graba y transcribe.
- --window: ventana grande estilo Apple (pywebview). Lee la carpeta notas/. Se abre a demanda.
Los dos procesos se comunican por archivos en notas/ (nada de llamadas entre hilos)."""
import os, re, sys, json, time, wave, base64, ctypes, socket, logging, sqlite3, threading, datetime, subprocess
from bisect import bisect_left, bisect_right
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from contextlib import contextmanager
from functools import lru_cache
import numpy as np
# groq se importa donde se usa: arrastra httpx, pydantic y anyio (~0,5-1 s y ~40 MB) y el widget,
# que es el proceso que vive siempre, no lo necesita hasta que termina una reunión.


def Groq(**kw):
    from groq import Groq as G
    return G(**kw)

SR = 16000
BLOCK = 2048
CHUNK_SECS = 600
# Umbrales de RMS, no de pico: ver rms(). Salen de medir las 14 sesiones grabadas hasta hoy.
SILENCE = 0.004           # rms por debajo de esto => el tramo es silencio (ver has_voice)
VOICE = 0.01              # rms por encima de esto => alguien habló (ver idle_state)
VOICE_FRAC = 0.15         # fracción de bloques con voz por debajo de la cual el canal está mudo:
                          # en las 13 sesiones con audio bueno el mínimo fue 39%, en la muda 4.7%
TURN_CHARS = 700          # tope de un turno. Sin esto, una sesión donde TODOS los segmentos
                          # llevan la misma etiqueta (colapso de eco, o un solo canal con audio)
                          # se juntaba en un único turno de 42 KB: un muro sin párrafos, con un
                          # solo t=0.0, sin puntos de salto y sin resaltado al reproducir.
                          # En las sesiones que salen bien el turno medio son ~170 chars, así que
                          # esto no las toca.
IDLE_WARN = 300           # segundos sin voz en ningún canal => preguntar si seguimos
IDLE_STOP = 120           # segundos más sin respuesta => detener y procesar lo grabado
WHISPER = "whisper-large-v3-turbo"
CHAT = "openai/gpt-oss-120b"   # Groq retiró llama-3.3-70b-versatile (404 model_not_found).
                              # Razona en un campo aparte, así content llega limpio (qwen3.6
                              # mete su <think> dentro del texto). Si cae, ver switch_chat()
CHAT_ALT = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "groq/compound"]   # relevos, en orden
ECHO_JIT = 2.0            # los timestamps de los dos canales derivan unos segundos entre sí
ECHO_SPAN = 40.0          # ningún segmento de Whisper dura más: hasta ahí se busca hacia atrás
ECHO_SIM = 0.5            # solape de palabras por encima del cual son la misma frase
ECHO_DUP = 0.6            # eco en más de esta fracción de las palabras del micro => micro sucio
ECHO_MIN = 0.15           # y aportando menos que esto => no hay nada que separar, texto plano
# El tier gratis da 8000 tokens por minuto y Groq cuenta la ENTRADA MÁS el tope de salida que
# pidas: los dos números de acá abajo tienen que sumar menos que eso o el request ni sale (413).
CHAT_CHARS = 12000        # ~3.2k tokens de entrada por request
CHAT_OUT = 4096           # tope de salida. Sin pedirlo, Groq pone 3072 y el texto sale cortado a
                          # media palabra: el "pensar" de gpt-oss gasta de este mismo balde.
                          # Probado darle al tramo 20k de entrada a cambio de 2k de salida: se
                          # queda sin sitio donde escribir y devuelve secciones vacías. Manda la
                          # salida, no la entrada.
# Gemini, opcional y solo para resumir/chatear (la transcripción sigue en Groq, que es quien
# tiene Whisper). Expone un endpoint con la misma forma que el de OpenAI, así que el SDK de Groq
# le habla tal cual cambiándole la base_url: cero dependencias nuevas.
TRAMO_OUT = 2048          # los tramos salen en 400-800 tokens: pedir 4096 era pagar 7,2k de TPM por
                          # request y esperar ~55 s entre tramos. Con 2048 (y el "pensar" de
                          # gpt-oss en low) son 5,2k y ~40 s. Solo Groq: Gemini no tiene TPM.
TEMP = 0.3                # esto es extracción: el 1.0 por defecto da actas distintas en cada
                          # corrida e inventa más
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_CHAT = "gemini-3.7-flash"
GEMINI_CHARS = 400000     # ~100k tokens: la reunión entera en un request, sin trocear
GEMINI_OUT = 32768        # los flash de Gemini razonan largo y el "pensar" sale de este mismo
                          # balde: con 8192 (el número que venía de Groq, donde lo limita el TPM)
                          # gemini-3.5-flash truncaba el acta en 4 de 6 llamadas. Acá no hay TPM
                          # que lo pague, así que ser tacaño solo corta resúmenes.

AUTO_RETRY = 120          # s entre barridos de sesiones en error
AUTO_RETRIES = 5          # intentos automáticos por sesión: una key mala no reintenta para siempre

BASE = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(__file__)))
NOTAS = os.path.join(BASE, "notas")
SETTINGS_PATH = os.path.join(BASE, "settings.json")
ACTION = os.path.join(BASE, ".action")     # el widget le pide algo a la ventana (abrir ⚙)


def res(rel):
    root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, rel)


log = logging.getLogger("tmn")


def setup_log(who):
    """Log rotativo en notas/: sin consola (el .exe es --noconsole) es lo único que queda
    para diagnosticar la máquina de otra persona. Un archivo por proceso, porque rotar el
    mismo archivo desde dos procesos falla en Windows (el rename choca con el lock).
    # ponytail: si algún día hay >1 widget a la vez, meter el pid en el nombre."""
    from logging.handlers import RotatingFileHandler
    os.makedirs(NOTAS, exist_ok=True)
    h = RotatingFileHandler(os.path.join(NOTAS, f"takemynotes-{who}.log"), maxBytes=1_000_000,
                            backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s pid%(process)d %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[h])


# ---------- audio ----------
def _to_i16(x):
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


def rms(a):
    """Volumen del tramo. NO el pico, que es lo que había acá y por lo que la sesión
    20260831_120332 pasó las tres guardas de silencio (canal mudo, auto-stop por inactividad y
    has_voice) con 38 minutos de silencio digital: el chasquido de arranque marcaba pico 0.72 y
    ya nada volvía a mirar. Un click tiene pico alto y rms ~0; la voz tiene crest factor ~4."""
    return float(np.sqrt(np.mean(np.square(a, dtype=np.float64)))) if len(a) else 0.0


def canal_mudo(voz, total):
    """Un canal está mudo si casi ningún bloque tuvo volumen de voz. Fracción y no cuenta
    absoluta: en una reunión de una hora, 20 bloques con ruido son nada, y en una de dos minutos
    son la reunión entera."""
    return voz < VOICE_FRAC * total if total else True


def _open_wav(path):
    w = wave.open(path, "wb")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
    return w


def wav_chunks(paths, secs=CHUNK_SECS):
    """Los WAV de `paths` en trozos crudos de `secs`, leídos de a uno. Antes se cargaba el canal
    entero en float32 y se duplicaba normalizado: una hora son 230 MB por array, y la reunión de
    1 h 45 llegaba a ~2,5 GB de pico (MemoryError en una máquina de 8 GB con Teams abierto). Un
    trozo de 10 min son 38 MB. Con dos rutas los mezcla trozo a trozo, alineados por tiempo."""
    ws = [wave.open(p, "rb") for p in paths if os.path.exists(p)]
    try:
        while True:
            parts = []
            for w in ws:
                x = np.frombuffer(w.readframes(SR * secs), "<i2").astype("float32")
                x /= 32767.0
                parts.append(x)
            if not any(len(x) for x in parts):
                return
            yield parts[0] if len(parts) == 1 else mix_arrays(*parts)
    finally:
        for w in ws:
            w.close()


def write_mix(dst, paths):
    """El .wav de auditoría, mezclado en streaming: nunca tiene la reunión entera en memoria."""
    w = _open_wav(dst)
    try:
        for x in wav_chunks(paths):
            w.writeframes(_to_i16(x))
    finally:
        w.close()


def wav_frames(path):
    if not os.path.exists(path):
        return 0
    with wave.open(path, "rb") as w:
        return w.getnframes()


def write_mono(path, samples):
    w = _open_wav(path)
    w.writeframes(_to_i16(samples))
    w.close()


def cleanup(x):
    """Quita DC y normaliza el pico, EN SITIO (cada copia de un trozo son 38 MB). Se aplica por
    trozo: normalizar por tramo es igual o mejor para Whisper que por sesión entera.
    # ponytail: sin denoise espectral; subir si hace falta."""
    if len(x) == 0:
        return x
    x -= x.mean()
    peak = float(np.abs(x).max())
    if peak > 0:
        x *= 0.9 / peak
    return x


def mix_arrays(mic, loop):
    n = max(len(mic), len(loop))
    if n == 0:
        return np.zeros(0, "float32")
    mic = np.pad(mic, (0, n - len(mic)))
    loop = np.pad(loop, (0, n - len(loop)))
    return np.clip(mic * 0.8 + loop * 0.8, -1, 1)


def chan_path(sid, which):
    return os.path.join(NOTAS, f"_{sid}_{which}.wav")


def shots(sid):
    """Capturas de pantalla de una sesión, en orden. El índice va en el nombre del archivo: no
    hay nada que guardar en el JSON ni que sincronizar entre los dos procesos."""
    if not os.path.isdir(NOTAS):
        return []
    pre = f"{sid}_shot"
    return sorted(os.path.join(NOTAS, f) for f in os.listdir(NOTAS)
                  if f.startswith(pre) and f.endswith(".png"))


def grab_png(path):
    """Captura de todo el escritorio a PNG. all_screens: con dos monitores el default agarra
    solo el primario. El contexto de DPI se pone y se saca en este mismo hilo porque el proceso
    del widget a propósito NO es DPI-aware (ver set_dpi_aware): sin esto Windows le miente el
    tamaño de la pantalla y la captura sale escalada y borrosa."""
    from PIL import ImageGrab
    u, old = ctypes.windll.user32, None
    try:
        u.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        u.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        old = u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
    except Exception:            # Windows < 1703: sale a la resolución virtualizada y listo
        log.warning("captura sin DPI awareness", exc_info=True)
    try:
        ImageGrab.grab(all_screens=True).save(path)
    finally:
        if old:
            u.SetThreadDpiAwarenessContext(old)


class Recorder:
    """Escribe micro y loopback a WAV en disco en vivo (a prueba de crash)."""
    def __init__(self, mic_path, loop_path):
        self.stop_evt = threading.Event()
        self.pause_evt = threading.Event()    # pausado: se sigue leyendo, no se escribe
        self.mute_evt = threading.Event()     # micro cortado: igual, pero solo ese canal
        self.mic_off = False                  # se cortó el micro en algún momento (para el aviso)
        self.mic_path, self.loop_path = mic_path, loop_path
        self.errors = []
        # bloques con voz y bloques escritos, por canal: el pico de toda la sesión no sirve
        # de guarda, un solo chasquido lo deja alto para siempre (ver rms)
        self.mic_voice = self.mic_blocks = self.loop_voice = self.loop_blocks = 0
        self.last_sound = time.time()     # el más reciente de los dos canales
        # rms del último bloque de cada canal: el widget lo dibuja en vivo. "¿Está entrando el
        # audio?" se contestaba al terminar la reunión, con una hora grabada del canal equivocado
        self.mic_level = self.loop_level = 0.0

    def _cap(self, device, path, is_mic):
        w = _open_wav(path)
        try:
            with device.recorder(samplerate=SR, channels=1, blocksize=BLOCK) as r:
                while not self.stop_evt.is_set():
                    b = r.record(numframes=BLOCK)
                    if self.pause_evt.is_set() or (is_mic and self.mute_evt.is_set()):
                        continue          # hay que drenar igual: si no, el device se atrasa
                    if len(b):
                        lv = rms(b)
                        voz = lv > VOICE
                        if voz:
                            self.last_sound = time.time()   # dos hilos, un float: gana el reciente
                        if is_mic:
                            self.mic_blocks += 1
                            self.mic_voice += voz
                            self.mic_level = lv
                        else:
                            self.loop_blocks += 1
                            self.loop_voice += voz
                            self.loop_level = lv
                        w.writeframes(_to_i16(b))
        except Exception as e:
            self.errors.append(str(e))    # el str y no la excepción: el traceback retiene el frame
            log.exception("captura de audio: %s", path)
        finally:
            w.close()                     # finaliza cabecera WAV aunque el loop falle

    def start(self):
        import soundcard as sc
        mic = sc.default_microphone()
        spk = sc.get_microphone(id=str(sc.default_speaker().name), include_loopback=True)
        self.t1 = threading.Thread(target=self._cap, args=(mic, self.mic_path, True), daemon=True)
        self.t2 = threading.Thread(target=self._cap, args=(spk, self.loop_path, False), daemon=True)
        self.t1.start(); self.t2.start()

    def finish(self):
        self.stop_evt.set()
        self.t1.join(); self.t2.join()


def idle_state(idle):
    """Segundos sin voz -> '' normal · 'warn' preguntar si seguimos · 'stop' detener.
    # ponytail: umbral VOICE fijo; con un micro ruidoso el aviso no dispara, subirlo."""
    return ("stop" if idle > IDLE_WARN + IDLE_STOP else
            "warn" if idle > IDLE_WARN else "")


# ---------- API key: cifrada con DPAPI ----------
class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(fn, data):
    """CryptProtectData / CryptUnprotectData: cifrado ligado a la cuenta de Windows.
    Solo ctypes: cero dependencias nuevas y cero hiddenimports que arreglar en PyInstaller."""
    c32 = ctypes.WinDLL("crypt32", use_last_error=True)
    buf = ctypes.create_string_buffer(data, len(data))
    src = _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = _BLOB()
    if not getattr(c32, fn)(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        k = _k32(); k.LocalFree.argtypes = [ctypes.c_void_p]
        k.LocalFree(out.pbData)


def _protect(text):
    return base64.b64encode(_dpapi("CryptProtectData", text.encode())).decode()


def _unprotect(b64):
    return _dpapi("CryptUnprotectData", base64.b64decode(b64)).decode()


# ---------- settings / persistencia ----------
# Las dos keys se guardan cifradas con DPAPI en el campo <nombre>_enc, y se pueden dar también
# por .env. "key" es la de Groq (transcripción y, por defecto, resumen); "gemini_key" es
# opcional y solo cambia con qué se resume.
KEYS = {"key": "GROQ_API_KEY", "gemini_key": "GEMINI_API_KEY"}


def load_settings():
    # keep_audio arranca en True: el audio es la única forma de auditar si la separación de
    # hablantes acertó, y se borra por sesión desde la pestaña Info de la ventana.
    d = {"keep_audio": True, "label_speakers": True, "theme": "auto", "edge": "top", "key": "",
         "gemini_key": "", "chat_model": "", "name": ""}
    raw = {}
    if os.path.exists(SETTINGS_PATH):
        try:
            raw = load_json(SETTINGS_PATH)
            d.update(raw)
        except Exception:
            log.warning("settings.json ilegible, usando defaults", exc_info=True)
    for k in KEYS:
        enc = d.pop(k + "_enc", "")
        if enc and not d.get(k):
            try:
                d[k] = _unprotect(enc)
            except Exception:   # blob de otra cuenta o máquina: hay que pegar la key otra vez
                log.warning("no se pudo descifrar %s", k, exc_info=True)
    # legado en claro: reescribirlo cifrado y quitar el plano. Salvo que esté en claro PORQUE
    # DPAPI falló (_plain): reintentar reescribiría el archivo en cada lectura, y el widget lo
    # relee cada vez que cambia el mtime.
    if any(raw.get(k) for k in KEYS) and not raw.get("_plain"):
        try:
            save_settings(d)
        except Exception:   # carpeta de solo lectura: leer settings nunca debe romper la app
            log.warning("no se pudo migrar la key a cifrado", exc_info=True)
    for k, env in KEYS.items():
        if not d.get(k):
            d[k] = _env_key(env)
    return d


def _env_key(name):
    """Alternativa a pegarla en la ventana: una línea NOMBRE=... en .env, o la variable de
    entorno del mismo nombre."""
    p = os.path.join(BASE, ".env")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get(name, "")


def save_settings(d):
    out = {k: v for k, v in d.items()
           if k not in KEYS and not k.endswith("_enc") and k != "_plain"}
    for k in KEYS:
        if not d.get(k):
            continue
        try:
            out[k + "_enc"] = _protect(d[k])
        except Exception:
            log.exception("DPAPI no disponible: %s se guarda en claro", k)
            out[k], out["_plain"] = d[k], True
    tmp = SETTINGS_PATH + ".tmp"      # atómico como store_session: el widget lo relee cada
    with open(tmp, "w", encoding="utf-8") as f:      # 500 ms y no puede ver un JSON a medias
        json.dump(out, f)
    _replace(tmp, SETTINGS_PATH)


def _aguanta(op, *args, tries=10):
    """os.replace / os.remove que aguantan a un lector. En Windows, si el otro proceso tiene el
    archivo abierto (la ventana relee los JSON en cada poll, OneDrive los bloquea al subirlos)
    dan PermissionError [WinError 5]: Python abre sin FILE_SHARE_DELETE. Esas lecturas duran
    milisegundos, así que esperar un poco alcanza."""
    for i in range(tries):
        try:
            return op(*args)
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.02)


def _replace(tmp, dst):
    return _aguanta(os.replace, tmp, dst)


def spath(sid):
    return os.path.join(NOTAS, f"{sid}.json")


def load_json(path):
    return json.load(open(path, encoding="utf-8"))


def load_session(sid):
    return load_json(spath(sid))


def session_start(sid):
    """El id de una sesión ES el instante en que se apretó grabar. Si algún día deja de serlo,
    guardar la reunión no puede romperse por eso: se cae a ahora y la hora sale aproximada."""
    try:
        return datetime.datetime.strptime(sid, "%Y%m%d_%H%M%S")
    except (ValueError, TypeError):
        return datetime.datetime.now()


def store_session(s):
    os.makedirs(NOTAS, exist_ok=True)
    tmp = spath(s["id"]) + ".tmp"       # escritura atómica: el otro proceso nunca ve un JSON a medias
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)
    _replace(tmp, spath(s["id"]))


PATCH_MUTEX = "Local\\TakeMyNotes.patch"


@contextmanager
def _patch_lock():
    """Mutex NOMBRADO y no un threading.Lock: el widget y la ventana son dos procesos que
    escriben el mismo JSON, y un lock de hilos solo vale dentro de uno. Sirve también entre
    hilos del mismo proceso (el dueño de un mutex de Windows es el hilo). Si alguien lo retiene
    más de 5 s se sigue sin él: mejor una carrera improbable que la app colgada."""
    k = _k32()
    h = k.CreateMutexW(None, False, PATCH_MUTEX)
    got = h and k.WaitForSingleObject(h, 5000) in (0, 0x80)    # WAIT_OBJECT_0 / ABANDONED
    if h and not got:
        log.warning("mutex de sesiones ocupado, sigo sin él")
    try:
        yield
    finally:
        if got:
            k.ReleaseMutex(h)
        if h:
            k.CloseHandle(h)


def _patch(sid, fn=None, **kw):
    """Cambia campos sueltos sobre lo que hay EN DISCO, no sobre una copia vieja en memoria:
    transcribir tarda minutos y en ese rato la ventana puede guardar notas o el nombre. Un
    valor None borra el campo. `fn(s)` modifica a partir de lo leído (alternar favorito, sumar
    al chat) dentro del mismo lock; si devuelve False, no se escribe nada y _patch devuelve None
    (comprobar y reclamar de una vez, ver claim). Si la sesión ya no está, no la resucita."""
    with _patch_lock():
        if not os.path.exists(spath(sid)):
            return None
        s = load_session(sid)
        if fn and fn(s) is False:
            return None
        s.update(kw)
        for k, v in kw.items():
            if v is None:
                s.pop(k, None)
        store_session(s)
        return s


def claim(sid, manual=False):
    """Pasa una sesión a 'pending' con este proceso de dueño, solo si nadie la está
    transcribiendo ya. Comprobar y reclamar van dentro del mismo lock: con la comprobación
    afuera, el reintento automático del widget y un Reintentar de la ventana en el mismo segundo
    arrancaban los dos, subían el audio dos veces y el último en terminar pisaba al otro.
    El automático además solo toma lo que sigue en error y con intentos disponibles; el manual
    devuelve todos los intentos. -> la sesión reclamada, o None."""
    def fn(s):
        if s.get("status") == "pending" and not _stale(s):
            return False                          # la está transcribiendo otro proceso vivo
        if not manual and (_view(s).get("status") != "error"
                           or (s.get("retries") or 0) >= AUTO_RETRIES):
            return False                          # ya no hace falta (o se agotaron los intentos)
        s.pop("error", None)
        s.update(status="pending", pid=os.getpid())
        if manual:
            s["retries"] = 0
    return _patch(sid, fn)


def remove_session(sid):
    """Borra el JSON bajo el mismo mutex que _patch. Afuera, un _patch que ya había comprobado
    que existía la escribía de vuelta después del borrado y la sesión resucitaba.
    -> True si ya no está."""
    with _patch_lock():
        try:
            _aguanta(os.remove, spath(sid))
        except FileNotFoundError:
            pass
        except OSError:
            log.warning("no se pudo borrar %s", sid, exc_info=True)
    return not os.path.exists(spath(sid))


def set_stage(sid, text):
    """Progreso visible para la ventana. Mismo canal que todo: el JSON de la sesión."""
    return _patch(sid, stage=text or None)


def _stale(s):
    """Trabajo en curso cuyo proceso ya murió: se fue con la app y nadie lo va a terminar."""
    pid = s.get("pid")
    busy = s.get("status") == "pending" or bool(s.get("stage"))
    return busy and bool(pid) and not _pid_alive(pid)


def _view(s):
    """Vista de lectura de una sesión: una 'pending' huérfana se presenta como error
    reintentable, y un resumen huérfano deja de decir "Resumiendo…" para siempre.
    Un solo sitio, para que la lista y el detalle nunca se contradigan."""
    if _stale(s):
        if s.get("status") == "pending":
            return dict(s, stage="", status="error",
                        error="Transcripción interrumpida (se cerró la app).")
        return dict(s, stage="")
    return s


def iter_sessions():
    if os.path.isdir(NOTAS):
        for f in os.listdir(NOTAS):
            if f.endswith(".json"):
                try:
                    s = load_json(os.path.join(NOTAS, f))
                except Exception:
                    log.warning("sesión ilegible: %s", f, exc_info=True)
                    continue
                if s.get("id"):       # un .json cualquiera en notas/ no puede tumbar la lista
                    yield s


# ---------- índice de búsqueda (SQLite FTS5: viene en la stdlib) ----------
def _db():
    os.makedirs(NOTAS, exist_ok=True)
    c = sqlite3.connect(os.path.join(NOTAS, ".search.db"))   # derivado de NOTAS, no un global aparte
    # remove_diacritics: "reunion" encuentra "reunión"
    c.execute("create virtual table if not exists notes using fts5("
              "sid unindexed, name, body, tokenize='unicode61 remove_diacritics 2')")
    c.execute("create table if not exists stamp(sid text primary key, mtime real)")
    return c


def _drop(c, sid):
    c.execute("delete from notes where sid=?", (sid,))
    c.execute("delete from stamp where sid=?", (sid,))


def drop_from_index(sid):
    c = _db()
    try:
        _drop(c, sid); c.commit()
    finally:
        c.close()


def sync_index():
    """Reindexa solo las sesiones cuyo JSON cambió (compara mtime). # ponytail: escaneo
    completo del directorio en cada búsqueda; con >5k sesiones, indexar en store_session()."""
    c = _db()
    try:
        have = dict(c.execute("select sid, mtime from stamp"))
        disk = {f[:-5]: os.path.getmtime(os.path.join(NOTAS, f))
                for f in os.listdir(NOTAS) if f.endswith(".json")}
        for sid in have.keys() - disk.keys():       # sesión borrada fuera de la app
            _drop(c, sid)
        for sid, mt in disk.items():
            if have.get(sid) == mt:
                continue
            try:
                s = load_session(sid)
            except Exception:
                continue                            # JSON a medio escribir: al próximo sync
            if not s.get("id"):
                continue                            # un .json que no es sesión: no se indexa
            _drop(c, sid)
            body = "\n".join(x for x in (s.get("transcript"), s.get("notes"),
                                         s.get("summary"), s.get("minuta")) if x)
            c.execute("insert into notes(sid, name, body) values(?,?,?)",
                      (sid, s.get("name") or "", body))
            c.execute("insert into stamp values(?,?)", (sid, mt))
        c.commit()
    finally:
        c.close()


def fts_query(q):
    """Palabras -> consulta FTS5 segura: todas deben aparecer, con prefijo.
    Citar cada token evita que ':', '"' o '*' del usuario rompan la sintaxis."""
    return " ".join('"%s"*' % t for t in re.findall(r"\w+", q or "", re.UNICODE))


_SYNONYMS = {}      # consulta -> términos. En memoria: el proceso vive mientras se busca, y
                    # así notas/ sigue teniendo solo sesiones (ver iter_sessions)


def expand_query(client, q):
    """Términos relacionados con la consulta, pedidos al modelo de chat: Groq no expone
    embeddings, así que el "semántico" lo pone el LLM y FTS5 sigue haciendo la búsqueda."""
    key = " ".join((q or "").lower().split())
    if key not in _SYNONYMS:
        txt = chat(client,
                   "Devuelve SOLO de 4 a 8 sinónimos o términos muy relacionados en español, "
                   "separados por comas. Sin explicar, sin numerar, sin comillas.", key)
        _SYNONYMS[key] = [t for t in (x.strip(" .-•\"'") for x in re.split(r"[,\n]", txt))
                          if 2 < len(t) < 40][:8]
    return _SYNONYMS[key]


def _match(fq):
    """[(sid, fragmento)] por relevancia para una consulta FTS5 ya armada."""
    if not fq:
        return []
    sync_index()
    c = _db()
    try:
        return c.execute("select sid, snippet(notes, 2, '', '', '…', 14) from notes "
                         "where notes match ? order by rank limit 200", (fq,)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        c.close()


def related_index(terms, exclude=()):
    """FTS5 con OR entre los términos sugeridos (cada uno sigue siendo AND por dentro),
    quitando lo que ya salió en la búsqueda literal."""
    fq = " OR ".join(f"({x})" for x in map(fts_query, terms) if x)
    return [r for r in _match(fq) if r[0] not in exclude]


def search_index(q):
    """Busca en título, transcripción, notas y resumen."""
    return _match(fts_query(q))


# ---------- Groq ----------
def _segments(r):
    segs = getattr(r, "segments", None)
    if segs is None and isinstance(r, dict):
        segs = r.get("segments")
    out = []
    for s in segs or []:
        g = s.get if isinstance(s, dict) else (lambda k, d, s=s: getattr(s, k, d))
        out.append((float(g("start", 0)), float(g("end", 0)), (g("text", "") or "").strip()))
    return out


def has_voice(raw, t0, t1):
    """¿El tramo [t0, t1) del canal tiene voz? Whisper ALUCINA sobre el
    silencio: en cada ventana de 30 s muda devuelve una muletilla ("Gracias.", "¡Suscríbete al
    canal!") y lo hace con no_speech_prob 0 y avg_logprob normal, así que el modelo no lo delata
    y la única señal fiable es el audio. Se mira el canal CRUDO y no el de cleanup(): ese
    normaliza el pico del canal entero, o sea que le sube el volumen al ruido justo donde no hay
    voz. Sin `raw` (sesión vieja, o quien llame sin pasarlo) no se filtra nada.
    Medido sobre las 14 sesiones grabadas: con el pico dejaba pasar el 98% de las ventanas de la
    sesión muda (por eso llegó a resumirse un acta vacía); con rms deja pasar el 27%, y a las
    buenas les quita entre 0% y 4% de ventanas, que son las que de verdad son silencio.
    # ponytail: umbral absoluto. Con un micro muy flojo (todo por debajo de SILENCE) se llevaría
    # frases reales; ahí hay que subir la ganancia de entrada o SILENCE."""
    if raw is None or not len(raw):
        return True
    a = int(max(0.0, t0) * SR)
    part = raw[a:max(int(t1 * SR), a + 1)]
    return not len(part) or rms(part) > SILENCE


def n_chunks(frames):
    """Trozos de CHUNK_SECS en que se va a partir un canal de `frames` muestras: el total
    para el progreso."""
    return max(1, -(-frames // (SR * CHUNK_SECS)))


def tramo_mudo(raw, win=30):
    """¿Ninguna ventana de `win` s pasa de SILENCE? Un canal que no es mudo en total puede tener
    trozos enteros de silencio (la presentación de 40 min donde solo habla el otro). Subirlo
    son 19 MB y cuota de audio por hora — que con dos canales se gasta el doble — para que
    Whisper alucine sobre el silencio y has_voice lo tire igual."""
    step = SR * win
    return all(rms(raw[i:i + step]) <= SILENCE for i in range(0, len(raw), step))


def transcribe_channel(client, chunks, label, done=None):
    """`chunks`: trozos CRUDOS de CHUNK_SECS (ver wav_chunks). Devuelve [(t, label, texto, fin)]
    con timestamps absolutos. Se sube la copia normalizada; con la cruda se tira lo que el
    modelo alucina en los tramos mudos (ver has_voice)."""
    segs, base = [], 0.0
    tmp = os.path.join(NOTAS, f"_seg_{os.getpid()}_{threading.get_ident()}.wav")
    try:
        for raw in chunks:
            if not tramo_mudo(raw):
                write_mono(tmp, cleanup(raw.copy()))
                with open(tmp, "rb") as f:
                    r = client.audio.transcriptions.create(
                        file=("c.wav", f.read()), model=WHISPER, language="es",
                        response_format="verbose_json")
                for t, end, txt in _segments(r):
                    if txt and has_voice(raw, t, end):
                        segs.append((base + t, label, txt, base + end))
            base += len(raw) / SR
            if done:
                done()
    finally:
        if os.path.exists(tmp):           # no dejar el temporal si Groq/red falla
            os.remove(tmp)
    return segs


def dialog_turns(segs):
    """[(t, etiqueta, texto)] -> [{t, who, text}] juntando los segmentos seguidos del mismo
    hablante, hasta TURN_CHARS. Conserva el segundo en que arranca cada turno, que es lo que le
    permite a la ventana saltar el audio a una línea de la transcripción y resaltar la que suena.
    El tope importa cuando NO hay alternancia de hablantes: ahí la etiqueta no corta nunca y sin
    él sale un turno único con toda la reunión adentro."""
    out = []
    for t, who, text, *_ in sorted((s for s in segs if s[2]), key=lambda x: x[0]):
        if out and out[-1]["who"] == who and len(out[-1]["text"]) + len(text) <= TURN_CHARS:
            out[-1]["text"] += " " + text
        else:
            out.append({"t": round(t, 1), "who": who, "text": text})
    return out


def format_dialog(segs):
    """La misma conversación en texto plano ("Etiqueta: lo que dijo"): es lo que se indexa en
    FTS5, lo que se le manda a Groq para resumir y lo que leen las sesiones viejas, que se
    guardaron antes de que existieran los turnos con tiempo."""
    return "\n\n".join(f"{x['who']}: {x['text']}" if x["who"] else x["text"]
                       for x in dialog_turns(segs)).strip()


def _bag(t):
    return set(re.findall(r"[^\W_]+", (t or "").lower()))


def drop_echo(segs, mic_label, jit=ECHO_JIT, sim=ECHO_SIM):
    """Si el micro capta los parlantes, la voz de los demás sale en LOS DOS canales y
    format_dialog las intercala como si fueran dos personas hablando por turnos. Se descarta
    la copia del micro (el loopback es un tap digital de la salida: ese no tiene eco).
    Devuelve (segmentos, eco, aporte) contando PALABRAS y no segmentos, que son de tamaños
    muy distintos: `eco` = cuánto del micro era copia del loopback, `aporte` = cuánto del
    texto que queda viene solo del micro. Con eco alto y aporte mínimo no hay dos hablantes
    que separar: es el mismo audio dos veces.
    # ponytail: bolsa de palabras contra solape de intervalos. Lo correcto sería cancelación de
    # eco real (el DSP de WASAPI) o un duck por energía entre canales; pide COM o calibrar
    # niveles. Los segmentos sin fin (sesión vieja, tests) valen como instante."""
    other = sorted((t, e[0] if e else t, _bag(x)) for t, l, x, *e in segs if l != mic_label)
    times = [t for t, _, _ in other]
    out, mic_w, echo_w = [], 0, 0
    for seg in segs:
        t, label, text, *e = seg
        if label != mic_label:
            out.append(seg); continue
        w, n, end = _bag(text), len(text.split()), (e[0] if e else t)
        mic_w += n
        # Se comparan los dos INTERVALOS, no la distancia entre arranques: el eco suena a la
        # vez que el audio que lo produce, pero Whisper devuelve segmentos de hasta media
        # minuto y el timestamp es el del principio, así que el eco de la última frase de un
        # segmento largo cae 10-15 s después de su t. Con la ventana de ±6 s sobre el arranque
        # se escapaba ~1 de cada 4 turnos del micro, y esos restos quedan dichos por quien
        # graba: el resumen le pone en la boca lo que dijo el otro.
        lo = bisect_left(times, t - ECHO_SPAN)      # cota del barrido, el filtro real es el solape
        hi = bisect_right(times, end + jit)
        # >=4 palabras: basta el solape. Corto (1-3): solo se descarta si TODO el fragmento es
        # subconjunto de lo que el otro canal dijo cerca — un "sí" o un "ok" que nadie dijo se
        # queda, pero el eco corto ("entonces", "por allá") deja de duplicar frases enteras.
        if any(((len(w) >= 4 and len(w & w2) / min(len(w), len(w2)) >= sim) or
                (len(w) < 4 and w and w <= w2))
               for _, e2, w2 in other[lo:hi] if w2 and e2 + jit >= t):
            echo_w += n; continue
        out.append(seg)
    total = sum(len(x[2].split()) for x in out)
    return out, (echo_w / mic_w if mic_w else 0.0), ((mic_w - echo_w) / total if total else 0.0)


def _agotado_el_dia(e):
    """El 429 por cuota DIARIA no se parece en nada al 429 por minuto aunque traiga el mismo
    número: no se rellena esperando un rato (Groq dice "try again in 47m", Gemini directamente
    no vuelve hasta medianoche). Esperar ahí son seis minutos tirados por proveedor antes de
    llegar al relevo, que es lo único que puede salvar el acta. Se mira el texto porque es lo
    único que distingue uno de otro: Groq escribe "tokens per day (TPD)" y Gemini nombra la
    cuota "GenerateRequestsPerDayPerProjectPerModel"."""
    t = str(e).lower()
    return "per day" in t or "perday" in t


def _retryable(e):
    """429 = pasaste los tokens por minuto: esperar sirve. 5xx = el proveedor está saturado o
    caído un rato ("model is currently overloaded", el 503 con el que Gemini contesta a cada
    rato): esperar también sirve, y sin esto el relevo se disparaba por un bache de un minuto.
    413 = este request solo ya no cabe: esperar no cambia nada, hay que mandar menos texto.
    Los cortes de red los reintentamos acá porque a los clientes de chat les sacamos los
    reintentos del SDK (ver chat_client)."""
    if e.__class__.__name__ in ("APIConnectionError", "APITimeoutError"):
        return True
    if getattr(e, "status_code", None) == 429 and _agotado_el_dia(e):
        return False
    return getattr(e, "status_code", None) in (429, 500, 502, 503, 504)


def humano(e):
    """El error de una API como lo lee una persona. Sin esto la ventana mostraba
    "Error code: 429 - {'error': {'message': 'Rate limit reached..." como error del acta."""
    code = getattr(e, "status_code", None)
    if e.__class__.__name__ in ("APIConnectionError", "APITimeoutError"):
        return "Sin conexión con el servicio de IA."
    if code == 401:
        return "API key inválida: revísala en ⚙."
    if code == 429 and _agotado_el_dia(e):
        return "Cuota del día agotada: cambia el modelo en ⚙ o espera a mañana."
    if code == 429:
        return "Demasiadas peticiones seguidas: prueba de nuevo en un minuto."
    if code == 413:
        return "La reunión no cabe en un request."
    if code == 404:
        return "Ese modelo no existe: cámbialo en ⚙."
    return str(e)[:200]


def switch_chat(client):
    """Groq retira modelos cada pocos meses sin avisar: llama-3.3-70b-versatile se fue con un 404
    y dejó todas las sesiones en error hasta tocar el código. Ante un 404 saltamos al primer
    relevo que la key SÍ vea (models.list() es la única verdad: qué modelos hay depende de la
    cuenta). El relevo dura lo que dure el proceso; el arreglo de verdad es editar CHAT."""
    global CHAT
    try:
        ids = {m.id for m in client.models.list().data}
    except Exception:
        return False
    for m in CHAT_ALT:
        if m in ids and m != CHAT:
            log.warning("El modelo %s ya no existe en Groq, uso %s", CHAT, m)
            CHAT = m
            return True
    return False


def chat_client(st=None):
    """El cliente con el que se RESUME, que no es el de Whisper. Si hay key de Gemini se prefiere
    esa por su ventana: la reunión entra entera en un request y el acta deja de armarse por
    tramos, que es de donde salen los descartes sin referente y los participantes mal atribuidos.
    No hay que elegir proveedor: con las dos keys puestas, la de Groq queda de `relevo` y entra
    sola si Gemini no puede. Los dos planes gratuitos tienen tope diario, y el día que uno se
    agota es justo el día que no querés quedarte sin acta.
    El modelo, el tamaño de tramo y el tope de salida viajan pegados al cliente: así los ocho
    sitios que llaman a chat() no tienen que enterarse de con quién están hablando."""
    st = st if st is not None else load_settings()
    # El modelo se puede fijar a mano en Configuración: los catálogos cambian sin avisar (Groq
    # retiró llama-3.3 de un día para otro) y así no hay que tocar código para seguirlos.
    # Aplica al preferido; el relevo entra siempre con su modelo por defecto, que es el que se
    # sabe que existe.
    # max_retries=0 en los dos: el SDK reintenta 4 veces solo, y con chat() reintentando otras
    # 4 encima, un resumen que falla se lleva 20 requests. La cuota gratis de Gemini son 20 por
    # día y por modelo: un solo 429 al mediodía dejaba el día entero sin resúmenes. Los
    # reintentos que sirven (con esperas de verdad) los hace chat().
    elegido = (st.get("chat_model") or "").strip()
    c = Groq(api_key=st.get("key", ""), max_retries=0)
    c.modelo, c.trozo, c.tope, c.relevo, c.tramo_out = CHAT, CHAT_CHARS, CHAT_OUT, None, TRAMO_OUT
    if st.get("gemini_key"):
        # El SDK de Groq NO sirve acá aunque el endpoint sea compatible: pega
        # "/openai/v1/chat/completions" a la base_url y Gemini espera "/chat/completions".
        # Importado dentro para que quien no use Gemini no necesite el paquete.
        from openai import OpenAI
        g = OpenAI(api_key=st["gemini_key"], base_url=GEMINI_URL, max_retries=0)
        g.modelo, g.trozo, g.tope = elegido or GEMINI_CHAT, GEMINI_CHARS, GEMINI_OUT
        g.tramo_out = None
        g.relevo = c if st.get("key") else None
        return g
    c.modelo = elegido or CHAT
    return c


def chat(client, system, user, tries=4, out=None, effort=None):
    # getattr y no client.modelo: el cliente de Whisper y los de los tests no pasan por
    # chat_client() y tienen que seguir funcionando contra Groq.
    model = getattr(client, "modelo", CHAT)
    out = out or getattr(client, "tope", CHAT_OUT)
    for i in range(1, tries + 1):
        # reasoning_effort solo a gpt-oss: el "pensar" sale del mismo balde de tokens por minuto
        # y otro modelo podría rechazar un parámetro que no conoce
        extra = {"reasoning_effort": effort} if effort and "gpt-oss" in model else None
        try:
            r = client.chat.completions.create(model=model, max_completion_tokens=out,
                temperature=TEMP, extra_body=extra,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}])
            if not r.choices:     # Gemini, cuando bloquea por seguridad: lista vacía
                raise RuntimeError(f"{model} no devolvió texto")
            c = r.choices[0]
            # Sin tope explícito Groq pone 3072, y en un modelo de razonamiento el "pensar"
            # gasta de ese mismo presupuesto: el acta salía cortada a media palabra, distinto
            # en cada corrida. Si aun así se corta, se avisa en el texto: un resumen truncado
            # que no lo dice se lee como si la reunión hubiera terminado ahí.
            # content=None pasa (gpt-oss o Gemini que agotaron el tope pensando, o un bloqueo):
            # sin texto y sin corte es un fallo, y con_relevo se lo pasa al otro proveedor.
            t = (c.message.content or "").strip()
            if c.finish_reason == "length":
                log.warning("respuesta truncada (%s tokens)", out)
                t += "\n\n[…cortado: el modelo llegó a su límite de salida]"
            elif not t:
                raise RuntimeError(f"{model} no devolvió texto")
            return t
        except Exception as e:
            # el relevo de modelo es cosa de Groq: CHAT_ALT son modelos suyos y models.list()
            # contra Gemini devuelve otro catálogo. Con Gemini, un 404 se reporta tal cual.
            if getattr(e, "status_code", None) == 404 and model == CHAT and switch_chat(client):
                model = CHAT
                continue          # con otro modelo, y sin gastar la espera del rate limit
            if i == tries or not _retryable(e):
                raise
            # el balde de tokens del tier gratis se rellena por minuto: los reintentos cortos
            # del SDK (~8 s) no alcanzan, hay que esperar de verdad
            wait = 20.0 * i
            try:
                wait = min(60.0, float(e.response.headers.get("retry-after")) + 1)
            except Exception:
                pass
            log.warning("%s: rate limit, espero %.0fs (intento %d/%d)", model, wait, i, tries)
            time.sleep(wait)


def split_text(t, limit=CHAT_CHARS):
    """Trozos de <= limit chars cortando por párrafo, que es por donde cambia el hablante.
    Un párrafo más largo que el límite (transcripción sin etiquetar, todo seguido) se corta duro."""
    blocks = []
    for b in (t or "").split("\n\n"):
        blocks += [b[i:i + limit] for i in range(0, len(b), limit)]
    out, cur = [], ""
    for b in blocks:
        if cur and len(cur) + len(b) + 2 > limit:
            out.append(cur); cur = b
        else:
            cur = f"{cur}\n\n{b}" if cur else b
    return out + ([cur] if cur.strip() else [])


SUM_SYS = """Sos el acta de una reunión: en español, combinando las NOTAS del usuario con la
TRANSCRIPCIÓN. Formato: párrafos y bullets de un solo nivel, con los títulos de sección en una
línea suelta y en mayúsculas. Negrita con **así** sí se ve. Nada de tablas, ni encabezados de
markdown, ni líneas de guiones: la ventana no las renderiza y salen como texto crudo.
La PRIMERA línea es "TÍTULO: " y un título de 3 a 6 palabras para la reunión, sin comillas ni
punto. Después, el acta.
Secciones, en este orden:
QUIÉNES: una línea por persona con nombre, lado (de quien convoca / del cliente / no se sabe) y
rol concreto (qué hace, no "participante"). Para el lado mirá quién dice "su/ustedes" hablando
del sitio o del negocio del otro — ése vende, es de quien convoca — y quién habla en primera
persona de la operación de la que se habla — ése compra, es del cliente. Un nombre suelto sin esa
señal va como no se sabe, y quien se presenta ante los demás suele ser de quien convoca. La etiqueta del hablante viene de una separación por
canales que se equivoca: si lo que dice un turno no encaja con su etiqueta, mandá el contenido y
agregá (inferido). Si no hay señales, escribí una sola línea diciendo que no se pudo separar a
los participantes, y no atribuyas nada en el resto del acta.
DECIDIDO: solo aquello a lo que alguien se comprometió o que quedó zanjado. Un elogio, una
opinión o una descripción de cómo funciona algo no es una decisión.
PROPUESTO (sin respuesta): lo que alguien planteó y nadie contestó. 10 como máximo: juntá en un
solo bullet todas las variantes del mismo tema. Una lista larga de propuestas no la lee nadie.
DESCARTADO: lo que se propuso y se cayó, con el motivo. Esta sección no se omite: que algo muera
en la reunión importa tanto como que se apruebe. El rechazo casi nunca viene pegado a la
propuesta: si en algún momento alguien dijo que algo no tiene sentido, que ya lo trae resuelto o
que se le complica, eso va acá aunque antes se haya hablado de ello como plan.
YA ENTREGADO: lo que se mostró o se dio por hecho durante la reunión. Nunca lo repitas como
pendiente.
PREGUNTAS SIN RESPONDER: preguntas que quedaron en el aire y que se entienden solas al leerlas.
Ahí suele estar lo que bloquea todo lo demás. Una pregunta de dos palabras, o una que no se sabe
sobre qué es, no se incluye: es ruido de la transcripción.
ACCIONES: agrupadas por responsable, que es como se leen después: una línea con el nombre en
negrita (**Eric**) y debajo, en bullets, TODAS sus acciones juntas. No repitas el nombre dentro
del bullet ni abras dos veces el mismo responsable. Primero quien más carga lleva; al final el
grupo **SIN DUEÑO - ASIGNAR** con lo que nadie tomó. 10 acciones como máximo en total, fusionando
las que son la misma. La acción que dependa de algo que sigue abierto cierra su propio bullet con
" - Bloqueada por: ...", no en una nota suelta al final. Si quien convoca cerró enumerando (uno,
dos, tres...), esos son los acuerdos y van primero dentro del grupo de su responsable, tal como
los dijo.
MENCIONADO: lo que se dijo al pasar y no entró en ninguna sección anterior. Las líneas PERSONAS
de los tramos son materia prima para QUIÉNES: no las copies acá ni en ninguna otra sección. Nada de repetir acá
lo que ya pusiste arriba; si no queda nada, saltá la sección.
Reglas duras: no escribas ningún nombre propio, marca ni tecnología que no aparezca literal en el
texto; si viene mal transcrito, copialo igual y agregá [sic?] en vez de arreglarlo. No conviertas
una restricción del cliente en tarea propia, ni un "podríamos" en un plan, ni una anécdota en un
entregable. Lo que se está vendiendo no es lo que ya existe. No inventes responsables ni equipos.
No expandas siglas. Si una frase quedó incomprensible en la transcripción, tirala en vez de
adivinar qué quiso decir: una pregunta que no se entiende no es una pregunta pendiente.
Fechas: viene la FECHA de la reunión. Si alguien dice una fecha relativa ("el viernes", "en dos
semanas") y se puede calcular desde ahí, escribí la fecha absoluta dd/mm/aaaa y entre paréntesis
lo que se dijo. Si no se puede calcular, copiá lo que se dijo tal cual."""
TRAMO_SYS = """Extraé en español, en bullets y sin introducción, lo que pasa en este TRAMO de una
reunión. Empezá con una línea PERSONAS: y ahí anotá a cada quien se nombre en el tramo con la
frase textual que lo delata: cómo se presentó, cómo lo llamaron, o un pedazo de lo que dijo donde
use "ustedes/su" o "nosotros/nuestro". La frase, no una conclusión tuya. Sin frase no lo pongas.
Si alguien enumera acuerdos ("uno... dos... tres..."), copiá la enumeración entera tal cual.
Después, un bullet por cosa, cada uno marcado con DECIDIDO / PROPUESTO / DESCARTADO / YA
ENTREGADO / PREGUNTA SIN RESPONDER / TAREA (con responsable, o SIN DUEÑO). Si alguien tumba,
frena o le pone un pero a algo, va como DESCARTADO con la frase con la que lo tumbó: es lo
primero que se pierde al resumir. Copiá los nombres propios, marcas y cifras tal cual aparecen,
aunque estén mal transcritos. Solo lo que está en el texto.
Si arriba del tramo vienen NOTAS del usuario, buscá en el tramo lo que ellas mencionan; no las
copies. Si viene HASTA AHORA, es quién es quién según los tramos anteriores: usalo para reconocer
a la gente y no lo repitas, salvo lo que este tramo agregue."""
MINUTA_SYS = """Redactá la MINUTA formal de una reunión, en español, para enviar a los asistentes.
Material: el ACTA ya hecha, las NOTAS del usuario y los datos de la reunión. No inventes nada que
no esté ahí. Texto plano: títulos en mayúsculas en una línea suelta, listas con "- " o "1. ",
nada de tablas, ni encabezados de markdown, ni líneas de guiones.
Fechas absolutas en dd/mm/aaaa calculadas desde la fecha de la reunión; lo que no se pueda
calcular va como se dijo, entre comillas. Ningún nombre que no esté en el acta. Lo inferido lleva
"(inferido)". Lo que no se sabe dice "No se definió" o "No se indica": nunca vacío ni inventado.
Formato exacto:
MINUTA DE REUNIÓN

Reunión: <título>
Fecha: <dd/mm/aaaa>
Hora: <hh:mm> a <hh:mm> (Duración: <n> min)
Modalidad: <Virtual / Presencial / No se indica>
Convocó: <nombre o No se indica>

ASISTENTES
- <Nombre> — <cargo o área> — <de quien convoca / del cliente / no se sabe>

ORDEN DEL DÍA
1. <Tema tratado, en el orden en que apareció>

DESARROLLO
1. <Tema>
   Discusión: <dos o tres líneas con lo que se planteó y quién>
   Conclusión: <a qué se llegó, o "Sin conclusión">

ACUERDOS Y COMPROMISOS
1. <Acuerdo> — Responsable: <nombre o SIN DUEÑO> — Fecha: <dd/mm/aaaa o "No se definió">

TEMAS PENDIENTES
- <Lo propuesto sin respuesta y las preguntas abiertas>

PRÓXIMA REUNIÓN
<Fecha, hora y tema si se mencionaron; si no, "No se definió">

Elaboró: TakeMyNotes (borrador automático; revisar antes de enviar)"""
DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def cuando(s):
    """Fecha y hora de inicio de una sesión para el modelo, con el día de la semana: sin eso "el
    viernes" no se puede pasar a fecha."""
    t0 = session_start(s.get("id"))
    return f"{t0:%d/%m/%Y %H:%M} ({DIAS[t0.weekday()]})"


def split_title(text):
    """El acta trae el título en la primera línea ("TÍTULO: …"). Pedírselo ahí y no en un
    request aparte ahorra uno por sesión, y en Groq ese request salía justo antes del resumen,
    del mismo balde por minuto. -> (título, acta sin esa línea); ("", texto) si no vino."""
    m = re.match(r"\s*\**\s*T[IÍ]TULO\s*\**\s*:\s*\**(.*)(?:\n|$)", text or "", re.I)
    if not m:
        return "", text
    return clean_name(m.group(1).strip(' "*.'), 60), text[m.end():].lstrip()


# Los mismos criterios que md() en la ventana: si difieren, el tilde de una acción se le pone
# a otra. check_ui.js y selftest prueban los dos lados contra la misma acta.
_HEAD = re.compile(r"^(?:#{1,4}\s+(.*)|([A-ZÁÉÍÓÚÜÑ][A-ZÁÉÍÓÚÜÑ0-9 ]{2,40}"
                   r"(?: \([^)\n]{1,40}\))?):?[ \t]*)$")
_ITEM = re.compile(r"^\s*(?:[-*•]|\d+\.)\s+(.*)$")
_WHO = re.compile(r"^\s*\*\*(.+?)\*\*\s*:?\s*$")


def acciones(summary):
    """Las ACCIONES del acta como [(índice, responsable, texto)]. El índice cuenta los bullets
    de esa sección en orden, igual que md() al ponerles el checkbox: es lo que va a s["done"].
    El responsable es la última línea suelta en negrita (**Eric**) que las encabeza."""
    out, dentro, who = [], False, ""
    for line in (summary or "").splitlines():
        h = _HEAD.match(line)
        if h:
            dentro = (h.group(1) or h.group(2) or "").strip().upper().startswith("ACCIONES")
            who = ""
            continue
        if not dentro:
            continue
        w, it = _WHO.match(line), _ITEM.match(line)
        if it:
            out.append((len(out), who, re.sub(r"\*\*(.+?)\*\*", r"\1", it.group(1)).strip()))
        elif w:
            who = w.group(1).strip()
    return out


def con_relevo(fn, client, *args):
    """Las dos keys puestas no son una opción a elegir: son un plan A y un plan B. Si el
    preferido no puede — cuota del día agotada, key mala, servicio caído — se repite el trabajo
    con el otro y la reunión termina con su acta igual.
    Se repite ENTERO y no desde donde falló, porque el troceo depende de cuánto le cabe a cada
    uno: lo que Gemini resume de una pieza, Groq lo tiene que partir en tramos."""
    try:
        return fn(client, *args)
    except Exception as e:
        rel = getattr(client, "relevo", None)
        if rel is None:
            raise
        log.warning("%s no pudo (%s), repito con %s", getattr(client, "modelo", "?"),
                    str(e)[:80], getattr(rel, "modelo", "?"))
        return fn(rel, *args)


def summarize_text(client, notes, transcript, progress=None, when=""):
    return con_relevo(_summarize, client, notes, transcript, progress, when)


def _summarize(client, notes, transcript, progress=None, when=""):
    """Una reunión de una hora son ~13k tokens y el tier gratis de Groq acepta 8k por minuto:
    de una sola pieza devuelve 413. Se resume por tramos y después se resumen los resúmenes.
    # ponytail: trocear es lo que hace caber la reunión, y también el techo de calidad del acta.
    # Cada tramo se resume a ciegas, así que lo que cruza el corte se pierde: la propuesta queda
    # en un tramo y el "no, eso no tiene sentido" en el siguiente, la enumeración de acuerdos del
    # cierre se parte, y quién es quién se decide sin haber oído las presentaciones. Los prompts
    # tapan parte (el tramo rescata nombres, citas de rechazo y enumeraciones), pero el arreglo
    # de verdad es no trocear: con un TPM que acepte ~17k, esto es un solo request y sobra."""
    trozo = getattr(client, "trozo", CHAT_CHARS)
    parts = split_text(transcript, trozo)
    cab = f"FECHA DE LA REUNIÓN: {when}\n\n" if when else ""
    def step(i, n):
        if progress:
            progress(i, n)
    if len(parts) <= 1:
        step(1, 1)
        return chat(client, SUM_SYS, f"{cab}NOTAS:\n{notes}\n\nTRANSCRIPCIÓN:\n{transcript}")
    # A cada tramo le llegan las notas (suelen ser <1000 chars y dicen qué buscar) y las líneas
    # PERSONAS de los anteriores: sin eso, quién es quién se reiniciaba cada 12 000 caracteres.
    guia = f"NOTAS del usuario:\n{notes[:1500]}\n\n" if (notes or "").strip() else ""
    outs, personas = [], []
    for i, p in enumerate(parts, 1):      # secuencial a propósito: el límite es por minuto
        step(i, len(parts) + 1)
        previo = f"HASTA AHORA:\n{chr(10).join(personas)[:800]}\n\n" if personas else ""
        o = chat(client, TRAMO_SYS, f"{guia}{previo}TRAMO:\n{p}",
                 out=getattr(client, "tramo_out", None), effort="low")
        outs.append(o)
        personas += [x.strip() for x in o.splitlines()
                     if x.strip().upper().startswith("PERSONAS") and x.strip() not in personas]
    step(len(parts) + 1, len(parts) + 1)
    joined = "\n\n".join(outs)
    # Reunión larguísima: ni los resúmenes de los tramos entran en un request. Se resumen otra vez
    # por el mismo camino; la guarda es que hayan encogido, si no esto no terminaría nunca.
    if len(joined) > trozo and len(joined) < len(transcript):
        return _summarize(client, notes, joined, progress, when)
    return chat(client, SUM_SYS, f"{cab}NOTAS:\n{notes}\n\n"
                "TRANSCRIPCIÓN (resúmenes de cada tramo, en orden):\n" + joined)


def ask_text(client, transcript, q):
    return con_relevo(_ask, client, transcript, q)


def _ask(client, transcript, q):
    """Igual que el resumen: si la transcripción no cabe en un request, se pregunta tramo por
    tramo y se juntan las respuestas que trajeron algo."""
    parts = split_text(transcript, getattr(client, "trozo", CHAT_CHARS))
    if len(parts) <= 1:
        return chat(client, "Responde en español usando SOLO esta transcripción:\n\n" + transcript, q)
    hits = [a for a in (chat(client, "Responde en español usando SOLO este tramo de la "
                              "transcripción. Si el tramo no dice nada sobre la pregunta, "
                              "responde exactamente SIN DATOS.\n\n" + p, q)
                        for p in parts)
            if "SIN DATOS" not in a.upper()]
    if not hits:
        return "No encontré nada sobre eso en esta sesión."
    return chat(client, "Unificá en español estas respuestas parciales sobre la misma reunión "
                        "en una sola, sin repetir.", f"PREGUNTA: {q}\n\n" + "\n\n".join(hits))


def minuta_text(client, s):
    return con_relevo(_minuta, client, s)


def _minuta(client, s):
    """La minuta sale del ACTA y no de la transcripción: un solo request que entra en Groq sin
    trocear (acta ~3-4k chars + notas) y gasta una sola de las llamadas diarias de Gemini.
    Fecha, hora de fin y duración van calculadas: son cuentas, no algo para que adivine el modelo."""
    t0, dur = session_start(s.get("id")), int(s.get("dur") or 0)
    fin = t0 + datetime.timedelta(seconds=dur)
    datos = (f"Reunión: {s.get('name') or 'Sin título'}\n"
             f"Fecha: {t0:%d/%m/%Y} ({DIAS[t0.weekday()]})\n"
             f"Hora: {t0:%H:%M} a {fin:%H:%M} (Duración: {max(1, round(dur / 60))} min)")
    return chat(client, MINUTA_SYS, f"DATOS:\n{datos}\n\nNOTAS:\n{s.get('notes') or '(sin notas)'}"
                                     f"\n\nACTA:\n{s.get('summary', '')}")


def clean_name(n, limit=80):
    """Único criterio para el nombre de una sesión, venga del usuario o del modelo: sin saltos
    de línea ni espacios de más, y cortado. La UI hace lo mismo para mostrarlo al instante."""
    return " ".join((n or "").split())[:limit]


def speaker_label(n):
    """Tu nombre como etiqueta de hablante. Sin ':' ni saltos: la transcripción es texto plano
    con formato "Etiqueta: lo que dijo" y la UI lo parte por ahí para armar las burbujas."""
    return clean_name((n or "").replace(":", " "), 24)


def name_session(client, transcript):
    if not transcript:
        return "Sesión sin audio"
    try:
        t = chat(client, "Devuelve SOLO un título de 3 a 6 palabras en español, sin comillas ni punto.",
                 transcript[:2000])
        return clean_name(t.strip('"'), 60) or "Reunión"
    except Exception:
        log.warning("no se pudo nombrar la sesión", exc_info=True)
        return "Reunión"


MIC_LABEL, THEM_LABEL = "Yo", "Los demás"


_TX_LOCK = threading.Lock()


def do_transcription(sid):
    """Transcribe una sesión pendiente y la resume. Lo usan el widget (nueva) y la ventana
    (reintento). Se puede grabar la reunión siguiente mientras esto corre, pero DE UNA A LA VEZ:
    dos transcripciones juntas son el doble de tokens por minuto contra el mismo límite de Groq. La que espera se ve "En cola…" en la ventana.
    # ponytail: lock de proceso, no de máquina. Un Reintentar desde la ventana (otro proceso)
    # sí puede solaparse con el widget; lo que ya evitaba eso es el claim a 'pending'."""
    if _TX_LOCK.locked():
        _patch(sid, pid=os.getpid(), stage="En cola…")
    with _TX_LOCK:
        _do_transcription(sid)


def _do_transcription(sid):
    """Escribe con _patch y no con la copia que leyó al empezar: esto tarda minutos y en ese
    rato la ventana puede estar guardando notas o renombrando."""
    # el dueño es quien transcribe: lo lee _stale para saber si la sesión quedó huérfana
    s = _patch(sid, pid=os.getpid(), stage="Transcribiendo…")
    if not s:                         # la borraron antes de arrancar
        return log.info("sesión %s ya no está", sid)
    st = load_settings()
    chans = [chan_path(sid, "mic"), chan_path(sid, "loop")]
    upd, warn = {}, [s.get("warn")]
    try:
        if not st.get("key"):
            raise RuntimeError("Falta la API key de Groq (Configuración).")
        client = Groq(api_key=st["key"], max_retries=4)
        me = speaker_label(st.get("name")) or MIC_LABEL   # "Eric:" en vez de "Yo:" si lo configuró
        mixed = not st.get("label_speakers", True)
        jobs = ([(chans, "")] if mixed else
                [([p], lbl) for p, lbl, silent in ((chans[0], me, s.get("mic_silent")),
                                                   (chans[1], THEM_LABEL, s.get("loop_silent")))
                 if not silent])
        total = sum(n_chunks(max(map(wav_frames, ps))) for ps, _ in jobs) or 1
        cnt, lk = [0], threading.Lock()

        def done():                   # progreso real: trozos de audio ya transcritos
            with lk:
                cnt[0] += 1; k = cnt[0]
            set_stage(sid, f"Transcribiendo… {k}/{total}")

        with ThreadPoolExecutor(2) as ex:   # los dos canales son llamadas Groq independientes
            futs = [ex.submit(transcribe_channel, client, wav_chunks(ps), lbl, done)
                    for ps, lbl in jobs]
            segs = [seg for f in futs for seg in f.result()]
        if not mixed:
            segs, echo, aporte = drop_echo(segs, me)
            log.info("%s: eco en el micro %.0f%%, aporte propio %.0f%%", sid, echo * 100,
                     aporte * 100)
            if echo > ECHO_DUP and aporte < ECHO_MIN:
                # el micro no trae nada propio, solo restos del eco que se parecían poco:
                # se queda el loopback entero y sin etiquetas, que es el audio de verdad.
                # ponytail: esto tira también lo poco que sí dijiste (medido en la reunión de
                # prueba: 59 de 7752 palabras, 5 frases entre 197 restos de 1 a 3 palabras).
                # Si molesta, conservar acá los segmentos del micro con >=6 palabras: recupera
                # esas frases a cambio de dejar entrar algún fragmento repetido.
                segs = [(t, "", x, *e) for t, lbl, x, *e in segs if lbl != me]
                warn.append("el micrófono captó el audio de la PC, así que no se pudieron "
                            "separar los hablantes (usá auriculares)")
        upd["turns"] = dialog_turns(segs)      # con tiempo: la ventana sincroniza el audio
        upd["transcript"] = format_dialog(segs)   # plano: es lo que se indexa y lo que va a Groq
        # el nombre lo trae el acta (ver split_title); sin nada que resumir, no hay acta
        if not (upd["transcript"] or s.get("notes") or s.get("name")):
            upd["name"] = "Sesión sin audio"
        upd["status"] = "done"
        upd["error"] = upd["retries"] = None
    except Exception as e:
        log.exception("transcripción %s", sid)   # el traceback completo, no los 500 chars
        upd["status"] = "error"
        upd["error"] = humano(e)[:500]   # los canales quedan en disco para reintentar
        upd["retries"] = (s.get("retries") or 0) + 1   # tope de los reintentos automáticos
    ok = upd["status"] == "done"
    upd["warn"] = ", ".join(x for x in warn if x)
    upd["stage"] = "Resumiendo…" if ok else None
    # El resultado va a disco ANTES de tocar los canales: si este patch fallaba después de
    # borrarlos, la sesión quedaba 'pending' con el pid vivo, sin transcripción y sin nada que
    # reintentar. Si falla, los canales siguen ahí y la sesión queda en error reintentable.
    try:
        alive = _patch(sid, **upd)
    except Exception:
        log.exception("guardar la transcripción de %s", sid)
        try:
            _patch(sid, status="error", stage=None, retries=upd.get("retries"),
                   error="No se pudo guardar la transcripción: reintenta.")
        except Exception:
            log.exception("marcar el error de %s", sid)
        return
    if alive and not ok:
        return
    # Éxito, o la borraron mientras se transcribía (alive None): en los dos casos los canales
    # crudos sobran, y el .wav de auditoría solo tiene sentido si la sesión sigue existiendo.
    if alive and st.get("keep_audio"):
        try:                          # guardar el .wav nunca puede tumbar una transcripción buena
            write_mix(os.path.join(NOTAS, f"{sid}.wav"), chans)
        except Exception:
            log.exception("guardar el wav de %s", sid)
            _patch(sid, warn=", ".join(x for x in (upd["warn"], "no se pudo guardar el audio") if x))
    for p in chans:
        try:
            os.remove(p)
        except OSError:               # otra corrida concurrente pudo borrarlo ya
            pass
    if alive:
        auto_summary(sid)


def net_up(host="api.groq.com", port=443, timeout=2):
    """¿Volvió la red? Un TCP contra Groq: es lo único que necesitamos que conteste, y
    responde al instante (un reintento a ciegas sube el audio entero para nada)."""
    try:
        socket.create_connection((host, port), timeout).close()
        return True
    except OSError:
        return False


def retryable_sessions():
    """En error, con el audio crudo todavía en disco y sin agotar los intentos automáticos.
    El tope existe porque no todo error es de red: una key inválida falla igual siempre.
    Se filtra por _view() y no por el status crudo: una sesión que quedó en 'pending' porque se
    cerró la app en medio de la transcripción ahí se ve como error, y ES lo que hay que
    reintentar. Sin esto quedaba en el disco marcada 'pending' para siempre — la ventana la
    mostraba interrumpida, pero el barrido automático nunca la tocaba y había que ir a apretar
    Reintentar a mano (o no darse cuenta y perderla)."""
    for s in iter_sessions():
        if (_view(s).get("status") == "error" and (s.get("retries") or 0) < AUTO_RETRIES
                and any(os.path.exists(chan_path(s["id"], w)) for w in ("mic", "loop"))):
            yield s["id"]


def auto_summary(sid):
    """Resumir es lo primero que uno quiere ver al terminar, así que no espera un clic. Va
    aparte de la transcripción: que Groq falle acá no puede tirar una transcripción buena."""
    try:
        s = load_session(sid)
        if s.get("summary") or not (s.get("transcript") or s.get("notes")):
            return set_stage(sid, "")
        store_summary(sid, summarize_text(chat_client(),
                                          s.get("notes", ""), s.get("transcript", ""),
                                          lambda i, n: set_stage(sid, f"Resumiendo… {i}/{n}"),
                                          cuando(s)))
    except Exception as e:
        log.exception("resumen automático de %s", sid)
        # queda el botón para reintentar. Sin acta tampoco hay título: se pide aparte, que es
        # lo que se hacía siempre antes de sacarlo del acta
        kw = {}
        try:
            s = load_session(sid)
            if not s.get("name"):
                kw["name"] = name_session(chat_client(), s.get("transcript"))
        except Exception:             # borrada, o sin key: el nombre no vale otro traceback
            log.warning("sin nombre para %s", sid, exc_info=True)
        _patch(sid, stage=None, sum_error=humano(e), **kw)


def store_summary(sid, text):
    """Guarda un acta nueva. El título que trae va a la sesión solo si no tenía nombre: el que
    puso el usuario (o una corrida anterior) no se pisa. Las acciones tildadas eran índices del
    acta vieja: con otra acta no significan nada."""
    title, text = split_title(text)
    def fn(s):
        if title and not s.get("name"):
            s["name"] = title
    return _patch(sid, fn, summary=text, stage=None, sum_error=None, done=None)


# ---------- servidor de medios de notas/ (solo en el proceso de la ventana) ----------
MEDIA_BASE = ""        # "http://127.0.0.1:<puerto>/<token>/" — lo pone window_main()
MIMES = {".wav": "audio/wav", ".png": "image/png"}


def byte_range(header, size):
    """Cabecera Range -> (desde, hasta, es_parcial). `hasta` es inclusive, como manda HTTP.
    Un solo rango simple, que es lo único que pide un <audio> al mover la aguja; cualquier otra
    cosa (multi-rango, basura, fuera de tamaño) cae a "el archivo entero", que nunca es incorrecto."""
    m = re.match(r"bytes=(\d*)-(\d*)$", (header or "").strip())
    whole = (0, max(0, size - 1), False)
    if not m or not (m.group(1) or m.group(2)) or size <= 0:
        return whole
    a, b = m.group(1), m.group(2)
    if a:
        start = min(int(a), size - 1)
        end = min(int(b), size - 1) if b else size - 1
    else:
        start, end = max(0, size - int(b)), size - 1     # sufijo: los últimos N bytes
    if end < start:
        return whole
    return start, end, (start, end) != (0, size - 1)


def start_media_server():
    """Sirve los archivos de notas/ por HTTP para el <audio> y las capturas de la ventana.
    Hace falta uno propio: pywebview sirve ui/ con su bottle y no expone la app para colgarle una
    ruta, y un src="file:///…" lo rechaza Chromium por cross-scheme. **Range no es opcional**: sin
    él, mover la aguja obliga a bajar los ~128 MB del WAV antes de oír nada.
    Solo loopback, puerto efímero y un token aleatorio en la ruta: cualquier proceso (o cualquier
    página web) puede llegar a un puerto de 127.0.0.1, así que adivinar el puerto no alcanza.
    Sin cabeceras CORS a propósito: el <audio> reproduce igual y nadie puede leer los bytes."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import unquote
    import secrets
    token = secrets.token_urlsafe(16)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass                  # el .exe no tiene consola; lo que importa ya va al log de la app

        def _resolve(self):
            """Ruta pedida -> archivo real, o None. Solo archivos que estén DIRECTAMENTE en
            notas/: descarta el token que no coincide, los .., las subcarpetas y las absolutas."""
            parts = self.path.lstrip("/").split("?")[0].split("/")
            if len(parts) != 2 or not secrets.compare_digest(   # .encode: la URL puede no ser ASCII
                    parts[0].encode("utf-8", "replace"), token.encode()):
                return None
            p = os.path.join(NOTAS, unquote(parts[1]))
            if os.path.dirname(os.path.abspath(p)) != os.path.abspath(NOTAS):
                return None
            return p if os.path.isfile(p) else None

        def _serve(self, body):
            p = self._resolve()
            if not p:
                self.send_error(404)
                return
            size = os.path.getsize(p)
            start, end, partial = byte_range(self.headers.get("Range"), size)
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type",
                             MIMES.get(os.path.splitext(p)[1].lower(), "application/octet-stream"))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if not body:
                return
            try:
                with open(p, "rb") as f:
                    f.seek(start)
                    left = end - start + 1
                    while left > 0:
                        b = f.read(min(262144, left))
                        if not b:
                            break
                        self.wfile.write(b)
                        left -= len(b)
            except OSError:
                pass              # el navegador corta la conexión en cada salto de la aguja

        def do_GET(self):
            self._serve(True)

        def do_HEAD(self):        # Chromium lo pide antes de decidir si puede hacer Range
            self._serve(False)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("servidor de medios en el puerto %d", srv.server_port)
    return f"http://127.0.0.1:{srv.server_port}/{token}/"


def media_url(path):
    """URL para un archivo de notas/, o "" si no hay servidor (proceso del widget) o no existe."""
    from urllib.parse import quote
    if not MEDIA_BASE or not path or not os.path.exists(path):
        return ""
    return MEDIA_BASE + quote(os.path.basename(path))


# ---------- procesos: liveness y una sola instancia de cada uno ----------
WINDOW_MUTEX = "Local\\TakeMyNotes.window"
WIDGET_MUTEX = "Local\\TakeMyNotes.widget"


@lru_cache(maxsize=1)
def _k32():
    """kernel32 con el restype de HANDLE puesto (el c_int por defecto trunca en 64 bits)."""
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = k.OpenMutexW.restype = k.OpenProcess.restype = ctypes.c_void_p
    k.CloseHandle.argtypes = k.ReleaseMutex.argtypes = [ctypes.c_void_p]
    k.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    return k


def _pid_alive(pid):
    # ponytail: Windows recicla PIDs, así que un PID reusado se ve "vivo". Solo afecta al
    # aviso de sesión interrumpida (se vería "transcribiendo…" de más), no a la ventana única.
    k = _k32()
    h = k.OpenProcess(0x1000, False, pid)                 # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        k.CloseHandle(h)
    return bool(h)


def lock_held(name=WINDOW_MUTEX):
    """¿Hay un proceso vivo con este mutex (la ventana, el widget)? Basta con poder abrirlo."""
    k = _k32()
    h = k.OpenMutexW(0x00100000, False, name)             # SYNCHRONIZE
    if h:
        k.CloseHandle(h)
    return bool(h)


def take_lock(name=WINDOW_MUTEX):
    """Mutex nombrado en vez de un lockfile con PID: el SO destruye el objeto al morir el
    proceso, así que no existen locks huérfanos. Devuelve el handle, o None si ya hay uno."""
    k = _k32()
    h = k.CreateMutexW(None, True, name)
    if h and ctypes.get_last_error() == 183:              # ERROR_ALREADY_EXISTS
        k.CloseHandle(h)
        return None
    return h


def _app_windows(classes=("WindowsForms",)):
    """hwnds de la app en OTROS procesos, por clase de ventana. Por defecto la ventana grande. El título no alcanza para identificarla: el widget se llama
    igual, y una ventana del Explorador abierta en esta carpeta también (se llevaba el foco).
    Se pide además que sea WinForms, que es lo que monta pywebview con WebView2 — el widget es
    TkTopLevel y el Explorador CabinetWClass. Si algún día cambia el backend, el ⤡ deja de
    traerla al frente pero no rompe nada más."""
    u = ctypes.windll.user32
    me, found = os.getpid(), []

    def name_of(fn, hwnd):
        b = ctypes.create_unicode_buffer(256)
        fn(hwnd, b, 256)
        return b.value.strip()

    def cb(hwnd, _):
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if (pid.value != me and u.IsWindowVisible(hwnd)
                and name_of(u.GetWindowTextW, hwnd) == "TakeMyNotes"
                and name_of(u.GetClassNameW, hwnd).startswith(classes)):
            found.append(hwnd)
        return True

    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(cb), 0)
    return found


def _other_window():
    found = _app_windows()
    return found[0] if found else None


def quit_all(wait=15):
    """Cierra el widget y la ventana como si se apretara su ✕: lo usan el acceso «Cerrar
    TakeMyNotes» del menú Inicio y el instalador antes de reemplazar el .exe. WM_CLOSE y no matar
    el proceso: el widget pregunta si está grabando o transcribiendo, igual que con su ✕.
    Espera a que los dos mutex se suelten (el SO los suelta al morir cada proceso)."""
    u = ctypes.windll.user32
    for h in _app_windows(("WindowsForms", "TkTopLevel")):
        u.PostMessageW(h, 0x0010, 0, 0)                             # WM_CLOSE
    t = time.time() + wait
    while time.time() < t and (lock_held(WINDOW_MUTEX) or lock_held(WIDGET_MUTEX)):
        time.sleep(0.2)
    return not (lock_held(WINDOW_MUTEX) or lock_held(WIDGET_MUTEX))


def focus_window():
    """Trae al frente la ventana ya abierta. Sin esto, ⤡ y ⚙ no hacían NADA cuando la
    ventana existía pero estaba detrás de otra: launch_window() salía temprano y listo."""
    h = _other_window()
    if not h:
        return False
    u = ctypes.windll.user32
    u.ShowWindow(h, 9)                                              # SW_RESTORE
    u.SetForegroundWindow(h)                                        # permitido: el clic nos dio foco
    return True


def set_action(a):
    """Deja una acción para la ventana (mismo canal que todo: un archivo)."""
    with open(ACTION, "w", encoding="utf-8") as f:
        f.write(a)


def take_action():
    """La consume quien la lee. Si la ventana ya estaba abierta la ve en el próximo poll."""
    try:
        with open(ACTION, encoding="utf-8") as f:
            a = f.read().strip()
        os.remove(ACTION)
        return a
    except OSError:
        return ""


def launch(*args):
    """Arranca otro proceso de la app: la ventana grande, o el widget de vuelta."""
    cmd = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, os.path.abspath(__file__)]
    subprocess.Popen(cmd + list(args), creationflags=0x08000000)     # CREATE_NO_WINDOW


def launch_window(action=""):
    if action:
        set_action(action)
    if lock_held():
        focus_window()
        return
    launch("--window")


# ---------- WIDGET nativo (Tkinter, transparente) ----------
KEYCOLOR = "#0b0c0e"   # color-llave: se vuelve transparente
LIGHT = {"pill": "#f5f5f7", "edge": "#d2d2d7", "ink": "#1d1d1f", "dim": "#7a7a7a",
         "icon": "#333333", "warn": "#b46b00"}
DARK = {"pill": "#000000", "edge": "#2e2e2e", "ink": "#f5f5f7", "dim": "#a1a1a6",
        "icon": "#e5e5ea", "warn": "#ff9f0a"}
NOSIGNAL = 60             # s grabando sin voz en un canal desde el arranque => avisar en vivo
HOTKEY = (0x0002 | 0x0004 | 0x4000, ord("R"))   # Ctrl+Shift+R (MOD_CONTROL|SHIFT|NOREPEAT)
TIPS = {"rec": "Grabar / detener (Ctrl+Shift+R)", "mic": "Cortar mi micrófono",
        "shot": "Captura de pantalla", "pause": "Pausar", "note": "Notas de la sesión",
        "gear": "Configuración", "expand": "Abrir la ventana", "quit": "Cerrar",
        "lvl": "Nivel: micrófono · audio de la PC"}


EDGES = ("top", "bottom", "left", "right")   # dónde va el notch (settings.json: "edge")


def work_area():
    """(izq, arriba, der, abajo) del escritorio sin la barra de tareas: abajo, el notch se
    apoya encima de ella y no la tapa."""
    r = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)   # SPI_GETWORKAREA
    return r.left, r.top, r.right, r.bottom


def in_pill(x, y, w, h, r):
    """¿(x, y) cae dentro del pill redondeado? Lo usa selftest para verificar que ningún halo de
    botón se salga por una esquina: el ✕ apretado se salía 2 px y se veía como un mordisco.
    Es un notch: plano arriba (las esquinas de arriba quedan en y<0, fuera de la ventana)."""
    x1, y1, x2, y2 = 0, -r, w, h - 1
    cx, cy = min(max(x, x1 + r), x2 - r), min(max(y, y1 + r), y2 - r)
    if cx != x and cy != y:                  # zona de esquina: distancia al centro del arco
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    return x1 <= x <= x2 and y1 <= y <= y2


def shade(color, f):
    """Aclara (f>1) u oscurece (f<1) un #rrggbb. Para el feedback de presionado: en tema
    oscuro hay que aclarar, oscurecer un icono ya oscuro no se ve."""
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(min(255, int(v * f)) for v in (r, g, b))


def dark_mode(theme):
    """'auto' se lo pregunta a Windows (AppsUseLightTheme); claro si el registro no contesta."""
    if theme in ("dark", "light"):
        return theme == "dark"
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return not winreg.QueryValueEx(k, "AppsUseLightTheme")[0]
    except OSError:
        return False


class Widget:
    """Pill flotante. Distribución fija a propósito: los botones de sesión (micro, cámara, pausa)
    se dibujan SIEMPRE y solo se apagan cuando no se está grabando. Antes aparecían y
    desaparecían, y el pill quedaba con un hueco de 78 px que se veía disparejo."""
    W, H, R = 400, 54, 20               # R: radio de las esquinas de abajo, lo usa in_pill()
    EAR = 12                            # radio de la oreja cóncava a cada lado, fuera del cuerpo
    TAB_W, TAB_H = 79, 10               # plegado: la pestañita (codenotch: #rest)
    ANIM = 360                          # ms del plegado/desplegado
    PEEK = 2500                         # ms que queda abierto solo cuando hay algo nuevo
    FOLD_DELAY = 700                    # ms sin el mouse encima antes de plegarse

    NEEDS_REC = ("mic", "shot", "pause")    # inertes sin grabación en curso (ver _hit)
    # x de cada control. Un solo sitio: el dibujo y los tests salen de acá, y selftest verifica
    # que ningún halo se pise con otro ni se salga del pill redondeado.
    GRIP, DOT, CLOCK = 18, 34, 46
    LVL = 116                           # barritas de nivel (micro, PC), a la derecha del reloj
    MIC, SHOT, PAUSE = 162, 188, 214    # controles de la sesión
    SEP = 234                           # separador, centrado entre los dos grupos de halos
    NOTE, GEAR, EXPAND = 254, 280, 306  # controles de la app
    REC, QUIT = 340, 376
    HALO, RECR = 13, 15                 # radios: halo de un botón chico, y el círculo de grabar
    # En los lados el notch es vertical (54 de ancho): los controles van en columna en las
    # mismas posiciones, pero el reloj, las barritas y el estado se apilan en vez de ir en fila.
    VCLOCK, VLVL, VSUB = 52, 66, 78

    def __init__(self):
        import tkinter as tk
        self.tk = tk
        self.root = tk.Tk()
        self.root.title("TakeMyNotes")
        self.root.protocol("WM_DELETE_WINDOW", lambda: self._quit())   # WM_CLOSE de quit_all
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", KEYCOLOR)
        self.root.config(bg=KEYCOLOR)
        # La ventana es SIEMPRE del tamaño abierto (más las orejas): plegado solo cambia lo
        # dibujado, y el color-llave es click-through. Tamaño y lugar los pone _place().
        self.c = tk.Canvas(self.root, bg=KEYCOLOR, highlightthickness=0)
        self.c.pack()
        self.edge = None              # borde de la pantalla: lo lee _read_theme de settings.json
        self._open = 0.0              # 0 plegado … 1 abierto (fracción, para la animación)
        self._anim = None             # job de after() de la animación en curso
        self._fold_job = None         # job de after() que va a plegar
        self._pinned = False          # clic derecho: no se pliega («Keep open» de codenotch)
        self._news = None             # lo último que hubo que mostrar: si cambia, _peek()
        self.recording = False
        self.rec = self.rec_id = None
        self.t0 = 0
        self.pause_t0 = 0             # instante en que se pausó (0 = grabando normal)
        self.jobs = 0                 # transcripciones en curso o en cola en este proceso
        self._dragging = False        # arrastrando: no se repinta (ver _loop)
        self.label = self.idle = ""
        self.notes_win = self.notes_box = None
        self._st_mtime = -1           # mtime de settings.json: -1 fuerza leer el tema al arrancar
        self._pal = LIGHT
        self._shown = None            # lo último dibujado, para repintar solo si cambió
        self._pressed = None          # tag apretado: se dibuja hundido hasta que suelten
        self._hot = False             # se apretó el atajo global (lo levanta otro hilo)
        self._tip = self._tip_tag = self._tip_job = None
        self._read_theme()
        self._draw()
        self._btn("rec", self.toggle)
        self._btn("pause", self._pause)
        self._btn("mic", self._mute)
        self._btn("shot", self._shot)
        self._btn("keep", self._keep)
        self._btn("note", self._open_notes)
        self._btn("gear", lambda: launch_window("settings"))
        self._btn("expand", launch_window)
        self._btn("quit", self._quit)
        for t in ("drag", "pill"):
            self.c.tag_bind(t, "<ButtonPress-1>", self._press)
            self.c.tag_bind(t, "<B1-Motion>", self._move)
            self.c.tag_bind(t, "<Button-3>", self._pin)
        self.root.bind("<ButtonRelease-1>", self._drop)
        self.c.bind("<Motion>", self._motion)
        self.c.bind("<Enter>", lambda e: self._unfold())
        self.c.bind("<Leave>", lambda e: self._set_tip(None))
        threading.Thread(target=self._auto_retry, daemon=True).start()
        threading.Thread(target=self._hotkey, daemon=True).start()
        self._loop()                  # el primer tick hace _peek(): se ve que arrancó
        self._hover()

    # --- atajo global ---
    def _hotkey(self):
        """Ctrl+Shift+R graba/detiene desde cualquier app. RegisterHotKey manda WM_HOTKEY a la
        cola del hilo que lo registró, y el mainloop de Tk la descartaría: por eso un hilo propio
        con su bucle de mensajes, que solo levanta un flag; _loop lo atiende en el hilo de Tk.
        # ponytail: atajo fijo. Si otra app lo tiene tomado, no hay atajo (queda en el log);
        # hacerlo configurable cuando alguien lo pida."""
        u = ctypes.windll.user32
        if not u.RegisterHotKey(None, 1, HOTKEY[0], HOTKEY[1]):
            return log.warning("el atajo global ya lo usa otra app")
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:                           # WM_HOTKEY
                self._hot = True

    # --- tooltips ---
    def _tip_at(self, a, d):
        """Qué control hay en (a, d) —a lo largo del notch, distancia al borde; ver _at—. Por
        coordenadas y no por <Enter> en los items: grabando, _draw() los borra y recrea cada
        500 ms y el tooltip parpadearía. Plegado o animando no hay controles."""
        if self._dragging or self._open < 1:
            return None
        if abs(a - self.REC) <= self.RECR:
            return "rec"
        if 14 <= d <= 40:
            for tag in ("mic", "shot", "pause", "note", "gear", "expand", "quit"):
                if abs(a - getattr(self, tag.upper())) <= self.HALO:
                    return tag
        lvl = self._ax("lvl")
        if self.recording and lvl - 6 <= a <= lvl + 6:
            return "lvl"
        return None

    def _motion(self, e):
        self._set_tip(self._tip_at(*self._from(e.x, e.y)))

    def _set_tip(self, tag):
        """Cambia el tooltip pendiente o visible. Aparece a los 600 ms de quedarse encima."""
        if tag == self._tip_tag:
            return
        if self._tip_job:
            self.root.after_cancel(self._tip_job)
        if self._tip:
            self._tip.destroy()
        self._tip = self._tip_job = None
        self._tip_tag = tag
        if tag:
            self._tip_job = self.root.after(600, self._show_tip)

    def _show_tip(self):
        tk, p, tag = self.tk, self._pal, self._tip_tag
        self._tip_job = None
        w = self._tip = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        tk.Label(w, text=TIPS[tag], bg=p["ink"], fg=p["pill"], font=("Segoe UI", 8),
                 padx=6, pady=2).pack()
        w.update_idletasks()
        x, y = self._beside(self._ax(tag), w.winfo_reqwidth(), w.winfo_reqheight(), 4)
        w.geometry(f"+{x}+{y}")

    # --- botones ---
    def _btn(self, tag, fn):
        """El clic actúa en el PRESS a propósito: si se redibuja ahí y se espera el
        <ButtonRelease>, el item que recibió el press ya fue borrado por _draw() y Tk nunca
        entrega el release al nuevo, así que el clic se perdía. El feedback se quita solo."""
        self.c.tag_bind(tag, "<Button-1>", lambda e: self._hit(tag, fn))

    def _hit(self, tag, fn):
        self._set_tip(None)
        if tag in self.NEEDS_REC and not self.recording:
            # el botón está dibujado pero apagado: decir por qué es mejor que no pasar nada
            return self._flash("dale grabar primero")
        self._pressed = tag
        self._draw()                        # hundido: se ve que el clic entró
        self.root.after(140, self._unpress)
        fn()

    def _flash(self, msg, ms=2000):
        """Mensaje efímero en la segunda línea del pill, que se borra solo."""
        self.label = msg
        self.root.after(ms, lambda: self._clear(msg))
        self._draw()

    def _unpress(self):
        self._pressed = None
        self._draw()

    # --- tema ---
    def _read_theme(self):
        """Relee el tema si settings.json cambió (un stat por tick, gratis) para que cambiarlo
        en la ventana se vea en el widget sin reiniciarlo."""
        try:
            mt = os.path.getmtime(SETTINGS_PATH)
        except OSError:
            mt = 0
        if mt == self._st_mtime:
            return
        self._st_mtime = mt
        st = load_settings()
        self._pal = DARK if dark_mode(st.get("theme", "auto")) else LIGHT
        edge = st.get("edge") if st.get("edge") in EDGES else "top"
        if edge != self.edge:
            self.edge = edge
            self._place()

    # --- geometría: en qué borde está el notch ---
    def _vertical(self):
        return self.edge in ("left", "right")

    def _size(self):
        """Ventana (ancho, alto): el notch abierto más una oreja a cada lado."""
        n = self.W + 2 * self.EAR
        return (self.H, n) if self._vertical() else (n, self.H)

    def _place(self):
        """Al centro del borde elegido, dentro del área de trabajo (sin la barra de tareas)."""
        (w, h), (l, t, r, b), E = self._size(), work_area(), self.EAR
        x = {"left": l, "right": r - w}.get(self.edge, (l + r - self.W) // 2 - E)
        y = {"top": t, "bottom": b - h}.get(self.edge, (t + b - self.W) // 2 - E)
        self.c.config(width=w, height=h)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _at(self, a, d, shape=True):
        """(a, d) del notch → (x, y) del canvas. `a` corre a lo largo del borde (0…W: son las x
        de los controles, GRIP…QUIT) y `d` se aleja de él (0…H). Las orejas caen en a<0 y a>W,
        por eso el +EAR. shape=True refleja d abajo y a la derecha, para la forma; los
        controles no se reflejan (el reloj queda arriba del estado en los cuatro bordes)."""
        a += self.EAR
        if shape and self.edge in ("bottom", "right"):
            d = self.H - d
        return (d, a) if self._vertical() else (a, d)

    def _pt(self, a, d):
        return self._at(a, d, shape=False)

    def _from(self, x, y):
        """Inversa de _pt: de un evento del canvas a (a, d)."""
        a, d = (y, x) if self._vertical() else (x, y)
        return a - self.EAR, d

    def _ax(self, tag):
        """`a` del centro de un control (las barritas cambian de lugar en vertical)."""
        if tag == "lvl":
            return self.VLVL if self._vertical() else self.LVL + 3
        return getattr(self, tag.upper())

    def _beside(self, a, w, h, gap):
        """Pantalla (x, y) para una ventana w×h pegada al notch del lado de adentro (hacia el
        centro de la pantalla), centrada en el punto `a` del notch."""
        wx, wy, E = self.root.winfo_x(), self.root.winfo_y(), self.EAR
        if self._vertical():
            x = wx + self.H + gap if self.edge == "left" else wx - w - gap
            y = wy + E + a - h // 2
        else:
            x = wx + E + a - w // 2
            y = wy + self.H + gap if self.edge == "top" else wy - h - gap
        return max(0, x), max(0, y)

    # --- notas escritas ---
    def _open_notes(self):
        """Bloc de notas: lo que escribas se guarda en la sesión que estés grabando (o en la
        próxima). Es una ventana Tk aparte, no toca la ventana grande."""
        tk = self.tk
        if self.notes_win and self.notes_win.winfo_exists():
            self.notes_win.deiconify(); self.notes_win.lift(); self.notes_box.focus_set()
            return
        p = self._pal
        w = self.notes_win = tk.Toplevel(self.root)
        w.title("Notas de la sesión")
        w.attributes("-topmost", True)
        # 280 de alto con la barra de título: abajo no se mete debajo del notch
        x, y = self._beside(140 if self._vertical() else 170, 340, 280, 10)
        w.geometry(f"340x240+{x}+{y}")
        w.config(bg=p["pill"])
        tk.Label(w, text="Se guardan en la sesión al detener la grabación.", bg=p["pill"],
                 fg=p["dim"], font=("Segoe UI", 8)).pack(pady=(8, 0))
        self.notes_box = tk.Text(w, wrap="word", font=("Segoe UI", 10), bd=0, padx=10, pady=8,
                                 bg=p["pill"], fg=p["ink"], insertbackground=p["ink"])
        self.notes_box.pack(fill="both", expand=True)
        self.notes_box.focus_set()
        w.protocol("WM_DELETE_WINDOW", lambda: w.withdraw())   # withdraw, no destroy: el texto vive

    def _take_notes(self):
        """Texto actual y vacía el bloc: se va con la sesión. Solo desde el hilo de UI (Tk)."""
        if not (self.notes_box and self.notes_win.winfo_exists()):
            return ""
        t = self.notes_box.get("1.0", "end").strip()
        self.notes_box.delete("1.0", "end")
        return t

    def _quit(self):
        if self.recording:
            from tkinter import messagebox
            if not messagebox.askyesno("TakeMyNotes", "Estás grabando.\n¿Detener, guardar y cerrar?\n\n"
                                       "La sesión queda sin transcribir: se reintenta desde la ventana."):
                return
            rec, sid, self.rec = self.rec, self.rec_id, None
            self.recording = False
            self._save_session(rec, sid, self._take_notes())   # síncrono: el JSON no puede perderse
        if self.jobs:
            from tkinter import messagebox
            if not messagebox.askyesno("TakeMyNotes", f"Faltan {self.jobs} transcripción(es) por "
                                       "terminar.\n¿Cerrar igual?\n\nEl audio queda en disco: se "
                                       "reintentan solas la próxima vez que abras la app."):
                return
        # after_idle y no destroy() directo: _quit corre dentro del <Button-1> de un item del
        # canvas, y destruir el canvas mientras Tk despacha ese evento corrompe la memoria de
        # Tcl ("alloc: invalid block", la app se cae con código 3 al cerrar con la ✕).
        self.root.after_idle(self.root.destroy)

    def _round(self, x1, y1, x2, y2, r, **kw):
        p = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
             x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.c.create_polygon(p, smooth=True, **kw)

    def _fmt(self):
        s = int((self.pause_t0 or time.time()) - self.t0) if self.recording else 0
        return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"

    def _muted(self):
        return bool(self.rec and self.rec.mute_evt.is_set())

    def _levels(self):
        """(micro, PC): ¿el último bloque de cada canal tuvo voz? Las barritas junto al reloj."""
        r = self.rec if self.recording else None
        return (bool(r and r.mic_level > VOICE and not r.mute_evt.is_set()),
                bool(r and r.loop_level > VOICE))

    def _no_signal(self):
        """Aviso en vivo de canal mudo: NOSIGNAL s grabando sin UN bloque con voz desde el
        arranque. Antes esto se sabía al terminar, con la reunión ya perdida."""
        r = self.rec
        if not (self.recording and r) or self.pause_t0 or time.time() - self.t0 < NOSIGNAL:
            return ""
        if not r.loop_voice:
            return "sin audio de la PC"
        if not r.mic_voice and not r.mic_off:
            return "micrófono sin señal"
        return ""

    def _state(self):
        """Lo que se ve. Si dos valores iguales, no hace falta repintar (lo mira _loop)."""
        return (self.recording, self.label, self.idle, self._pal is DARK, self._pressed,
                bool(self.pause_t0), self.jobs, self._muted(), self._levels(), self._no_signal(),
                round(self._open, 2), self._pinned, self.edge)

    # --- notch: plegar y desplegar ---
    def _body(self, dot):
        """Plano del lado del borde (esas esquinas caen fuera de la ventana) y redondeado del
        otro. Plegado es la pestañita con el punto de estado; animando, un cuerpo interpolado;
        abierto suma las orejas cóncavas donde toca el borde de la pantalla. Se dibuja en
        (a, d) y _at lo lleva al borde que toque. Devuelve True solo abierto del todo."""
        c, p, at = self.c, self._pal, self._at

        def box(a1, d1, a2, d2):
            (x1, y1), (x2, y2) = at(a1, d1), at(a2, d2)
            return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)

        t = 1 - (1 - self._open) ** 3                   # ease-out
        w = self.TAB_W + (self.W - self.TAB_W) * t
        h = self.TAB_H + (self.H - self.TAB_H) * t
        r = 6 + (self.R - 6) * t
        a1 = (self.W - w) / 2
        self._round(*box(a1, -r, a1 + w, h - 1), r, fill=p["pill"], outline=p["edge"],
                    tags="pill")
        if self._open == 0:
            if dot != "#9a9aa0":                        # parado: sin punto
                c.create_oval(*box(self.W / 2 - 2, 2, self.W / 2 + 3, 7), fill=dot, outline="",
                              tags="pill")
            return False
        if self._open < 1:
            return False
        E = self.EAR
        # Oreja: un cuadrado del color del pill tapa el borde lateral del cuerpo, y un cuarto
        # de círculo color-llave le recorta la curva cóncava; el arco pinta el borde encima.
        for a, ca, side in ((-E, -E, 1), (self.W, self.W + E, -1)):
            c.create_rectangle(*box(a, 0, a + E + 1, E), fill=p["pill"], outline="", tags="pill")
            (cx, cy), (bx, by), (ex, ey) = at(ca, E), at(ca + side, E), at(ca, E - 1)
            # el cuarto que mira hacia el cuerpo y hacia el borde (ángulos de Tk: y para arriba)
            q = {(1, -1): 0, (-1, -1): 90, (-1, 1): 180, (1, 1): 270}[
                (bx - cx + ex - cx, by - cy + ey - cy)]
            oval = (cx - E, cy - E, cx + E, cy + E)
            c.create_arc(*oval, start=q, extent=90, style="pieslice", fill=KEYCOLOR, outline="")
            c.create_arc(*oval, start=q, extent=90, style="arc", outline=p["edge"])
        return True

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
        """Pasos de 16 ms hasta target (0 o 1). instant=True salta al final (selftest).
        Arrastrando no se repinta (ver _loop): espera a que suelten."""
        if self._anim:
            self.root.after_cancel(self._anim)
            self._anim = None
        if instant:
            self._open = target
        elif not self._dragging:
            step = 16 / self.ANIM
            self._open = min(self._open + step, 1) if target else max(self._open - step, 0)
        if self._open != target:
            self._anim = self.root.after(16, lambda: self._animate(target))
        if not self._dragging:
            self._draw()

    def _hover(self):
        """Sondea el puntero (GetCursorPos) en vez de fiarse de <Leave>: con color-llave el
        mouse «sale» de la ventana en cada píxel transparente. Diez lecturas por segundo."""
        x, y = self.root.winfo_pointerxy()
        wx, wy = self.root.winfo_x(), self.root.winfo_y()
        if self._open:
            x1, y1, (x2, y2) = 0, 0, self._size()
        else:                                           # la pestañita ± 24 px: «al acercarse»
            (ax, ay), (bx, by) = (self._at((self.W - self.TAB_W) / 2, 0),
                                  self._at((self.W + self.TAB_W) / 2, self.TAB_H))
            x1, y1 = min(ax, bx) - 24, min(ay, by) - 24
            x2, y2 = max(ax, bx) + 24, max(ay, by) + 24
        if wx + x1 <= x <= wx + x2 and wy + y1 <= y <= wy + y2:
            self._unfold()
        elif self._open and not self._fold_job:
            self._arm_fold(self.FOLD_DELAY)
        self.root.after(100, self._hover)

    def _draw(self):
        c, p = self.c, self._pal
        c.delete("all")
        dot = (p["warn"] if self.idle == "warn" else
               "#ff3b30" if self.recording and not self.pause_t0 else "#9a9aa0")
        if not self._body(dot):
            self._shown = self._state()
            return
        if self._pinned and dot == "#9a9aa0":
            dot = p["ink"]                              # fijado: que se note
        V, P = self._vertical(), self._pt

        def base(a):
            """(bx, by) tal que el icono del control `a` va en (bx + dx, by + y): en fila y es
            la de siempre (14…40); en columna el icono se corre para centrarse en su lugar."""
            x, y = P(a, 27)
            return x, y - 27

        # Asa de arrastre: dos columnas de puntitos. Sin algo explícito, el único sitio para
        # agarrar el pill era el hueco entre los iconos, que grabando casi no existe.
        for ga in (self.GRIP, self.GRIP + 4):
            for gd in (21, 27, 33):
                x, y = P(ga, gd)
                c.create_oval(x - 1, y - 1, x + 1, y + 1, fill=p["dim"], outline="", tags="drag")
        # El estado va en una segunda línea debajo del reloj (al lado le quedaban 47 px y
        # "transcribiendo 2" no entraba). Cuando NO hay estado —lo normal— el reloj se centra
        # con los botones en vez de quedarse flotando arriba con un hueco vacío abajo.
        # En vertical va todo apilado y centrado: reloj, barritas y el estado en letra chica.
        mudo = self._no_signal()
        sub = ("¿seguir grabando?" if self.idle == "warn" else
               "en pausa" if self.pause_t0 else
               self.label or mudo or (f"transcribiendo {self.jobs}" if self.jobs else ""))
        cy = 27 if V or not sub else 20
        x, y = P(self.DOT, cy)
        c.create_oval(x - 4, y - 4, x + 4, y + 4, fill=dot, outline="", tags="drag")
        if V:
            c.create_text(*P(self.VCLOCK, 27), text=self._fmt(), font=("Segoe UI", 9, "bold"),
                          fill=p["ink"], tags="drag")
        else:
            c.create_text(*P(self.CLOCK, cy), anchor="w", text=self._fmt(),
                          font=("Segoe UI", 12, "bold"), fill=p["ink"], tags="drag")
        if self.recording:            # nivel en vivo: micro y PC, verdes cuando entra voz
            for dx, on in zip((0, 5), self._levels()):
                x, y = P(self.VLVL, 24 + dx) if V else P(self.LVL + dx, cy)
                c.create_rectangle(x, y - 6, x + 3, y + 6,
                                   fill="#34c759" if on else p["edge"], outline="", tags="drag")
        if sub:                       # 46..149 de ancho: el halo del micro arranca en 149
            warn = self.idle == "warn"
            font = ("Segoe UI", 7 if V else 9 if warn else 8, "bold" if warn else "normal")
            kw = dict(anchor="n", width=50, justify="center") if V else dict(anchor="w")
            c.create_text(*(P(self.VSUB, 27) if V else P(self.CLOCK, 38)), text=sub, font=font,
                          fill=p["warn"] if warn or sub == mudo else p["dim"],
                          tags="keep" if warn else "drag", **kw)
        press = 1.35 if p is DARK else 0.75          # apretado: aclarar en oscuro, oscurecer en claro

        def halo(a, tag, col=None):
            """Fondo hundido del botón (se ve que el clic entró) y el color del icono resuelto.
            Un tag de NEEDS_REC sin grabación en curso sale apagado: el boton se sigue viendo
            —el pill no cambia de forma— pero se lee que ahora no hace nada.
            Devuelve (icono, fondo): el lente de la cámara es un agujero y hay que pintarlo del
            fondo que quedó, hundido o no."""
            col = col or p["icon"]
            if tag in self.NEEDS_REC and not self.recording:
                return p["edge"], p["pill"]              # apagado: ni halo ni acción
            if self._pressed != tag:
                return col, p["pill"]
            bg = shade(p["pill"], press)
            x, y = base(a)
            c.create_oval(x - self.HALO, y + 14, x + self.HALO, y + 40, fill=bg, outline="",
                          tags=tag)
            return shade(col, press), bg

        # Micro / cámara / pausa: dibujados y no glyph, que no todas las Segoe UI los traen.
        # Se dibujan SIEMPRE (apagados si no se graba): antes aparecían y desaparecían y el
        # pill quedaba con un hueco de 78 px que se veía disparejo.
        mc = "#ff3b30" if self._muted() else halo(self.MIC, "mic")[0]
        x, y = base(self.MIC)
        c.create_oval(x - 4, y + 16, x + 4, y + 28, fill=mc, outline="", tags="mic")
        c.create_line(x, y + 28, x, y + 34, fill=mc, width=2, tags="mic")
        c.create_line(x - 5, y + 34, x + 5, y + 34, fill=mc, width=2, tags="mic")
        if self._muted():
            c.create_line(x - 9, y + 37, x + 9, y + 15, fill=mc, width=2, tags="mic")
        sc, bg = halo(self.SHOT, "shot")
        x, y = base(self.SHOT)
        c.create_rectangle(x - 8, y + 22, x + 8, y + 34, fill=sc, outline="", tags="shot")
        c.create_rectangle(x - 4, y + 19, x + 4, y + 22, fill=sc, outline="", tags="shot")
        c.create_oval(x - 3, y + 25, x + 3, y + 31, fill=bg, outline="", tags="shot")
        pc, _ = halo(self.PAUSE, "pause")
        x, y = base(self.PAUSE)
        if self.pause_t0:
            c.create_polygon(x - 4, y + 20, x - 4, y + 34, x + 8, y + 27,
                             fill=pc, outline="", tags="pause")
        else:
            for dx in (-5, 2):
                c.create_rectangle(x + dx, y + 20, x + dx + 3, y + 34, fill=pc,
                                   outline="", tags="pause")
        # separador: a la izquierda los controles de la sesión, a la derecha los de la app.
        # Sin él son seis iconos seguidos y no se lee que son dos grupos distintos.
        c.create_line(*P(self.SEP, 19), *P(self.SEP, 35), fill=p["edge"], tags="drag")
        for a, glyph, size, tag, col in ((self.NOTE, "✎", 15, "note", p["icon"]),
                                         (self.GEAR, "⚙", 13, "gear", p["icon"]),
                                         (self.EXPAND, "⤡", 12, "expand", p["icon"]),
                                         (self.QUIT, "✕", 11, "quit", p["dim"])):
            g, _ = halo(a, tag, col)      # el ✕ es p["dim"], no p["icon"]
            x, y = base(a)
            c.create_text(x, y + (27 if tag != "quit" else 26), text=glyph,
                          font=("Segoe UI", size), fill=g, tags=tag)
        rc = "#ff3b30" if self.recording else "#0066cc"
        if self._pressed == "rec":
            rc = shade(rc, 0.78)
        r = self.RECR - 2 if self._pressed == "rec" else self.RECR   # y se encoge un poco
        x, y = base(self.REC)
        c.create_oval(x - r, y + 26 - r, x + r, y + 26 + r, fill=rc, outline="", tags="rec")
        if self.recording:
            c.create_rectangle(x - 5, y + 21, x + 5, y + 31, fill="#fff", outline="", tags="rec")
        else:
            c.create_oval(x - 5, y + 21, x + 5, y + 31, fill="#fff", outline="", tags="rec")
        self._shown = self._state()

    def _press(self, e):
        self._dragging = True
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _move(self, e):
        """Solo se desliza a lo largo de su borde, sin salirse del área de trabajo."""
        (l, t, r, b), E = work_area(), self.EAR
        if self._vertical():
            x, y = self.root.winfo_x(), min(max(e.y_root - self._dy, t - E), b - self.W - E)
        else:
            x, y = min(max(e.x_root - self._dx, l - E), r - self.W - E), self.root.winfo_y()
        self.root.geometry(f"+{x}+{y}")

    def _pin(self, _e=None):
        """Clic derecho: queda fijo abierto (o lo suelta)."""
        self._pinned = not self._pinned
        self._unfold()
        self._draw()

    def _drop(self, _e=None):
        """El release se escucha en la ventana y no en un tag del canvas: mientras se arrastra
        no se repinta, pero si igual se perdiera el item, el flag tiene que soltarse."""
        self._dragging = False

    def _keep(self):
        """Clic en "¿seguir?": la reunión sigue viva aunque nadie hable."""
        if self.rec:
            self.rec.last_sound = time.time()
        self.idle = ""; self._draw()

    def _mute(self):
        """Corta TU micro sin parar la reunión (los demás se siguen grabando por el loopback).
        Igual que la pausa, el canal se sigue leyendo pero no se escribe: ese tramo no existe ni
        en el audio ni en la transcripción, así que tampoco se sube a Groq."""
        if not (self.recording and self.rec):
            return
        if self.rec.mute_evt.is_set():
            self.rec.mute_evt.clear()
        else:
            self.rec.mute_evt.set()
            self.rec.mic_off = True      # para que el aviso diga "desactivado" y no "mudo"
        self._draw()

    def _clear(self, msg):
        """Borra un mensaje efímero solo si sigue siendo el que se puso (no pisa uno más nuevo)."""
        if self.label == msg:
            self.label = ""

    def _shot(self):
        """Captura del escritorio a notas/<sid>_shotNN.png. Solo grabando: fuera de una sesión no
        hay dónde colgarla. Va en el hilo de UI a propósito (~200 ms): la alternativa era tocar
        Tk desde un hilo de fondo para esconder el widget, y eso no se puede."""
        if not (self.recording and self.rec_id):
            return
        sid, n = self.rec_id, len(shots(self.rec_id)) + 1
        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.root.withdraw(); self.root.update()   # que el propio widget no salga en la captura
        try:
            grab_png(os.path.join(NOTAS, f"{sid}_shot{n:02d}.png"))
            msg = f"captura {n} ✓"
        except Exception:
            log.exception("captura de pantalla")
            msg = "error en la captura"
        finally:
            # deiconify puede no devolver el topmost ni la posición de una overrideredirect:
            # se reponen las dos, que es más corto que averiguar cuándo pasa
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.geometry(f"+{x}+{y}")
        self._flash(msg, 2500)

    def _auto_retry(self):
        """Sesiones que fallaron por red: se reintentan solas cuando la red vuelve. Corre en
        el widget, que es el proceso que siempre está vivo; el botón Reintentar de la ventana
        sigue estando para forzarlo (y para cuando se agotaron los AUTO_RETRIES). Los dos
        reclaman con claim(): el que llega segundo ve la sesión ya 'pending' y no arranca."""
        while True:
            time.sleep(AUTO_RETRY)
            try:
                sids = list(retryable_sessions())
                if not sids or not net_up():
                    continue
                for sid in sids:
                    if claim(sid):
                        log.info("reintento automático de %s", sid)
                        self.jobs += 1        # el pill lo cuenta igual que una recién grabada
                        try:
                            do_transcription(sid)
                        finally:
                            self.jobs -= 1    # el tick de _loop lo repinta
            except Exception:
                log.exception("reintento automático")

    def _pause(self):
        """Pausa la captura: los dos canales dejan de escribir al WAV y el cronómetro se
        congela, así que el silencio de la pausa no entra en el audio ni en la duración.
        Al reanudar se resetea last_sound: la pausa no cuenta para el aviso de inactividad."""
        if not (self.recording and self.rec):
            return
        if self.pause_t0:
            self.t0 += time.time() - self.pause_t0    # el reloj no cuenta lo pausado
            self.pause_t0 = 0
            self.rec.last_sound = time.time()
            self.rec.pause_evt.clear()
        else:
            self.pause_t0 = time.time()
            self.rec.pause_evt.set()
        self.idle = ""
        self._draw()

    def toggle(self, idle_stop=False):
        if not self.recording:
            os.makedirs(NOTAS, exist_ok=True)
            self.rec_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.rec = Recorder(chan_path(self.rec_id, "mic"), chan_path(self.rec_id, "loop"))
            try:
                self.rec.start()
            except Exception:
                log.exception("no se pudo abrir el audio")
                self.rec = None; self.label = "error audio"; self._draw(); return
            self.recording = True; self.t0 = time.time(); self.pause_t0 = 0
            self.label = self.idle = ""; self._draw()
        else:
            self.recording = False; self.idle = ""; self.pause_t0 = 0
            rec, sid, self.rec = self.rec, self.rec_id, None
            notes = self._take_notes()        # leer el bloc acá: Tk solo desde su hilo
            # jobs se sube ACÁ y no dentro del hilo: entre el start() y la primera línea del
            # hilo el pill diría "listo" durante un tick, justo cuando el usuario mira
            self.jobs += 1
            self.label = ""; self._draw()
            threading.Thread(target=self._finish, args=(rec, sid, notes, idle_stop),
                             daemon=True).start()

    def _finish(self, rec, sid, notes="", idle_stop=False):
        """En un hilo de fondo: el botón de grabar queda libre desde ya, así que se puede arrancar
        la reunión siguiente mientras esta se transcribe (do_transcription las hace de a una)."""
        try:
            self._save_session(rec, sid, notes, idle_stop)
            launch_window()
            do_transcription(sid)
        finally:
            self.jobs -= 1                    # el tick de _loop lo repinta

    def _save_session(self, rec, sid, notes="", idle_stop=False):
        rec.finish()
        mic_silent = canal_mudo(rec.mic_voice, rec.mic_blocks)
        loop_silent = canal_mudo(rec.loop_voice, rec.loop_blocks)
        warn = [m for m, silent in
                (("micrófono desactivado" if rec.mic_off else "micrófono mudo", mic_silent),
                 ("audio de la PC mudo", loop_silent)) if silent]
        if rec.errors:
            warn.append("error de captura de audio")
        if idle_stop:
            warn.append("detenida por inactividad")
        # La hora de la sesión es la de INICIO, que es justo lo que ya lleva el sid. Esto se
        # guarda al parar, así que con now() una junta de una hora salía listada una hora tarde.
        t0 = session_start(sid)
        dur = max(wav_frames(chan_path(sid, "mic")), wav_frames(chan_path(sid, "loop"))) // SR
        store_session({"id": sid, "name": "", "date": t0.strftime("%Y-%m-%d"),
                       "time": t0.strftime("%H:%M"), "dur": dur, "status": "pending",
                       "pid": os.getpid(),   # si este proceso muere, la sesión se ve interrumpida
                       "transcript": "", "notes": notes, "summary": "", "chat": [],
                       "warn": ", ".join(warn),
                       "mic_silent": mic_silent, "loop_silent": loop_silent})

    def _loop(self):
        """Único sitio que dibuja fuera de los clics: Tk no es thread-safe, así que los hilos
        de fondo solo cambian self.label y este tick lo repinta."""
        self._read_theme()
        # Mientras se arrastra NO se repinta. _draw() hace delete("all"), y Tk entrega los
        # <B1-Motion> al item que recibió el press: si ese item se borra, el arrastre se corta en
        # seco. Grabando, el cronómetro repintaba cada 500 ms, y de ahí que casi no se pudiera
        # mover el pill mientras grababa (parado no se repinta y por eso ahí sí andaba).
        if self._dragging:
            return self.root.after(500, self._loop)
        if self._hot:                 # el atajo global, atendido acá: Tk solo desde su hilo
            self._hot = False
            self.toggle()
        if self.recording and self.rec:
            # en pausa nadie habla por definición: no dispara el aviso ni el auto-stop
            self.idle = "" if self.pause_t0 else idle_state(time.time() - self.rec.last_sound)
            if self.idle == "stop":       # nadie contestó al aviso: cerrar y transcribir lo grabado
                self.toggle(idle_stop=True)
            else:
                self._draw()              # el cronómetro corre
        elif self._shown != self._state():
            self._draw()                  # cambió el tema, o terminó de transcribir
        # Algo nuevo que ver (arrancó/paró, mensaje, aviso, terminó de transcribir): se abre
        # solo. Sin el reloj ni las barritas de nivel, que grabando cambian a cada tick.
        news = (self.recording, self.label, self.idle, bool(self.pause_t0), self.jobs,
                self._muted(), self._no_signal())
        if news != self._news:
            self._news = news
            self._peek()
        self.root.after(500, self._loop)

    def run(self):
        self.root.mainloop()


# ---------- API de la ventana (pywebview) ----------
class Api:
    def _client(self):
        """El de Whisper: transcribir es siempre Groq. Para resumir o chatear va _chat()."""
        st = load_settings()
        if not st.get("key"):
            raise RuntimeError("Falta la API key de Groq (Configuración).")
        return Groq(api_key=st["key"], max_retries=4)

    def _chat(self):
        st = load_settings()
        if not (st.get("gemini_key") or st.get("key")):
            raise RuntimeError("Falta la API key (Configuración).")
        return chat_client(st)

    def take_action(self):
        return take_action()

    def open_widget(self):
        """El ✕ del widget lo cierra del todo y el widget es el único sitio desde donde se graba:
        sin esto había que volver a abrir la app a mano. El mutex dice si sigue vivo, así que un
        segundo clic no deja dos pills flotando."""
        if lock_held(WIDGET_MUTEX):
            return {"ok": True, "msg": "El widget ya está abierto."}
        launch()
        return {"ok": True, "msg": "Widget abierto ✓"}

    def is_dark(self, theme="auto"):
        return dark_mode(theme)     # una sola función traduce tema -> oscuro, para los 2 procesos

    def get_settings(self):
        st = load_settings()
        theme = st.get("theme", "auto")
        return {"keep_audio": st.get("keep_audio", False),
                "label_speakers": st.get("label_speakers", True),
                "theme": theme, "dark": dark_mode(theme),   # resuelto acá: la UI no espera otra llamada
                "edge": st.get("edge", "top"),
                "name": st.get("name", ""), "key_set": bool(st.get("key")),
                "gemini_set": bool(st.get("gemini_key")),
                "chat_model": st.get("chat_model", ""),
                "chat_model_def": GEMINI_CHAT if st.get("gemini_key") else CHAT}

    def save_settings(self, keep_audio, label_speakers, key, theme="auto", name=None,
                      gemini_key=None, chat_model=None, edge=None):
        st = load_settings()
        st["keep_audio"] = bool(keep_audio)
        st["label_speakers"] = bool(label_speakers)
        st["theme"] = theme if theme in ("auto", "light", "dark") else "auto"
        if edge in EDGES:                 # None = la UI no lo manda (quitar Gemini): no se toca
            st["edge"] = edge
        if name is not None:              # None = la UI no lo manda; "" = borrarlo a propósito
            st["name"] = speaker_label(name)
        if key:
            st["key"] = key.strip()
        # Un campo vacío NO borra la key: el diálogo se abre siempre vacío y guardar cualquier
        # otro ajuste la tiraría. Para quitarla está el botón, que manda "-".
        if chat_model is not None:        # "" = volver al modelo por defecto del proveedor
            st["chat_model"] = " ".join(str(chat_model).split())[:64]
        if gemini_key == "-":
            st["gemini_key"] = ""
        elif gemini_key:
            st["gemini_key"] = gemini_key.strip()
        save_settings(st)
        return self.get_settings()

    def test_key(self, key, gemini_key="", chat_model=None):
        st = load_settings()
        if chat_model is not None:
            st["chat_model"] = chat_model
        elegido = (st.get("chat_model") or "").strip()
        g = gemini_key or st.get("gemini_key")
        # Sin Gemini, el modelo elegido es de Groq y es el que hay que probar: la UI promete
        # que Probar valida que exista. Con Gemini, a Groq le toca el suyo por defecto (relevo).
        gm = CHAT if g else (elegido or CHAT)
        c = Groq(api_key=(key or st.get("key")), max_retries=0)
        for _ in range(2):        # el segundo intento va con el relevo si CHAT ya no existe
            try:
                c.chat.completions.create(model=gm, max_tokens=1,
                                          messages=[{"role": "user", "content": "hi"}])
                break
            except Exception as e:
                # solo se salta de modelo si el que falta es el nuestro: si falta el que eligió
                # el usuario, eso es justo lo que Probar tiene que decirle
                if getattr(e, "status_code", None) == 404 and gm == CHAT and switch_chat(c):
                    gm = CHAT
                    continue
                return {"ok": False, "msg": f"Groq ({gm}): " + humano(e)[:150]}
        # La de Gemini se prueba aparte porque es la que va a resumir: si está mal, el error no
        # aparecería hasta terminar una reunión entera.
        if not g:
            return {"ok": True, "msg": f"Groq válida ✓ resume {gm}"}
        m = elegido or GEMINI_CHAT
        try:
            from openai import OpenAI
            OpenAI(api_key=g, base_url=GEMINI_URL, max_retries=0).chat.completions.create(
                model=m, max_completion_tokens=1,
                messages=[{"role": "user", "content": "hi"}])
        except Exception as e:
            return {"ok": False, "msg": f"Gemini ({m}): " + humano(e)[:130]}
        return {"ok": True, "msg": f"Las dos válidas ✓ resume {m}, Groq de respaldo"}

    def __init__(self):
        self._cache = {}          # archivo -> (mtime, _slim(sesión)), ver list_sessions

    @staticmethod
    def _slim(s):
        """Lo que la lista necesita de una sesión, sin la transcripción de 70k chars."""
        d = {k: s.get(k) for k in ("id", "name", "date", "time", "dur", "status", "stage",
                                   "fav", "pid", "sum_error")}
        d["summary"], d["transcript"] = bool(s.get("summary")), bool(s.get("transcript"))
        return d

    def _meta(self, s):
        s = _view(s)
        m = {k: s.get(k) for k in ("id", "name", "date", "time", "dur", "status", "stage")}
        m["fav"] = bool(s.get("fav"))
        # sin acta: falló el resumen. La lista lo marca con un punto, antes solo se veía al abrirla
        m["nosum"] = bool(s.get("sum_error")) or (s.get("status") == "done" and not s.get("stage")
                                                  and bool(s.get("transcript"))
                                                  and not s.get("summary"))
        return m

    def list_sessions(self):
        """Solo relee los JSON cuyo mtime cambió (misma idea que sync_index). Durante una
        transcripción cada _patch cambia el mtime de notas/ y esto parseaba TODOS los JSON
        completos cada 2,5 s: ~4 MB por tick con 40 sesiones, 20 MB con 200. _view se aplica
        igual en cada llamada: que el proceso dueño muera no cambia el archivo."""
        out, seen = [], set()
        for f in (os.listdir(NOTAS) if os.path.isdir(NOTAS) else []):
            if not f.endswith(".json"):
                continue
            p = os.path.join(NOTAS, f)
            try:          # con el tamaño: dos escrituras en el mismo tick del reloj de archivos
                st = os.stat(p)
                mt = (st.st_mtime_ns, st.st_size)
            except OSError:
                continue
            seen.add(f)
            hit = self._cache.get(f)
            if not hit or hit[0] != mt:
                try:
                    s = load_json(p)
                except Exception:
                    log.warning("sesión ilegible: %s", f, exc_info=True)
                    continue
                # un .json cualquiera en notas/ no puede tumbar la lista
                hit = self._cache[f] = (mt, self._slim(s) if s.get("id") else None)
            if hit[1]:
                out.append(self._meta(hit[1]))
        for f in set(self._cache) - seen:
            del self._cache[f]
        return sorted(out, key=lambda x: x["id"], reverse=True)

    def _hits(self, rows):
        """[(sid, fragmento)] -> metadatos para la lista. Ya vienen por relevancia (rank)."""
        out = []
        for sid, snip in rows:
            try:
                m = self._meta(load_session(sid))
            except Exception:
                continue
            m["snip"] = snip
            out.append(m)
        return out

    def search(self, q):
        if not (q or "").strip():
            return self.list_sessions()
        return self._hits(search_index(q))

    def search_related(self, q, seen=()):
        """Sugerencias para cuando uno recuerda la idea pero no la palabra ("engaño" cuando
        en la reunión se dijo "fraude"). {terms, hits}: la UI las muestra aparte, marcadas.
        `seen` son los ids que la UI ya tiene de la búsqueda literal, para no repetirlos ni
        volver a correr esa consulta."""
        if not (q or "").strip():
            return {"terms": [], "hits": []}
        try:
            terms = expand_query(self._chat(), q)
        except Exception as e:
            log.warning("sin sinónimos", exc_info=True)
            return {"terms": [], "hits": [], "msg": humano(e)[:120]}
        return {"terms": terms, "hits": self._hits(related_index(terms, set(seen)))}

    def _paths(self, sid):
        """Dónde vive en disco todo lo de la sesión, más las URLs con las que la ventana puede
        reproducirlo. Las dos cosas: la pestaña Info muestra la ruta real (cuando algo se hace
        raro es lo primero que uno quiere ver) y el reproductor necesita la URL."""
        wav = os.path.join(NOTAS, f"{sid}.wav")
        sh = shots(sid)
        return {"json": spath(sid), "audio": wav if os.path.exists(wav) else "",
                "audio_url": media_url(wav), "shots": sh,
                "shot_urls": [media_url(p) for p in sh], "dir": NOTAS}

    def notas_stamp(self):
        """mtime de la carpeta notas/: cambia al crear o borrar un archivo, así que delata una
        sesión nueva (o borrada desde afuera) con un solo stat. Lo llama el poll de la ventana;
        listar y parsear todos los JSON cada 2,5 s sería el mismo resultado pagando mucho más."""
        try:
            return os.path.getmtime(NOTAS)
        except OSError:
            return 0

    def get_session(self, sid):
        return dict(_view(load_session(sid)), paths=self._paths(sid))

    def copy_text(self, text=""):
        """Copia texto al portapapeles (CF_UNICODETEXT). pywebview no expone clipboard y
        navigator.clipboard no está garantizado en WebView2 con file://, así que se va por
        las API de Win32 directamente."""
        if not text:
            return {"ok": False}
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        u.OpenClipboard.argtypes = [wintypes.HWND]
        u.OpenClipboard.restype = wintypes.BOOL
        u.EmptyClipboard.restype = wintypes.BOOL
        k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k.GlobalAlloc.restype = wintypes.HGLOBAL
        k.GlobalLock.argtypes = [wintypes.HGLOBAL]
        k.GlobalLock.restype = ctypes.c_void_p
        k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        k.GlobalUnlock.restype = wintypes.BOOL
        k.GlobalFree.argtypes = [wintypes.HGLOBAL]
        u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        u.SetClipboardData.restype = wintypes.HANDLE
        u.CloseClipboard.restype = wintypes.BOOL
        try:
            if not u.OpenClipboard(None):
                return {"ok": False}
            try:
                u.EmptyClipboard()
                data = (text + "\0").encode("utf-16-le")
                h = k.GlobalAlloc(0x0042, len(data))        # GMEM_MOVEABLE | GMEM_ZEROINIT
                if not h:
                    return {"ok": False}
                try:
                    p = k.GlobalLock(h)
                    if not p:
                        return {"ok": False}
                    ctypes.memmove(p, data, len(data))
                    k.GlobalUnlock(h)
                    if not u.SetClipboardData(13, h):        # CF_UNICODETEXT
                        return {"ok": False}
                    h = None                                  # el portapapeles toma posesión
                finally:
                    if h:
                        k.GlobalFree(h)
            finally:
                u.CloseClipboard()
        except Exception as e:
            log.warning("no se pudo copiar al portapapeles", exc_info=True)
            return {"ok": False, "msg": str(e)[:120]}
        return {"ok": True}

    def delete_audio(self, sid):
        """Borra el .wav de auditoría y deja el resto. Un .wav de una hora son ~110 MB."""
        try:
            os.remove(os.path.join(NOTAS, f"{sid}.wav"))
        except OSError:
            pass
        return {"ok": True, "paths": self._paths(sid)}

    def open_path(self, p, select=False):
        """Abre un archivo con el programa del sistema, o lo muestra en el Explorador.
        Solo archivos que estén EN notas/: la vista es local y de confianza, pero un startfile
        con ruta libre es un agujero gratis y no cuesta nada no dejarlo abierto."""
        p = os.path.abspath(p or "")
        if os.path.dirname(p) != os.path.abspath(NOTAS) or not os.path.exists(p):
            return {"ok": False}
        if select:
            subprocess.Popen(f'explorer /select,"{p}"')   # explorer devuelve 1 aunque funcione
        else:
            os.startfile(p)
        return {"ok": True}

    # Todo lo que escribe pasa por _patch: leer-modificar-escribir con load/store completos
    # pisaba lo que el widget guardara en el medio (el patch final de una transcripción).
    def save_notes(self, sid, notes):
        return {"ok": bool(_patch(sid, notes=notes))}

    def toggle_fav(self, sid):
        s = _patch(sid, lambda s: s.update(fav=not s.get("fav")))
        return {"ok": bool(s), "fav": bool(s and s.get("fav"))}

    def rename_session(self, sid, name):
        n = clean_name(name)
        s = _patch(sid, name=n) if n else load_session(sid)
        return {"ok": bool(s), "name": (s or {}).get("name", "")}

    def rename_speaker(self, sid, old, new):
        """«Los demás» -> «Acme» en esta sesión: en los turnos y en el texto plano, que es lo
        que va al modelo, así que el acta siguiente ya sale con el nombre real."""
        new = speaker_label(new)
        if not (old and new) or new == old:
            return {"ok": False}

        def fn(s):
            for t in s.get("turns") or []:
                if t.get("who") == old:
                    t["who"] = new
            s["transcript"] = re.sub(r"(^|\n\n)" + re.escape(old) + ": ",
                                     lambda m: m.group(1) + new + ": ", s.get("transcript") or "")
        return {"ok": bool(_patch(sid, fn)), "who": new}

    def toggle_done(self, sid, i):
        """Tilde de una acción del acta. Se guarda el índice del bullet en s["done"] y el texto
        del acta no se toca (ver acciones())."""
        def fn(s):
            s["done"] = sorted(set(s.get("done") or []) ^ {int(i)})
        s = _patch(sid, fn)
        return {"ok": bool(s), "done": (s or {}).get("done", [])}

    def pending(self):
        """Las acciones sin tildar de todas las sesiones, para la vista Pendientes. Lee todos
        los JSON, pero solo cuando se abre esa vista."""
        out = []
        for s in iter_sessions():
            done = set(s.get("done") or [])
            out += [{"sid": s["id"], "name": s.get("name") or "Sesión", "date": s.get("date"),
                     "i": i, "who": who, "text": text}
                    for i, who, text in acciones(s.get("summary")) if i not in done]
        return sorted(out, key=lambda x: x["sid"], reverse=True)

    def export_md(self, sid):
        """notas/<id>.md con todo lo que se pega en un mail o en Notion."""
        s = load_session(sid)
        out = [f"# {s.get('name') or 'Sesión'}",
               f"{s.get('date', '')} · {s.get('time', '')} · {round((s.get('dur') or 0) / 60)} min"]
        for title, k in (("Acta", "summary"), ("Minuta", "minuta"), ("Notas", "notes"),
                         ("Transcripción", "transcript")):
            if (s.get(k) or "").strip():
                out += [f"## {title}", s[k].strip()]
        p = os.path.join(NOTAS, f"{sid}.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n\n".join(out) + "\n")
        return {"ok": True, "path": p}

    def test_audio(self, secs=5):
        """¿Entra audio por los dos canales? Unos segundos de cada uno, sin guardar nada: el
        aviso de canal mudo llega al terminar la reunión, y esto lo adelanta a antes de grabar."""
        import soundcard as sc
        devs = {"mic": sc.default_microphone(),
                "loop": sc.get_microphone(id=str(sc.default_speaker().name),
                                          include_loopback=True)}
        out = {}

        def cap(k):
            try:
                with devs[k].recorder(samplerate=SR, channels=1, blocksize=BLOCK) as r:
                    lv = max(rms(r.record(numframes=BLOCK)) for _ in range(SR * secs // BLOCK))
                out[k] = {"level": round(lv, 4), "ok": lv > VOICE}
            except Exception as e:
                log.warning("prueba de audio %s", k, exc_info=True)
                out[k] = {"level": 0, "ok": False, "msg": str(e)[:120]}
        ts = [threading.Thread(target=cap, args=(k,)) for k in devs]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        return out

    def delete_session(self, sid):
        # Primero el JSON, que ES la sesión: si no se pudo borrar, no se toca nada más y se dice.
        # Antes el error se tragaba y la UI decía "eliminada" con la sesión todavía en disco.
        if not remove_session(sid):
            return {"ok": False, "msg": "No se pudo eliminar: el archivo está en uso. "
                                        "Prueba de nuevo en unos segundos."}
        for p in [os.path.join(NOTAS, f"{sid}.wav"), os.path.join(NOTAS, f"{sid}.md"),
                  chan_path(sid, "mic"), chan_path(sid, "loop")] + shots(sid):
            try:
                os.remove(p)
            except OSError:   # no existe, o lo está leyendo la transcripción: esa los borra al
                pass          # terminar, porque su _patch final ve que la sesión ya no está
        drop_from_index(sid)
        return {"ok": True}

    def retry_transcription(self, sid):
        if not (os.path.exists(chan_path(sid, "mic")) or os.path.exists(chan_path(sid, "loop"))):
            return {"ok": False, "msg": "El audio ya no está disponible."}
        # pedido a mano: vuelve a tener todos los automáticos
        if not claim(sid, manual=True):
            return {"ok": False, "msg": "Ya se está transcribiendo." if os.path.exists(spath(sid))
                    else "La sesión ya no está."}
        threading.Thread(target=do_transcription, args=(sid,), daemon=True).start()
        return {"ok": True}

    def resync(self, sid):
        """Le pone tiempos a una sesión vieja volviendo a transcribir el .wav guardado.
        Hasta esta versión la transcripción se guardaba como texto plano y los tiempos que
        devolvía Whisper se tiraban, así que esas sesiones no pueden seguir el audio y no hay
        forma de recuperar los tiempos salvo preguntando de nuevo.
        El .wav es la MEZCLA de los dos canales: esto NO separa hablantes, y por eso la UI avisa
        que la separación se pierde antes de dejarte confirmar. No toca notas, resumen ni chat."""
        wav = os.path.join(NOTAS, f"{sid}.wav")
        if not os.path.exists(wav):
            return {"ok": False, "msg": "Esta sesión no tiene audio guardado."}
        if not _patch(sid, pid=os.getpid(), stage="Sincronizando…"):
            return {"ok": False, "msg": "La sesión ya no está."}
        try:
            total, cnt = n_chunks(wav_frames(wav)), [0]

            def done():
                cnt[0] += 1
                set_stage(sid, f"Sincronizando… {cnt[0]}/{total}")

            segs = transcribe_channel(self._client(), wav_chunks([wav]), "", done)
        except Exception as e:
            log.exception("sincronizar %s", sid)
            _patch(sid, stage=None)
            return {"ok": False, "msg": humano(e)}
        _patch(sid, turns=dialog_turns(segs), transcript=format_dialog(segs), stage=None)
        return {"ok": True}

    def summarize(self, sid):
        """Reintento manual: lo normal es que el resumen ya esté (auto_summary al transcribir)."""
        s = load_session(sid)
        if not s.get("transcript") and not s.get("notes"):
            return {"ok": False, "msg": "Nada que resumir."}
        _patch(sid, pid=os.getpid())       # el dueño del trabajo en curso es este proceso
        try:
            text = summarize_text(self._chat(), s.get("notes", ""), s.get("transcript", ""),
                                  lambda i, n: set_stage(sid, f"Resumiendo… {i}/{n}"), cuando(s))
        except Exception as e:
            log.exception("resumen de %s", sid)
            _patch(sid, stage=None, sum_error=humano(e))
            return {"ok": False, "msg": humano(e)}
        s = store_summary(sid, text) or {}
        return {"ok": True, "summary": s.get("summary", ""), "name": s.get("name", "")}

    def minuta(self, sid):
        """La minuta formal para enviar. A mano y no automática: se pide cuando se va a mandar,
        y así no gasta cuota en reuniones donde no hace falta. Sale del acta: sin acta, primero
        el acta."""
        s = load_session(sid)
        if not s.get("summary"):
            r = self.summarize(sid)
            if not r["ok"]:
                return r
            s = load_session(sid)
        _patch(sid, pid=os.getpid(), stage="Redactando minuta…")
        try:
            text = minuta_text(self._chat(), s)
        except Exception as e:
            log.exception("minuta de %s", sid)
            _patch(sid, stage=None, min_error=humano(e))
            return {"ok": False, "msg": humano(e)}
        _patch(sid, minuta=text, stage=None, min_error=None)
        return {"ok": True, "minuta": text}

    def ask(self, sid, q):
        s = load_session(sid)
        if not s.get("transcript"):
            return {"ok": False, "msg": "Esta sesión no tiene transcripción."}
        try:
            a = ask_text(self._chat(), s["transcript"], q)
        except Exception as e:
            log.exception("chat de %s", sid)
            return {"ok": False, "msg": humano(e)}
        # la lista se relee dentro del lock: la leída ANTES de la llamada al LLM perdía la
        # respuesta de otra pregunta que terminara en el medio
        _patch(sid, lambda s: s.update(chat=(s.get("chat") or []) + [{"q": q, "a": a}]))
        return {"ok": True, "a": a}


def set_dpi_aware():
    """Solo en el proceso de la ventana: sin esto Windows virtualiza el tamaño (le miente al
    proceso) y WebView2 igual renderiza a la escala del monitor, así que el viewport CSS queda
    a 1/escala y la UI se corta. El widget Tk se deja sin tocar: sus coordenadas son fijas y
    virtualizado se ve del tamaño correcto."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)               # PER_MONITOR_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            log.warning("sin DPI awareness", exc_info=True)


def window_size():
    """Con set_dpi_aware() puesta, WinForms ya escala lo que se le pide: se le pasan px
    LÓGICOS. Solo hay que acotarlos al escritorio lógico (físico / escala) para no pedir
    una ventana más grande que la pantalla."""
    u = ctypes.windll.user32
    s = (u.GetDpiForSystem() or 96) / 96.0
    lw, lh = u.GetSystemMetrics(0) / s, u.GetSystemMetrics(1) / s
    w, h = min(1040, int(lw * .94)), min(680, int(lh * .92))
    return w, h, (min(760, w), min(520, h))


def window_main():
    global MEDIA_BASE
    h = take_lock()           # dos procesos --window pueden llegar aquí; solo uno gana el mutex
    if not h:
        focus_window()        # ya estaba abierta (acceso del menú Inicio): traerla al frente
        return
    set_dpi_aware()
    try:
        MEDIA_BASE = start_media_server()
    except OSError:           # sin servidor la ventana funciona igual, sin audio ni capturas
        log.exception("no se pudo abrir el servidor de medios")
    import webview
    w, ht, mn = window_size()
    webview.create_window("TakeMyNotes", res("ui/index.html"), js_api=Api(),
                          width=w, height=ht, min_size=mn)
    try:
        webview.start()
    finally:
        _k32().CloseHandle(h)


def selftest():
    # relevo de modelo: solo salta a uno que la cuenta ve de verdad, y no da vueltas en círculo
    global CHAT
    ns = __import__("types").SimpleNamespace
    fake = ns(models=ns(list=lambda: ns(data=[ns(id=CHAT_ALT[-1])])))
    prev = CHAT
    logging.disable(logging.WARNING)  # si no, el test avisa de un relevo que no ha pasado
    # 429 por minuto vs 429 del día: el primero se espera, el segundo va derecho al relevo
    tpd = Exception("Error code: 429 - {'message': 'Rate limit reached ... on tokens per day "
                    "(TPD): Limit 200000, Used 199327'}")
    tpd.status_code = 429
    dia_gemini = Exception("429 RESOURCE_EXHAUSTED quota_metric "
                           "GenerateRequestsPerDayPerProjectPerModel")
    dia_gemini.status_code = 429
    minuto = Exception("Error code: 429 - rate limit on tokens per minute (TPM)")
    minuto.status_code = 429
    assert not _retryable(tpd) and not _retryable(dia_gemini)
    assert _retryable(minuto)
    caido = Exception("boom"); caido.status_code = 503
    assert _retryable(caido)

    assert switch_chat(fake) and CHAT == CHAT_ALT[-1]
    assert not switch_chat(fake)      # ya estamos en el único que existe: no hay a dónde saltar
    assert not switch_chat(ns(models=ns(list=lambda: 1 / 0)))   # sin lista de modelos, no inventa
    logging.disable(logging.NOTSET)
    CHAT = prev

    a = np.array([0.5, -0.5, 0.5], "float32")
    assert abs(np.abs(cleanup(a)).max() - 0.9) < 1e-4
    assert len(mix_arrays(np.zeros(0, "float32"), np.zeros(0, "float32"))) == 0
    d = format_dialog([(2.0, "Los demás", "hola"), (0.5, "Yo", "buenas"), (1.0, "Yo", "qué tal")])
    assert d == "Yo: buenas qué tal\n\nLos demás: hola", d
    assert format_dialog([(1.0, "", "hola"), (2.0, "", "qué tal")]) == "hola qué tal"

    # turnos con tiempo: es lo que le deja a la ventana saltar el audio a una línea
    t = dialog_turns([(2.0, "Los demás", "hola"), (0.5, "Yo", "buenas"), (1.04, "Yo", "qué tal")])
    assert t == [{"t": 0.5, "who": "Yo", "text": "buenas qué tal"},
                 {"t": 2.0, "who": "Los demás", "text": "hola"}], t
    assert dialog_turns([]) == [] and dialog_turns([(1.0, "Yo", "")]) == []
    # el tiempo de un turno es el del PRIMER segmento, no el del último que se le pegó
    assert dialog_turns([(9.0, "Yo", "a"), (11.0, "Yo", "b")])[0]["t"] == 9.0

    # Range del <audio>: sin esto, mover la aguja baja los 128 MB del WAV antes de sonar
    assert byte_range(None, 100) == (0, 99, False)
    assert byte_range("bytes=0-", 100) == (0, 99, False)     # todo el archivo: no es parcial
    assert byte_range("bytes=10-19", 100) == (10, 19, True)
    assert byte_range("bytes=90-999", 100) == (90, 99, True)  # se recorta al tamaño real
    assert byte_range("bytes=-20", 100) == (80, 99, True)     # sufijo: los últimos N
    assert byte_range("bytes=50-10", 100) == (0, 99, False)   # al revés: el archivo entero
    assert byte_range("bytes=abc", 100) == (0, 99, False)     # basura: el archivo entero
    assert byte_range("bytes=0-0", 100) == (0, 0, True)       # un solo byte, es válido
    assert byte_range("bytes=0-99", 0) == (0, 0, False)       # archivo vacío
    assert n_chunks(0) == 1                                       # el total del progreso
    assert n_chunks(SR * CHUNK_SECS + 1) == 2

    # eco: el micro captó los parlantes, así que la misma frase salió en los dos canales
    segs = [(0.0, "Yo", "esperemos que nos lo resuelvan pronto porque están amarradas"),
            (1.2, "Los demás", "que nos lo resuelvan pronto porque están amarradas allá"),
            (30.0, "Yo", "esto lo dije yo solo y nadie más lo dijo"),
            (60.0, "Yo", "ok")]                                  # corto: se deja siempre
    kept, echo, aporte = drop_echo(segs, "Yo")
    assert [t for t, _, _ in kept] == [1.2, 30.0, 60.0], kept    # se queda la copia del loopback
    assert 0.4 < echo < 0.5 and aporte > ECHO_MIN, (echo, aporte)  # hablás: se separa
    twins = [(t, l, "los dos canales dicen exactamente lo mismo") for t, l in
             ((0.0, "Yo"), (0.5, "Los demás"), (10.0, "Yo"), (10.4, "Los demás"))]
    _, echo, aporte = drop_echo(twins, "Yo")
    assert echo == 1.0 and aporte == 0.0, (echo, aporte)         # gemelos: nada que separar
    # micro sucio pero con algo propio (el otro habla el 80% y vos poco): sigue separando
    mostly = []
    for i in range(3):
        frase = f"frase numero {i} que dijo el otro y el micro también captó de los parlantes"
        mostly += [(i * 20.0, "Los demás", frase), (i * 20.0 + 0.4, "Yo", frase)]
    mostly.append((70.0, "Yo", "esto lo dije yo solo y nadie más lo dijo en toda la reunión"))
    _, echo, aporte = drop_echo(mostly, "Yo")
    assert echo > ECHO_DUP and aporte > ECHO_MIN, (echo, aporte)
    # eco tardío (caso real, sesión 20260819_103448): el loopback trae UN segmento de 149 a 158
    # y el eco de su última frase se transcribe en el micro a los 159.2. Mirando solo el
    # arranque quedan a 10.2 s y con ±6 s se colaba, atribuido a quien graba.
    tarde = [(149.0, "Los demás", "bueno no lo manejamos así pero si es que lo queremos manejar",
              158.0),
             (159.2, "Yo", "bueno no lo manejamos así pero sí así lo queremos así debería", 164.0),
             (200.0, "Yo", "eso lo dije yo y no lo había dicho nadie antes en la reunión", 205.0)]
    kept, _, _ = drop_echo(tarde, "Yo")
    assert [x[0] for x in kept] == [149.0, 200.0], kept
    lejos = [(10.0, "Yo", "esto lo digo yo antes de que el otro abra la boca", 15.0),
             (40.0, "Los demás", "esto lo digo yo antes de que el otro abra la boca", 45.0)]
    assert drop_echo(lejos, "Yo")[0][0][1] == "Yo"   # intervalos que no se tocan: no es eco

    parts = split_text("a\n\n" + "b" * 30, limit=20)             # el resumen va por tramos
    assert all(len(p) <= 20 for p in parts), parts               # ninguno excede el request
    assert "".join(p.replace("\n", "") for p in parts) == "a" + "b" * 30, parts
    assert split_text("") == [] and split_text("corto") == ["corto"]

    # la hora que se lista es la del INICIO (el sid), no la del stop, que llega hasta 1 h después
    assert session_start("20260819_120427").strftime("%Y-%m-%d %H:%M") == "2026-08-19 12:04"
    assert session_start("basura").year >= 2024          # y un id raro no puede tumbar el guardado

    assert _unprotect(_protect("gsk_prueba_ñ")) == "gsk_prueba_ñ"   # DPAPI ida y vuelta

    # sin alternancia de hablantes la etiqueta no corta nunca: sin tope, toda la reunión salía
    # en UN turno (sesiones 20260820_110443 y 20260821_091700, 42 KB y 30 KB de un solo bloque)
    uno = [(float(i), "", "x" * 100, float(i) + 1) for i in range(20)]
    t = dialog_turns(uno)
    assert len(t) > 1 and all(len(x["text"]) <= TURN_CHARS for x in t), [len(x["text"]) for x in t]
    assert [x["t"] for x in t] == sorted(x["t"] for x in t)      # cada corte con su tiempo real
    assert "".join(x["text"] for x in t).replace(" ", "") == "x" * 2000   # no se pierde texto
    # y la alternancia normal sigue juntando: dos segmentos seguidos del mismo son un turno
    dos = [(0.0, "Yo", "hola", 1.0), (1.0, "Yo", "que tal", 2.0), (2.0, "Otro", "bien", 3.0)]
    assert [(x["who"], x["text"]) for x in dialog_turns(dos)] == [("Yo", "hola que tal"),
                                                                 ("Otro", "bien")]

    assert idle_state(0) == "" and idle_state(IDLE_WARN - 1) == ""
    assert idle_state(IDLE_WARN + 1) == "warn"
    assert idle_state(IDLE_WARN + IDLE_STOP + 1) == "stop"

    assert fts_query('presu*puesto "mkt:') == '"presu"* "puesto"* "mkt"*'
    assert fts_query("  ") == ""
    if lock_held():                          # la app está corriendo: nadie más puede tomarlo
        assert take_lock() is None
    else:
        h = take_lock()
        assert h and lock_held() and take_lock() is None
        _k32().CloseHandle(h)
        assert not lock_held()               # el SO lo destruye al cerrar el handle
    assert lock_held(WIDGET_MUTEX) == lock_held(WIDGET_MUTEX)   # ventana y widget, mutex distintos

    # el modelo alucina sobre el silencio ("Gracias." en cada ventana de 30 s muda) y no lo avisa
    # por ningún campo de la respuesta: lo que lo delata es que ahí el canal no tiene señal
    voz = np.zeros(SR * 60, "float32")
    voz[SR * 10:SR * 12] = 0.4                       # habla entre el segundo 10 y el 12
    voz[SR * 30:] = 0.002                            # y de ahí en adelante, ruido de sala
    assert has_voice(voz, 10, 12) and not has_voice(voz, 30, 59.98)
    assert not has_voice(voz, 0, 2) and has_voice(voz, 9, 13)   # el tramo que roza la voz se queda
    assert has_voice(None, 0, 30) and has_voice(np.zeros(0, "float32"), 0, 30)   # sin canal, no filtra
    assert has_voice(voz, 11, 11)                    # tramo de duración 0: mira al menos 1 muestra
    assert has_voice(voz, 1e9, 1e9 + 30)             # fuera del audio: nada que juzgar, no filtra
    segs = _segments({"segments": [{"start": 30, "end": 59.9, "text": " Gracias."}]})
    assert segs == [(30.0, 59.9, "Gracias.")], segs  # y el end es lo que se compara con el audio

    # el caso 20260831_120332: 38 min de silencio digital con un chasquido al arrancar. Con el
    # pico, ese click daba por buenas las tres guardas y el acta salió vacía.
    click = np.zeros(SR * 60, "float32")
    click[SR // 2] = 0.72                            # un solo sample, como el pop de arranque
    assert float(np.abs(click).max()) > VOICE        # el pico decía "hay voz"...
    assert rms(click[:BLOCK]) < VOICE                # ...y el rms del bloque no
    assert not has_voice(click, 0, 30)               # el tramo con el click sigue siendo silencio
    assert rms(np.zeros(0, "float32")) == 0.0        # canal vacío: no revienta

    # un canal mudo lo dice la FRACCIÓN de bloques con voz, no cuántos: 20 bloques con ruido son
    # nada en una hora y son la reunión entera en dos minutos
    assert canal_mudo(20, 18000) and not canal_mudo(20, 60)
    assert canal_mudo(0, 0)                          # sin bloques escritos: mudo, no división por 0
    assert not canal_mudo(int(0.39 * 1000), 1000)    # 39%: el peor de las 13 sesiones buenas
    assert canal_mudo(int(0.047 * 1000), 1000)       # 4.7%: la sesión muda

    assert shade("#808080", .5) == "#404040" and shade("#ffffff", 2) == "#ffffff"   # clampea

    # Distribución del pill: los botones no se pisan y ningún halo se sale por una esquina.
    # A ojo no se nota (el halo solo aparece 140 ms al tocar), así que va acá: es geometría pura.
    W, H, R, V = Widget.W, Widget.H, Widget.R, vars(Widget)
    areas = {k: (V[k] - Widget.HALO, V[k] + Widget.HALO)
             for k in ("MIC", "SHOT", "PAUSE", "NOTE", "GEAR", "EXPAND", "QUIT")}
    areas["REC"] = (Widget.REC - Widget.RECR, Widget.REC + Widget.RECR)
    for a, b in ((a, b) for a in areas for b in areas if a < b):
        gap = max(areas[a][0], areas[b][0]) - min(areas[a][1], areas[b][1])
        assert gap >= 0, f"{a} y {b} se pisan {-gap} px"
    for k, (lo, hi) in areas.items():
        for x in (lo, hi):
            for y in (14, 40):               # alto del halo, y del círculo de grabar
                assert in_pill(x, y, W, H, R), f"el halo de {k} se sale del pill en ({x},{y})"
    # y el bloque del reloj no puede llegar a meterse debajo del primer botón
    assert Widget.CLOCK + 103 <= min(areas["MIC"]), "el estado se solapa con el micro"
    assert Widget.SEP == (areas["PAUSE"][1] + areas["NOTE"][0]) // 2, \
        "el separador tiene que quedar centrado entre los dos grupos, no pegado a uno"
    assert in_pill(Widget.GRIP - 2, 21, W, H, R), "el asa de arrastre se sale por la izquierda"
    assert Widget.TAB_W < Widget.W and Widget.EAR <= Widget.R

    assert not _stale({"status": "pending", "pid": os.getpid()})
    dead = {"status": "pending", "pid": 999999999}    # no es múltiplo de 4: PID imposible
    assert _stale(dead) and _view(dead)["status"] == "error"
    assert not _stale({"status": "done", "pid": 999999999})
    busy = {"status": "done", "stage": "Resumiendo… 2/4", "pid": 999999999}
    assert _stale(busy) and _view(busy)["stage"] == ""   # un resumen huérfano no gira para siempre
    assert not _stale({"status": "done", "stage": "", "pid": 999999999})

    global NOTAS, SETTINGS_PATH              # todo de juguete, no toca las notas reales
    import tempfile
    NOTAS = tempfile.mkdtemp()
    SETTINGS_PATH = os.path.join(NOTAS, "settings.json")

    save_settings({"keep_audio": True, "label_speakers": False, "theme": "light", "key": "gsk_x"})
    st = load_settings()                     # ida y vuelta por disco, con la key cifrada
    assert st["key"] == "gsk_x" and st["keep_audio"] and not st["label_speakers"]
    assert dark_mode("dark") and not dark_mode("light")
    assert isinstance(dark_mode("auto"), bool)          # lo dice el registro de Windows
    assert speaker_label(" Eric  Vaz ") == "Eric Vaz"      # etiqueta de hablante para la UI
    assert ":" not in speaker_label("Eric: jefe"), "un ':' rompería el parseo de la burbuja"
    assert speaker_label("x" * 40) == "x" * 24 and speaker_label(None) == ""
    assert clean_name("  Junta\nde   ventas ") == "Junta de ventas"   # misma regla que la UI
    assert clean_name("x" * 120) == "x" * 80 and clean_name(None) == ""
    assert "key" not in load_json(SETTINGS_PATH), "la key nunca va en claro al disco"

    # Gemini opcional: solo cambia con qué se RESUME, y la de Groq sigue igual para Whisper
    c = chat_client({"key": "gsk_x"})
    assert (c.modelo, c.trozo) == (CHAT, CHAT_CHARS)
    assert c.relevo is None                  # con una sola key no hay a quién pasarle el trabajo
    c = chat_client({"key": "gsk_x", "gemini_key": "AIza_x"})
    assert (c.modelo, c.trozo, c.tope) == (GEMINI_CHAT, GEMINI_CHARS, GEMINI_OUT)
    assert str(c.base_url).startswith(GEMINI_URL[:40]), c.base_url
    assert c.relevo is not None and c.relevo.modelo == CHAT   # y Groq espera detrás
    assert chat_client({"gemini_key": "AIza_x"}).relevo is None    # sin key de Groq no hay relevo
    # modelo fijado a mano: manda sobre el preferido, pero el relevo entra con el suyo, que es
    # el único que se sabe que existe (si el nombre elegido está mal, el relevo tiene que salvar)
    c2 = chat_client({"key": "gsk_x", "gemini_key": "AIza_x", "chat_model": "gemini-otro"})
    assert c2.modelo == "gemini-otro" and c2.relevo.modelo == CHAT
    assert chat_client({"key": "gsk_x", "chat_model": "  otro  "}).modelo == "otro"

    # el relevo repite el trabajo entero con el otro proveedor, no lo continúa a medias
    vistos = []
    def falla_con_gemini(cl, *a):
        vistos.append(cl.modelo)
        if cl.modelo == GEMINI_CHAT:
            raise RuntimeError("cuota diaria agotada")
        return "acta"
    assert con_relevo(falla_con_gemini, c, "notas", "texto", None) == "acta"
    assert vistos == [GEMINI_CHAT, CHAT], vistos
    def siempre_falla(cl, *a):
        raise RuntimeError("los dos caídos")
    try:                                     # sin relevo, el error sube: no se traga en silencio
        con_relevo(siempre_falla, c.relevo, "n", "t", None)
        raise AssertionError("tenía que propagar")
    except RuntimeError:
        pass
    save_settings({"keep_audio": True, "label_speakers": False, "theme": "light",
                   "key": "gsk_x", "gemini_key": "AIza_x"})   # el theme lo lee un assert de abajo
    raw = load_json(SETTINGS_PATH)
    assert "gemini_key" not in raw and raw.get("gemini_key_enc"), "la de Gemini también cifrada"
    assert load_settings()["gemini_key"] == "AIza_x"
    # una reunión de una hora cabe entera en Gemini: sin trocear no hay contexto que se pierda
    assert len(split_text("x" * 50000, GEMINI_CHARS)) == 1

    store_session({"id": "s0", "name": "Café ñandú", "transcript": "acción"})
    assert load_session("s0")["name"] == "Café ñandú"
    assert not [f for f in os.listdir(NOTAS) if f.endswith(".tmp")]   # sin temporales sueltos
    os.remove(spath("s0"))
    store_session({"id": "s1", "name": "Junta de ventas",
                   "transcript": "hay que subir el presupuesto de marketing"})
    store_session({"id": "s2", "name": "Otra cosa", "transcript": "el clima estuvo raro"})
    hits = search_index("presupuesto marketing")          # 2 palabras separadas en el texto
    assert [h[0] for h in hits] == ["s1"], hits
    assert "presupuesto" in hits[0][1]                    # el fragmento trae la palabra
    assert [h[0] for h in search_index("junta")] == ["s1"]        # busca en el título
    assert [h[0] for h in search_index("Presu")] == ["s1"]        # prefijo, sin importar mayúsculas
    assert search_index("presupuesto clima") == []                # AND, no OR

    api = Api()                                                   # la capa que consume la UI
    assert [m["id"] for m in api.list_sessions()] == ["s2", "s1"]  # más nueva primero
    assert api.search("presupuesto")[0]["id"] == "s1"
    assert api.search("  ")[0]["id"] == "s2"                       # búsqueda vacía = listado
    api.save_notes("s1", "ojo con el gasto")
    assert load_session("s1")["notes"] == "ojo con el gasto"
    api.rename_session("s1", "  Junta Q3  ")
    assert load_session("s1")["name"] == "Junta Q3"
    assert api.search("gasto")[0]["id"] == "s1"                    # las notas también se indexan
    assert api.retry_transcription("s1")["ok"] is False            # ya no queda audio crudo
    assert api.resync("s1")["ok"] is False, "sin .wav no hay de dónde sacar los tiempos"
    assert api.resync("noexiste")["ok"] is False
    assert api.toggle_fav("s1")["fav"] is True                     # favoritos
    assert api._meta(load_session("s1"))["fav"] is True
    assert api.toggle_fav("s1")["fav"] is False

    store_session({"id": "s3", "name": "En curso", "notes": "", "status": "pending"})
    set_stage("s3", "Transcribiendo… 1/2")                         # progreso para la ventana
    assert load_session("s3")["stage"] == "Transcribiendo… 1/2"
    assert api._meta(load_session("s3"))["stage"] == "Transcribiendo… 1/2"
    api.save_notes("s3", "escritas mientras transcribía")          # el otro proceso, en paralelo
    _patch("s3", transcript="listo", stage=None, error=None)
    s3 = load_session("s3")
    assert s3["notes"] == "escritas mientras transcribía", "_patch pisó lo del otro proceso"
    assert s3["transcript"] == "listo" and "stage" not in s3
    assert _patch("noexiste", stage="x") is None                   # borrada: no se resucita

    # reintento automático: solo lo que falló y todavía tiene el audio crudo
    store_session({"id": "s4", "name": "Falló", "status": "error", "error": "conexión"})
    assert list(retryable_sessions()) == []          # sin audio no hay nada que reintentar
    with open(chan_path("s4", "mic"), "wb"):
        pass
    assert list(retryable_sessions()) == ["s4"], list(retryable_sessions())
    _patch("s4", retries=AUTO_RETRIES)
    assert list(retryable_sessions()) == []          # agotó los automáticos: queda el botón
    # se cerró la app en medio de la transcripción: quedó 'pending' con un pid muerto. Esto es lo
    # que se veía como transcripción perdida: hay que barrerla igual que un error de red.
    store_session({"id": "s5", "status": "pending", "pid": 999999999})
    with open(chan_path("s5", "loop"), "wb"):
        pass
    assert list(retryable_sessions()) == ["s5"], list(retryable_sessions())
    _patch("s5", pid=os.getpid())                    # viva: la está transcribiendo este proceso
    assert list(retryable_sessions()) == []
    os.remove(spath("s5")); os.remove(chan_path("s5", "loop"))
    logging.disable(logging.CRITICAL)     # el hilo que arranca falla (wav de juguete) y logea
    assert api.retry_transcription("s4")["ok"] is True           # a mano siempre se puede
    assert load_session("s4")["retries"] == 0                    # y le devuelve los intentos
    assert isinstance(net_up(timeout=0.5), bool)
    assert not net_up("127.0.0.1", 9, 0.3)           # puerto muerto: no hay red que valga

    # pestaña Info: rutas en disco, audio de auditoría y capturas
    store_session({"id": "s6", "name": "Con audio"})
    assert api.get_session("s6")["paths"]["json"] == spath("s6")
    assert api.get_session("s6")["paths"]["audio"] == ""           # sin .wav todavía
    wav = os.path.join(NOTAS, "s6.wav")
    write_mono(wav, np.zeros(SR // 10, "float32"))
    assert api.get_session("s6")["paths"]["audio"] == wav
    assert not api.open_path(os.path.join(BASE, "settings.json"))["ok"], "solo notas/"
    assert not api.open_path(os.path.join(NOTAS, "no-existe.wav"))["ok"]
    assert api.delete_audio("s6")["paths"]["audio"] == ""          # se borra solo el .wav
    assert not os.path.exists(wav) and os.path.exists(spath("s6"))
    assert shots("s6") == []
    grab_png(os.path.join(NOTAS, "s6_shot01.png"))                 # captura real del escritorio
    assert shots("s6") == [os.path.join(NOTAS, "s6_shot01.png")]
    assert api.get_session("s6")["paths"]["shots"] == shots("s6")
    # las URLs solo existen con el servidor de medios levantado (la ventana, no el widget)
    assert api.get_session("s6")["paths"]["shot_urls"] == [""]
    global MEDIA_BASE
    MEDIA_BASE = "http://127.0.0.1:1/tok/"
    P = api.get_session("s6")["paths"]
    assert P["shot_urls"] == ["http://127.0.0.1:1/tok/s6_shot01.png"], P["shot_urls"]
    assert P["audio_url"] == "", "sin .wav no hay URL de audio"
    write_mono(wav, np.zeros(SR // 10, "float32"))
    assert api.get_session("s6")["paths"]["audio_url"].endswith("/s6.wav")

    # el servidor de medios de verdad: es lo que alimenta el <audio> de la transcripción
    from urllib.request import urlopen, Request
    from urllib.error import HTTPError
    MEDIA_BASE = start_media_server()
    url = api.get_session("s6")["paths"]["audio_url"]
    assert url.startswith(MEDIA_BASE) and url.endswith("s6.wav"), url
    with urlopen(url) as r:
        entero = r.read()
        assert r.status == 200 and r.headers["Accept-Ranges"] == "bytes"
    assert len(entero) == os.path.getsize(wav)
    with urlopen(Request(url, headers={"Range": "bytes=10-19"})) as r:   # el salto de la aguja
        assert r.status == 206 and r.read() == entero[10:20]
        assert r.headers["Content-Range"] == f"bytes 10-19/{len(entero)}"
    otro_token = re.sub(r"/[^/]+/$", "/tokenmalo/", MEDIA_BASE)
    for bad, why in ((MEDIA_BASE + "no-existe.wav", "un archivo que no está"),
                     # %2F y no "/": si no, urllib normaliza el .. antes de mandarlo y el guard
                     # de _resolve() (el que importa) nunca se ejerce
                     (MEDIA_BASE + "..%2Fsettings.json", "salir de notas/ con .."),
                     (MEDIA_BASE + "sub%2Fdir%2Fx.wav", "una subcarpeta"),
                     (otro_token + "s6.wav", "un token que no es")):
        try:
            urlopen(bad).read()
            assert False, "el servidor de medios aceptó " + why
        except HTTPError as e:
            assert e.code == 404, (why, e.code)
    MEDIA_BASE = ""
    api.delete_session("s6")                                       # y se lleva las capturas
    assert not os.path.exists(spath("s6")) and shots("s6") == []

    api.delete_session("s1")                                       # borra JSON + fila del índice
    assert not os.path.exists(spath("s1"))
    assert search_index("presupuesto") == []

    # --- integridad: dos procesos sobre el mismo JSON ---
    # 1.1: con el archivo abierto por otro (la ventana leyendo), os.replace da WinError 5
    import threading as th
    abierto = th.Event()

    def lector_de(sid):
        def lector():
            abierto.clear()
            with open(spath(sid), encoding="utf-8"):
                abierto.set(); time.sleep(0.1)
        return lector
    t = th.Thread(target=lector_de("s2")); t.start(); abierto.wait()
    store_session(load_session("s2"))               # sin _replace esto revienta
    t.join()
    # 1.2: dos escritores sobre la misma sesión: no se pisan
    store_session({"id": "s8", "notes": "", "n": 0})

    def spam():
        for i in range(200):
            _patch("s8", n=i + 1)
    t = th.Thread(target=spam); t.start()
    for i in range(50):
        api.save_notes("s8", f"nota {i}")
    t.join()
    s8 = load_session("s8")
    assert s8["n"] == 200 and s8["notes"] == "nota 49", s8
    os.remove(spath("s8"))

    # claim: comprobar y reclamar en el mismo lock. Dos reintentos a la vez -> uno solo arranca
    store_session({"id": "s12", "status": "error", "retries": 0})
    ganadores = []
    hilos = [th.Thread(target=lambda m=m: ganadores.append(bool(claim("s12", manual=m))))
             for m in (False, True, False, True)]
    for t in hilos:
        t.start()
    for t in hilos:
        t.join()
    assert sorted(ganadores) == [False, False, False, True], ganadores
    assert load_session("s12")["status"] == "pending"
    _patch("s12", status="error", retries=AUTO_RETRIES)
    assert not claim("s12"), "sin intentos automáticos no lo toma el barrido"
    assert claim("s12", manual=True)["retries"] == 0, "a mano sí, y devuelve los intentos"
    assert _patch("s12", lambda s: False) is None and load_session("s12")["status"] == "pending"
    # borrar va bajo el mismo lock; con el JSON abierto por otro, reintenta en vez de fallar
    t = th.Thread(target=lector_de("s12")); t.start(); abierto.wait()
    assert remove_session("s12") and not os.path.exists(spath("s12"))
    t.join()
    assert remove_session("s12"), "ya borrada: no es un error"
    assert claim("s12") is None and _patch("s12", x=1) is None, "y nada la resucita"

    # 3.2: sin DPAPI la key va en claro, y leer no puede reescribir settings.json cada vez
    prot, guardar, n = _protect, save_settings, []
    globals()["_protect"] = lambda t: 1 / 0
    save_settings({"key": "gsk_plano", "theme": "light", "keep_audio": True,
                   "label_speakers": False})
    globals()["save_settings"] = lambda d: n.append(1)
    assert load_settings()["key"] == "gsk_plano" and n == [], "bucle de escritura"
    globals()["_protect"], globals()["save_settings"] = prot, guardar
    save_settings({"key": "gsk_x", "theme": "light", "keep_audio": True, "label_speakers": False})
    assert "_plain" not in load_json(SETTINGS_PATH)

    # 2.1: content None / choices vacío no pueden llegar como 'NoneType' has no attribute 'strip'
    def fake_chat(texto, calls=None, fin="stop"):
        def create(**kw):
            if calls is not None:
                calls.append(kw)
            t = texto.pop(0) if isinstance(texto, list) else texto
            return ns(choices=[ns(message=ns(content=t), finish_reason=fin)])
        return ns(chat=ns(completions=ns(create=create)))
    assert "cortado" in chat(fake_chat(None, fin="length"), "s", "u")
    for vacio in (fake_chat(None), ns(chat=ns(completions=ns(create=lambda **k: ns(choices=[]))))):
        try:
            chat(vacio, "s", "u")
            raise AssertionError("sin texto tiene que fallar, así entra el relevo")
        except RuntimeError:
            pass
    # 2.4: el error como lo lee una persona
    e401 = Exception("x"); e401.status_code = 401
    assert "inválida" in humano(e401) and "Cuota del día" in humano(tpd)
    assert "minuto" in humano(minuto) and humano(Exception("raro")) == "raro"
    assert "Sin conexión" in humano(type("APIConnectionError", (Exception,), {})())

    # 7.2 y 6.3: los tramos ven las notas y quién es quién hasta ahora, piden menos salida y
    # piensan en low; el acta recibe la fecha
    calls = []
    fc = fake_chat(["PERSONAS: Eric = \"yo los convoqué\"\n- DECIDIDO x",
                    "PERSONAS: Ana = \"nuestro local\"\n- y", "TÍTULO: T\n\nacta"], calls)
    fc.trozo, fc.tramo_out, fc.modelo = 30, TRAMO_OUT, CHAT
    assert _summarize(fc, "ver la fecha", "a" * 25 + "\n\n" + "b" * 25, None, "30/09/2026 10:15")
    tramo1, tramo2, final = (c["messages"][1]["content"] for c in calls)
    assert "NOTAS del usuario:\nver la fecha" in tramo1 and "HASTA AHORA" not in tramo1
    assert "HASTA AHORA" in tramo2 and "yo los convoqué" in tramo2
    assert calls[0]["max_completion_tokens"] == TRAMO_OUT and calls[-1]["max_completion_tokens"] == CHAT_OUT
    assert calls[0]["extra_body"] == {"reasoning_effort": "low"} and calls[-1]["extra_body"] is None
    assert final.startswith("FECHA DE LA REUNIÓN: 30/09/2026") and calls[0]["temperature"] == TEMP
    # el título viene en la primera línea del acta: un request menos
    assert split_title("TÍTULO: Junta de ventas\n\nQUIÉNES\n- x") == ("Junta de ventas", "QUIÉNES\n- x")
    assert split_title('**Título:** "Plan Q3".\nDECIDIDO') == ("Plan Q3", "DECIDIDO")
    assert split_title("QUIÉNES\n- x") == ("", "QUIÉNES\n- x")
    store_session({"id": "s10", "name": ""})
    store_summary("s10", "TÍTULO: Nuevo\n\nacta")
    assert (load_session("s10")["name"], load_session("s10")["summary"]) == ("Nuevo", "acta")
    _patch("s10", done=[1])
    store_summary("s10", "TÍTULO: Otro\n\nacta2")
    s10 = load_session("s10")
    assert s10["name"] == "Nuevo", "no pisa el nombre que ya tenía"
    assert "done" not in s10, "los tildes eran de otra acta"
    assert cuando({"id": "20260930_101500"}) == "30/09/2026 10:15 (miércoles)"

    # 4.7: las acciones del acta, contadas igual que md() en la ventana
    acta = ("QUIÉNES\n- Eric, convoca\nACCIONES\n**Eric**\n- mandar la **propuesta**\n"
            "- llamar a Ana\n**SIN DUEÑO - ASIGNAR**\n- definir fecha\nMENCIONADO\n- nada")
    assert acciones(acta) == [(0, "Eric", "mandar la propuesta"), (1, "Eric", "llamar a Ana"),
                              (2, "SIN DUEÑO - ASIGNAR", "definir fecha")], acciones(acta)
    assert acciones("### Acciones\n1. uno") == [(0, "", "uno")] and acciones(None) == []
    _patch("s10", summary=acta, name="Con acciones")
    assert api.toggle_done("s10", 1)["done"] == [1]
    assert [x["text"] for x in api.pending() if x["sid"] == "s10"] == ["mandar la propuesta",
                                                                        "definir fecha"]
    assert api.toggle_done("s10", 1)["done"] == []

    # 4.9: "Los demás" -> "Acme" solo en la etiqueta, nunca dentro de lo que se dijo
    store_session({"id": "s9", "turns": [{"t": 0, "who": "Los demás", "text": "hola"},
                                         {"t": 1, "who": "Yo", "text": "Los demás: no"}],
                   "transcript": "Los demás: hola\n\nYo: Los demás: no"})
    assert api.rename_speaker("s9", "Los demás", " Acme: SA ")["who"] == "Acme SA"
    s9 = load_session("s9")
    assert [t["who"] for t in s9["turns"]] == ["Acme SA", "Yo"]
    assert s9["transcript"] == "Acme SA: hola\n\nYo: Los demás: no", s9["transcript"]
    assert not api.rename_speaker("s9", "Yo", "  ")["ok"]

    # 7.3: la minuta sale del acta en un request y se guarda aparte
    _patch("s9", summary="DECIDIDO\n- x", name="Junta", dur=1800)
    calls = []
    api._chat = lambda: fake_chat("MINUTA DE REUNIÓN\n\nASISTENTES\n- Eric", calls)
    r = api.minuta("s9")
    s9 = load_session("s9")
    assert r["ok"] and s9["minuta"].startswith("MINUTA DE REUNIÓN") and "stage" not in s9
    assert "ACTA:\nDECIDIDO" in calls[0]["messages"][1]["content"] and len(calls) == 1
    assert "(Duración: 30 min)" in calls[0]["messages"][1]["content"]
    api._chat = lambda: fake_chat(None)                  # falla: se reporta, no se traga
    assert not api.minuta("s9")["ok"] and load_session("s9")["min_error"]
    del api._chat
    # 4.2: el acta sale de la app
    md_path = api.export_md("s9")["path"]
    assert md_path == os.path.join(NOTAS, "s9.md")
    txt = open(md_path, encoding="utf-8").read()
    assert txt.startswith("# Junta") and "## Minuta" in txt and "## Transcripción" in txt
    api.delete_session("s9")
    assert not os.path.exists(md_path), "borrar la sesión se lleva el .md"

    # 6.5: la lista cachea por archivo y relee solo el que cambió
    api2 = Api()
    antes = {m["id"]: m["name"] for m in api2.list_sessions()}
    _patch("s2", name="Renombrada desde el otro proceso")
    assert {m["id"]: m["name"] for m in api2.list_sessions()}["s2"] != antes["s2"]
    store_session({"id": "s11", "status": "done", "transcript": "x", "sum_error": "429"})
    assert [m["nosum"] for m in api2.list_sessions() if m["id"] == "s11"] == [True]
    os.remove(spath("s11"))
    assert "s11" not in [m["id"] for m in api2.list_sessions()]

    # 6.1: el audio se lee de a trozos; 25 min = 3 trozos con su tiempo base, y el .wav de
    # auditoría se mezcla en streaming con la duración del canal más largo
    tono = (0.3 * np.sin(2 * np.pi * 220 * np.arange(SR * 60) / SR)).astype("float32")
    largo, corto, mezcla = (os.path.join(NOTAS, f) for f in ("_l.wav", "_c.wav", "_m.wav"))
    for path, mins in ((largo, 25), (corto, 12)):
        w_ = _open_wav(path)
        for _ in range(mins):
            w_.writeframes(_to_i16(tono))
        w_.close()
    subidas, prog = [], []
    fw = ns(audio=ns(transcriptions=ns(create=lambda **k: subidas.append(1) or
                                        {"segments": [{"start": 1.0, "end": 2.0, "text": "hola"}]})))
    segs = transcribe_channel(fw, wav_chunks([largo]), "Yo", lambda: prog.append(1))
    assert len(subidas) == 3 and len(prog) == 3 == n_chunks(wav_frames(largo))
    assert [x[0] for x in segs] == [1.0, 601.0, 1201.0], segs
    write_mix(mezcla, [largo, corto])
    assert wav_frames(mezcla) == SR * 60 * 25
    for f in (largo, corto, mezcla):
        os.remove(f)
    # 6.2: un trozo mudo no se sube (cuota y alucinaciones), pero el progreso avanza igual
    subidas.clear(); prog.clear()
    assert transcribe_channel(fw, iter([np.zeros(SR * 30, "float32")]), "Yo",
                              lambda: prog.append(1)) == []
    assert subidas == [] and prog == [1]

    # de punta a punta con un Groq de mentira: la transcripción se guarda ANTES de tocar los
    # canales, el título sale del acta, y el .wav se escribe al final
    groq_real, env_real = Groq, _env_key
    globals()["_env_key"] = lambda name: ""              # que un GEMINI_API_KEY real no se cuele
    borrar = []

    def whisper(**k):
        for x in borrar:
            os.remove(spath(x))                           # la borran mientras se transcribe
        return {"segments": [{"start": 0.5, "end": 2.0, "text": "hola equipo"}]}
    fg = ns(audio=ns(transcriptions=ns(create=whisper)), chat=fake_chat("TÍTULO: Junta de prueba"
                                                                        "\n\nDECIDIDO\n- algo").chat)
    globals()["Groq"] = lambda **kw: fg
    for sid, quitar in (("20260930_101500", False), ("20260930_111500", True)):
        store_session({"id": sid, "name": "", "status": "pending", "notes": "", "dur": 3})
        for ch in ("mic", "loop"):
            write_mono(chan_path(sid, ch), tono[:SR * 3])
        borrar[:] = [sid] if quitar else []
        do_transcription(sid)
        assert not any(os.path.exists(chan_path(sid, ch)) for ch in ("mic", "loop"))
        if quitar:                                        # 1.3: ni sesión resucitada ni .wav huérfano
            assert not os.path.exists(spath(sid))
            assert not os.path.exists(os.path.join(NOTAS, f"{sid}.wav"))
            continue
        s_ = load_session(sid)
        assert s_["status"] == "done" and "stage" not in s_, s_
        assert s_["name"] == "Junta de prueba" and s_["summary"] == "DECIDIDO\n- algo"
        assert "hola equipo" in s_["transcript"]
        assert wav_frames(os.path.join(NOTAS, f"{sid}.wav")) == SR * 3
        api.delete_session(sid)
    do_transcription("noexiste")                          # 3.1: borrada antes de arrancar
    globals()["Groq"], globals()["_env_key"] = groq_real, env_real

    w = Widget(); w.root.withdraw()                # oculto: sin parpadeo en pantalla
    assert w._pal is LIGHT                         # el tema sale de settings.json (light arriba)
    w.label = "captura 1 ✓"                        # mensaje efímero de una captura
    w._loop(); assert w._shown[:10] == (False, "captura 1 ✓", "", False, None, False, 0, False,
                                        (False, False), "") and w._shown[11] is False
    w._clear("otro"); assert w.label == "captura 1 ✓", "no pisa un mensaje más nuevo"
    w._clear("captura 1 ✓")                        # y el suyo sí lo limpia
    w._loop(); assert w._shown[:8] == (False, "", "", False, None, False, 0, False), "repintar"
    w.jobs = 2                                     # grabar la próxima mientras estas transcriben
    w._loop(); assert w._shown[6] == 2
    w.jobs = 0
    w._animate(1, instant=True)                    # los controles solo existen abierto
    hits = []                                      # el clic dispara la acción y marca hundido
    w._hit("rec", lambda: hits.append(1))
    assert hits == [1] and w._shown[4] == "rec"
    w._unpress(); assert w._shown[4] is None
    assert not w.c.find_withtag("keep")
    w.idle = "warn"; w._loop()                     # el chip ¿seguir? existe y es clickable
    assert w.c.find_withtag("keep") and w.c.tag_bind("keep", "<Button-1>")
    # TODOS los botones están siempre dibujados y ligados: el pill no cambia de forma al grabar
    for tag in ("note", "gear", "expand", "quit", "rec", "mic", "shot", "pause", "drag"):
        assert w.c.find_withtag(tag) and (tag == "drag" or w.c.tag_bind(tag, "<Button-1>")), tag
    w._pal = DARK; w._loop(); assert w._shown[3] is True       # cambiar el tema repinta

    tocado = []                                    # parado, los de sesión están apagados: inertes
    for tag in Widget.NEEDS_REC:
        w._hit(tag, lambda t=tag: tocado.append(t))
        assert tocado == [] and w._pressed is None, tag
        assert w.label == "dale grabar primero", "y tiene que decir por qué"
    w._clear("dale grabar primero")

    # arrastrar: mientras se arrastra NO se puede repintar, o Tk corta el <B1-Motion>
    w._press(type("E", (), {"x_root": 500, "y_root": 300})())
    assert w._dragging
    antes = w.c.find_withtag("rec")
    w._loop(); assert w.c.find_withtag("rec") == antes, "repintó en medio del arrastre"
    w._drop(); assert not w._dragging
    w._loop(); assert w.c.find_withtag("rec") != antes, "al soltar tiene que volver a repintar"

    w.recording, w.rec, w.t0 = True, Recorder("m.wav", "l.wav"), time.time() - 5.5
    w._loop()
    w._mute()                                      # micro cortado: solo ese canal deja de escribir
    assert w.rec.mute_evt.is_set() and w.rec.mic_off and w._muted()
    w._loop(); assert w._shown[7] is True          # y el tachado se ve
    w._mute(); assert not w.rec.mute_evt.is_set() and w.rec.mic_off   # mic_off no se deshace:
    w._loop(); assert w._shown[7] is False         # el aviso debe decir "desactivado", no "mudo"
    w.rec.last_sound = 0                           # como si nadie hablara hace horas
    w._pause()                                     # pausado: el reloj y el aviso se congelan
    assert w.pause_t0 and w.rec.pause_evt.is_set() and w._fmt() == "00:00:05"
    w._loop()                                      # sin la pausa, este tick autodetendría
    assert w.recording and w.idle == "" and w._shown[5] is True
    w._pause()                                     # reanudar: sigue donde estaba
    assert not w.pause_t0 and not w.rec.pause_evt.is_set() and w._fmt() == "00:00:05"
    assert idle_state(time.time() - w.rec.last_sound) == ""    # la pausa no cuenta como silencio
    # nivel en vivo: la barrita del canal que tiene voz se enciende
    w.rec.loop_level = 0.05
    w._loop(); assert w._shown[8] == (False, True), w._shown
    w.rec.mic_level = 0.05; w.rec.mute_evt.set()
    w._loop(); assert w._shown[8] == (False, True), "micro cortado: su barrita no se enciende"
    w.rec.mute_evt.clear()
    w._loop(); assert w._shown[8] == (True, True)
    # canal sin un solo bloque con voz tras NOSIGNAL s: se avisa en vivo, no al terminar
    assert w._no_signal() == "", "antes de NOSIGNAL no se avisa"
    w.t0 = time.time() - NOSIGNAL - 1
    w._loop(); assert w._shown[9] == "sin audio de la PC"
    w.rec.loop_voice = 1
    assert w._no_signal() == "", "micro cortado a propósito: no es falta de señal"
    w.rec.mic_off = False
    assert w._no_signal() == "micrófono sin señal"
    w.rec.mic_voice = 1
    assert w._no_signal() == ""
    # tooltips: por coordenadas, así que sobreviven al repintado de cada tick
    assert w._tip_at(Widget.REC, 26) == "rec" and w._tip_at(Widget.GEAR, 27) == "gear"
    assert w._tip_at(Widget.LVL + 2, 20) == "lvl" and w._tip_at(Widget.GRIP, 27) is None
    assert all(hasattr(Widget, k.upper()) for k in TIPS), "cada tooltip necesita su x"
    w._set_tip("gear"); w._show_tip()
    assert w._tip.winfo_exists() and "Configuración" in TIPS["gear"]
    w._set_tip(None); assert w._tip is None and w._tip_job is None
    toggles = []                                   # el atajo global llega a toggle() en el hilo de Tk
    w.toggle = lambda: toggles.append(1)
    w._hot = True; w._loop()
    assert not w._hot and toggles == [1]
    del w.toggle
    w.recording, w.rec, w.jobs = False, None, 0
    assert w._take_notes() == ""                   # sin bloc abierto, no hay notas
    w._open_notes(); w.notes_win.withdraw()        # el bloc alimenta la sesión y queda vacío
    w.notes_box.insert("1.0", "ojo con el gasto")
    assert w._take_notes() == "ojo con el gasto" and w._take_notes() == ""
    # notch: plegado no hay controles, solo la pestañita
    w._animate(0, instant=True)
    assert w.c.find_withtag("pill") and not w.c.find_withtag("rec")
    assert w._tip_at(Widget.REC, 26) is None, "plegado no hay tooltips"
    w._peek(); assert w._anim and w._fold_job      # algo nuevo que ver: abre y arma el plegado
    w._animate(1, instant=True); w._cancel_fold()
    w.idle = "warn"; w._fold(); assert w._open == 1 and not w._anim, "ocupado no se pliega"
    w.idle = ""; w._fold(); assert w._anim, "limpio sí"
    w._animate(0, instant=True); assert w._open == 0
    w._animate(1, instant=True); w._pinned = True
    w._fold(); assert w._open == 1 and not w._anim, "fijado nunca"
    w._pinned = False
    # los cuatro bordes: cada control cae dentro de la ventana y el tooltip lo encuentra
    w.recording = True
    for e in EDGES:
        w.edge = e; w._place(); w._animate(1, instant=True)
        cw, ch = w._size()
        assert (cw < ch) == (e in ("left", "right")), e
        for tag in ("rec", "mic", "shot", "pause", "note", "gear", "expand", "quit", "drag"):
            x1, y1, x2, y2 = w.c.bbox(tag)
            assert -1 <= x1 and x2 <= cw + 1 and -1 <= y1 and y2 <= ch + 1, (e, tag, w.c.bbox(tag))
        for tag in ("rec", "gear", "lvl"):
            assert w._tip_at(*w._from(*w._pt(w._ax(tag), 26))) == tag, (e, tag)
    w.recording = False
    w.edge = "top"; w._place()
    # «Cerrar TakeMyNotes» manda WM_CLOSE: tiene que pasar por _quit, no destruir a lo bruto
    assert w.root.protocol("WM_DELETE_WINDOW"), "sin protocolo, WM_CLOSE cerraría grabando"
    w.root.destroy()
    logging.disable(logging.NOTSET)
    print("selftest ok")


if __name__ == "__main__":
    # Accesos del menú Inicio (ver installer.iss): TakeMyNotes = el widget, --window = sesiones,
    # --settings = Configuración, --quit = cerrar todo
    if "--selftest" in sys.argv:
        selftest()
    elif "--quit" in sys.argv:
        quit_all()
    elif "--settings" in sys.argv:
        launch_window("settings")
    else:
        win = "--window" in sys.argv
        setup_log("window" if win else "widget")
        log.info("inicio")
        try:
            if win:
                window_main()
            elif take_lock(WIDGET_MUTEX):   # el handle vive lo que vive el proceso: lo suelta el SO
                Widget().run()
            else:
                log.info("ya hay un widget abierto")   # abrir la app dos veces no da dos pills
        except Exception:
            log.exception("crash")    # sin consola, morir en silencio no es opción
            raise
