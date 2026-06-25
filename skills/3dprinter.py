"""3D printer integration — control your printer by voice.

Drop-in Eve skill (no core changes). Speaks to your printer over its local
network API, behind a small backend abstraction so the same voice commands work
across firmware stacks. Ships with two backends:

  - "prusa"  — PrusaLink local HTTP API (MK4 / XL / Mini / MK3.9 w/ PrusaLink).
               stdlib only, authenticates with the PrusaLink API key.
  - "bambu"  — Bambu Lab printers over local MQTT (LAN mode). Needs
               `pip install paho-mqtt` and the printer's access code + serial.

Adding a third stack (OctoPrint, Moonraker/Klipper, …) is just another
`PrinterBackend` subclass + an entry in `_BACKENDS` — the voice layer below is
backend-agnostic because every backend returns the same normalized dicts.

──────────────────────────────────────────────────────────────────────────────
Setup — add a "printer" block to settings.json at the repo root:

  PrusaLink:
    "printer": {
      "type": "prusa",
      "host": "192.168.1.50",          // printer IP or hostname (no http://)
      "api_key": "abcdef123456"        // PrusaLink → Settings → API key
    }

  Bambu Lab (LAN mode must be enabled on the printer):
    "printer": {
      "type": "bambu",
      "host": "192.168.1.60",          // printer IP
      "serial": "01PXXXXXXXXXXXX",     // device serial
      "access_code": "12345678"        // LAN access code (printer screen)
    }

Then restart Eve. Try: "how's my print", "how long left", "what's the nozzle
temp", "pause the print", "resume printing", "cancel the print" (asks to
confirm), "preheat for PETG", "cool down the printer".
──────────────────────────────────────────────────────────────────────────────
"""
import json
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from core.response import Silent, Verified

_SETTINGS_FILE = Path(__file__).parent.parent / "settings.json"

# Per-material preheat targets (nozzle, bed) in °C. Conservative defaults.
_MATERIALS = {
    "pla":   (215, 60),
    "petg":  (240, 85),
    "abs":   (255, 100),
    "asa":   (260, 100),
    "tpu":   (220, 50),
    "nylon": (260, 90),
    "pc":    (270, 110),
}
_DEFAULT_MATERIAL = "pla"

# Normalized printer states the voice layer phrases against.
PRINTING, PAUSED, IDLE, FINISHED, ERROR, OFFLINE = (
    "printing", "paused", "idle", "finished", "error", "offline")


# ── config ─────────────────────────────────────────────────────────────────

def _config() -> dict:
    """Read the `printer` block from settings.json. {} if absent/unreadable."""
    try:
        data = json.loads(_SETTINGS_FILE.read_text())
        cfg = data.get("printer") or {}
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


# ── backend abstraction ────────────────────────────────────────────────────

class PrinterError(Exception):
    """Raised by a backend when the printer can't be reached or refuses an
    action. Handlers catch this and speak a friendly line instead of letting
    the skill loader treat the raise as a non-match."""


class PrinterBackend:
    """A printer Eve can talk to. Every method either succeeds or raises
    PrinterError. Read methods return normalized dicts so the voice layer is
    backend-agnostic:

        status() -> {"state": <one of the module constants>,
                     "job": <str|None>, "progress": <float 0-100|None>,
                     "time_left_s": <int|None>}
        temps()  -> {"nozzle": float|None, "nozzle_target": float|None,
                     "bed":    float|None, "bed_target":    float|None}
    """

    def status(self) -> dict:        raise NotImplementedError
    def temps(self) -> dict:         raise NotImplementedError
    def pause(self) -> None:         raise NotImplementedError
    def resume(self) -> None:        raise NotImplementedError
    def cancel(self) -> None:        raise NotImplementedError

    def set_temps(self, nozzle=None, bed=None) -> None:
        """Set target temps (°C). None leaves that heater untouched."""
        raise NotImplementedError


# ── PrusaLink (HTTP) ───────────────────────────────────────────────────────

_PRUSA_STATE = {
    "PRINTING": PRINTING, "BUSY": PRINTING, "ATTENTION": PRINTING,
    "PAUSED": PAUSED,
    "IDLE": IDLE, "READY": IDLE, "STOPPED": IDLE,
    "FINISHED": FINISHED,
    "ERROR": ERROR,
}


class PrusaBackend(PrinterBackend):
    """PrusaLink local API v1, with the legacy OctoPrint-compatible endpoints
    for setting temperatures (PrusaLink exposes both)."""

    def __init__(self, cfg: dict):
        self.host = (cfg.get("host") or "").strip().rstrip("/")
        self.key = (cfg.get("api_key") or "").strip()
        if not self.host:
            raise PrinterError("No printer host is set in settings.json.")

    def _req(self, path: str, method: str = "GET", body=None, _retry=True):
        url = f"http://{self.host}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if self.key:
            req.add_header("X-Api-Key", self.key)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=6) as r:
                raw = (r.read() or b"").decode("utf-8", "replace").strip()
            return json.loads(raw) if raw[:1] in ("{", "[") else {}
        except urllib.error.HTTPError as e:
            # Some PrusaLink builds want POST where the spec says PUT (or vice
            # versa) for job control — retry once with the other verb on 405.
            if e.code == 405 and _retry and method in ("PUT", "POST"):
                other = "POST" if method == "PUT" else "PUT"
                return self._req(path, other, body, _retry=False)
            if e.code in (401, 403):
                raise PrinterError("The printer rejected the API key.")
            raise PrinterError(f"The printer returned an error ({e.code}).")
        except (urllib.error.URLError, OSError, TimeoutError):
            raise PrinterError("I couldn't reach the printer.")

    def _job_id(self):
        st = self._req("/api/v1/status")
        return ((st.get("job") or {}).get("id")), st

    def status(self) -> dict:
        st = self._req("/api/v1/status")
        printer = st.get("printer") or {}
        job = st.get("job") or {}
        name = None
        if job.get("id") is not None:
            j = self._req("/api/v1/job")
            f = j.get("file") or {}
            name = f.get("display_name") or f.get("name")
            job = {**job, **{k: v for k, v in j.items() if k != "file"}}
        return {
            "state": _PRUSA_STATE.get((printer.get("state") or "").upper(), IDLE),
            "job": name,
            "progress": _as_float(job.get("progress")),
            "time_left_s": _as_int(job.get("time_remaining")),
        }

    def temps(self) -> dict:
        printer = (self._req("/api/v1/status").get("printer")) or {}
        return {
            "nozzle":        _as_float(printer.get("temp_nozzle")),
            "nozzle_target": _as_float(printer.get("target_nozzle")),
            "bed":           _as_float(printer.get("temp_bed")),
            "bed_target":    _as_float(printer.get("target_bed")),
        }

    def _job_action(self, verb: str):
        jid, _ = self._job_id()
        if jid is None:
            raise PrinterError("There's no active print.")
        if verb == "stop":
            self._req(f"/api/v1/job/{jid}", "DELETE")
        else:
            self._req(f"/api/v1/job/{jid}/{verb}", "PUT")

    def pause(self):  self._job_action("pause")
    def resume(self): self._job_action("resume")
    def cancel(self): self._job_action("stop")

    def set_temps(self, nozzle=None, bed=None):
        # Legacy OctoPrint-compatible endpoints PrusaLink also serves.
        if nozzle is not None:
            self._req("/api/printer/tool", "POST",
                      {"command": "target", "targets": {"tool0": int(nozzle)}})
        if bed is not None:
            self._req("/api/printer/bed", "POST",
                      {"command": "target", "target": int(bed)})


# ── Bambu Lab (MQTT, LAN mode) ─────────────────────────────────────────────

_BAMBU_STATE = {
    "RUNNING": PRINTING, "PREPARE": PRINTING, "SLICING": PRINTING,
    "PAUSE": PAUSED,
    "IDLE": IDLE,
    "FINISH": FINISHED,
    "FAILED": ERROR,
}
_seq_lock = threading.Lock()
_seq_n = 0


def _seq() -> str:
    global _seq_n
    with _seq_lock:
        _seq_n += 1
        return str(_seq_n)


class BambuBackend(PrinterBackend):
    """Bambu Lab over local MQTT. The printer must have LAN mode enabled.
    Each call opens a short-lived TLS MQTT session (Bambu pushes a full report
    in response to a `pushall` request), so there's no long-lived connection to
    babysit — fine for the low frequency of voice commands."""

    def __init__(self, cfg: dict):
        self.host = (cfg.get("host") or "").strip()
        self.serial = (cfg.get("serial") or "").strip()
        self.code = (cfg.get("access_code") or "").strip()
        if not (self.host and self.serial and self.code):
            raise PrinterError(
                "Bambu needs host, serial, and access_code in settings.json.")

    def _client(self):
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            raise PrinterError(
                "The Bambu backend needs paho-mqtt. Run pip install paho-mqtt.")
        try:  # paho 2.x requires an explicit callback API version
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        except (AttributeError, TypeError):
            c = mqtt.Client()
        c.username_pw_set("bblp", self.code)
        c.tls_set(cert_reqs=ssl.CERT_NONE)
        c.tls_insecure_set(True)
        try:
            c.connect(self.host, 8883, keepalive=20)
        except (OSError, TimeoutError):
            raise PrinterError("I couldn't reach the printer over the network.")
        return c

    @property
    def _report_topic(self):  return f"device/{self.serial}/report"
    @property
    def _request_topic(self): return f"device/{self.serial}/request"

    def _query(self, timeout: float = 6.0) -> dict:
        """Connect, ask for a full push, merge incoming `print` reports until we
        have a usable snapshot (or timeout). Returns the merged `print` dict."""
        merged: dict = {}
        ready = threading.Event()

        def on_connect(client, *_):
            client.subscribe(self._report_topic)
            client.publish(self._request_topic, json.dumps(
                {"pushing": {"sequence_id": _seq(), "command": "pushall"}}))

        def on_message(client, _userdata, msg):
            try:
                pr = (json.loads(msg.payload) or {}).get("print")
            except Exception:
                return
            if pr:
                merged.update(pr)
                if "gcode_state" in merged:
                    ready.set()

        c = self._client()
        c.on_connect = on_connect
        c.on_message = on_message
        c.loop_start()
        try:
            ready.wait(timeout)
        finally:
            c.loop_stop()
            c.disconnect()
        if not merged:
            raise PrinterError("The printer didn't respond in time.")
        return merged

    def _command(self, payload: dict):
        c = self._client()
        c.loop_start()
        try:
            info = c.publish(self._request_topic, json.dumps(payload))
            try:
                info.wait_for_publish(timeout=4)
            except Exception:
                pass
            time.sleep(0.4)  # let the TLS buffer flush before we tear down
        finally:
            c.loop_stop()
            c.disconnect()

    def status(self) -> dict:
        pr = self._query()
        mins = _as_int(pr.get("mc_remaining_time"))
        return {
            "state": _BAMBU_STATE.get((pr.get("gcode_state") or "").upper(), IDLE),
            "job": pr.get("subtask_name") or pr.get("gcode_file") or None,
            "progress": _as_float(pr.get("mc_percent")),
            "time_left_s": (mins * 60) if mins is not None else None,
        }

    def temps(self) -> dict:
        pr = self._query()
        return {
            "nozzle":        _as_float(pr.get("nozzle_temper")),
            "nozzle_target": _as_float(pr.get("nozzle_target_temper")),
            "bed":           _as_float(pr.get("bed_temper")),
            "bed_target":    _as_float(pr.get("bed_target_temper")),
        }

    def _print_cmd(self, command: str):
        self._command({"print": {"command": command, "sequence_id": _seq()}})

    def pause(self):  self._print_cmd("pause")
    def resume(self): self._print_cmd("resume")
    def cancel(self): self._print_cmd("stop")

    def set_temps(self, nozzle=None, bed=None):
        lines = []
        if nozzle is not None:
            lines.append(f"M104 S{int(nozzle)}")
        if bed is not None:
            lines.append(f"M140 S{int(bed)}")
        for line in lines:
            self._command({"print": {"command": "gcode_line",
                                     "sequence_id": _seq(), "param": line + "\n"}})


_BACKENDS = {"prusa": PrusaBackend, "bambu": BambuBackend}


def _backend() -> PrinterBackend:
    """Build the configured backend, or raise PrinterError with guidance."""
    cfg = _config()
    if not cfg:
        raise PrinterError(
            "No printer is set up. Add a printer block to settings.json with a "
            "type, host, and credentials.")
    kind = (cfg.get("type") or "").strip().lower()
    cls = _BACKENDS.get(kind)
    if cls is None:
        opts = " or ".join(sorted(_BACKENDS))
        raise PrinterError(f"Unknown printer type '{kind}'. Set it to {opts}.")
    return cls(cfg)


# ── small coercion / phrasing helpers ──────────────────────────────────────

def _as_float(v):
    try:    return float(v)
    except (TypeError, ValueError): return None

def _as_int(v):
    try:    return int(round(float(v)))
    except (TypeError, ValueError): return None


def _say_duration(seconds) -> str:
    if not seconds or seconds < 0:
        return "less than a minute"
    mins = int(round(seconds / 60))
    if mins < 1:
        return "less than a minute"
    if mins < 60:
        return f"{mins} minute{'s' if mins != 1 else ''}"
    h, m = divmod(mins, 60)
    if m == 0:
        return f"{h} hour{'s' if h != 1 else ''}"
    return f"{h} hour{'s' if h != 1 else ''} and {m} minute{'s' if m != 1 else ''}"


def _say_temp(label, cur, target) -> str:
    if cur is None:
        return f"I couldn't read the {label} temperature."
    cur_i = int(round(cur))
    if target and target > 0 and abs(target - cur) >= 3:
        return f"The {label} is at {cur_i} degrees, heading to {int(round(target))}."
    return f"The {label} is at {cur_i} degrees."


# ── verification helpers ───────────────────────────────────────────────────
# After a control command, re-query the printer to confirm the state/targets
# actually changed. Backend calls are reused (one instance per command) and any
# PrinterError counts as "not confirmed yet".

def _safe(fn) -> None:
    try:
        fn()
    except Exception:
        pass


def _state_is(b, want) -> bool:
    try:
        return b.status().get("state") == want
    except PrinterError:
        return False


def _not_printing(b) -> bool:
    try:
        return b.status().get("state") != PRINTING
    except PrinterError:
        return False


def _targets_near(b, nozzle=None, bed=None, tol=5) -> bool:
    try:
        t = b.temps()
    except PrinterError:
        return False
    if nozzle is not None:
        v = t.get("nozzle_target")
        if v is None or abs(v - nozzle) > tol:
            return False
    if bed is not None:
        v = t.get("bed_target")
        if v is None or abs(v - bed) > tol:
            return False
    return True


# ── voice handlers (backend-agnostic) ──────────────────────────────────────

def _status() -> str:
    try:
        s = _backend().status()
    except PrinterError as e:
        return str(e)
    state = s["state"]
    if state == PRINTING:
        parts = []
        if s.get("job"):
            parts.append(f"Printing {s['job']}.")
        else:
            parts.append("A print is running.")
        if s.get("progress") is not None:
            parts.append(f"{int(round(s['progress']))} percent done.")
        if s.get("time_left_s") is not None:
            parts.append(f"About {_say_duration(s['time_left_s'])} left.")
        return " ".join(parts)
    if state == PAUSED:
        return "The print is paused."
    if state == FINISHED:
        return "The print is finished."
    if state == ERROR:
        return "The printer is reporting an error — better check it."
    return "The printer is idle. Nothing is printing right now."


def _time_left() -> str:
    try:
        s = _backend().status()
    except PrinterError as e:
        return str(e)
    if s["state"] != PRINTING:
        return "Nothing is printing right now."
    if s.get("time_left_s") is None:
        return "I couldn't get a time estimate from the printer."
    return f"About {_say_duration(s['time_left_s'])} left on the print."


def _temps() -> str:
    try:
        t = _backend().temps()
    except PrinterError as e:
        return str(e)
    return (_say_temp("nozzle", t["nozzle"], t["nozzle_target"]) + " " +
            _say_temp("bed", t["bed"], t["bed_target"]))


def _pause() -> str:
    try:
        b = _backend()
        b.pause()
    except PrinterError as e:
        return str(e)
    return Verified(
        "Paused the print.",
        check=lambda: _state_is(b, PAUSED),
        on_fail="I sent pause, but the printer still shows it printing.",
        retry=lambda: _safe(b.pause),
        delay=2.0,
    )


def _resume() -> str:
    try:
        b = _backend()
        b.resume()
    except PrinterError as e:
        return str(e)
    return Verified(
        "Resumed the print.",
        check=lambda: _state_is(b, PRINTING),
        on_fail="I sent resume, but the printer still shows it paused.",
        retry=lambda: _safe(b.resume),
        delay=2.0,
    )


def _do_cancel() -> str:
    try:
        b = _backend()
        b.cancel()
    except PrinterError as e:
        return str(e)
    return Verified(
        "Cancelled the print.",
        check=lambda: _not_printing(b),
        on_fail="I sent the cancel, but the printer still shows it printing.",
        retry=lambda: _safe(b.cancel),
        delay=2.5,
    )


def _cancel() -> str:
    """Route through Eve's single-turn yes/no confirmation before stopping —
    cancelling a print is destructive and irreversible."""
    try:
        import core.session as _sess_mod
        _sess_mod.get().pending_confirm = (_do_cancel, (), "cancel the print")
        return Silent("Cancel the print? Say yes to confirm.")
    except Exception:
        return _do_cancel()


def _preheat(material=None) -> str:
    mat = (material or _DEFAULT_MATERIAL).strip().lower()
    nozzle, bed = _MATERIALS.get(mat, _MATERIALS[_DEFAULT_MATERIAL])
    try:
        b = _backend()
        b.set_temps(nozzle=nozzle, bed=bed)
    except PrinterError as e:
        return str(e)
    label = mat.upper() if mat in _MATERIALS else _DEFAULT_MATERIAL.upper()
    return Verified(
        f"Preheating for {label} — nozzle {nozzle}, bed {bed} degrees.",
        check=lambda: _targets_near(b, nozzle=nozzle, bed=bed),
        on_fail=f"I set the targets for {label}, but the printer didn't take them.",
        retry=lambda: _safe(lambda: b.set_temps(nozzle=nozzle, bed=bed)),
        delay=2.0,
    )


def _cooldown() -> str:
    try:
        b = _backend()
        b.set_temps(nozzle=0, bed=0)
    except PrinterError as e:
        return str(e)
    return Verified(
        "Cooling the printer down.",
        check=lambda: _targets_near(b, nozzle=0, bed=0),
        on_fail="I sent the cooldown, but the heaters are still set.",
        retry=lambda: _safe(lambda: b.set_temps(nozzle=0, bed=0)),
        delay=2.0,
    )


# ── intents ────────────────────────────────────────────────────────────────
# Order matters within this list (first match wins). Specific phrasings —
# time-left, temps, preheat, cooldown — come before the generic status catch.
_MAT = "|".join(_MATERIALS)

INTENTS = [
    # how long left
    (r"\bhow (?:long|much time)(?:'?s| is| has)?\s*(?:left|remaining|to go)\b", _time_left),
    (r"\b(?:time (?:left|remaining)|when (?:will|does).*(?:finish|done))\b",    _time_left),

    # temperatures
    (r"\b(?:what(?:'s| is)?\s*the\s*)?(?:nozzle|hot ?end|bed|printer)\s*temp(?:erature)?\b", _temps),
    (r"\b(?:is the )?(?:nozzle|hot ?end|bed)\s*(?:hot|up to temp)\b",            _temps),
    (r"\bprinter temp(?:erature)?s?\b",                                          _temps),

    # print control
    (r"\b(?:pause|hold)\s+(?:the\s+)?(?:print|printer|job)\b",                   _pause),
    (r"\b(?:resume|continue|unpause)\s+(?:the\s+)?(?:print(?:ing|er)?|job)\b",   _resume),
    (r"\b(?:cancel|stop|abort)\s+(?:the\s+)?(?:print(?:ing|er)?|job)\b",         _cancel),

    # preheat / cooldown. The optional trailing group captures the material
    # ("preheat for PETG"); a bare "preheat" leaves it None → defaults to PLA.
    (rf"\b(?:pre[\s-]?heat|warm up)(?:\s+the\s+printer)?(?:.*?\bfor\s+({_MAT}))?\b", _preheat),
    (r"\b(?:cool ?down|cool (?:it|the printer) down|turn off the heaters?)\b",   _cooldown),

    # status (generic — keep last so the specifics above win)
    (r"\b(?:how(?:'s| is)\s+(?:my|the)\s+print|print(?:er)?\s+status|"
     r"what(?:'s| is)\s+(?:printing|on the printer)|is (?:it|the print) done)\b", _status),
]


def setup(display=None) -> None:
    """Log which backend is configured (helps when nothing responds)."""
    cfg = _config()
    if not cfg:
        print("[skills] printer: no 'printer' block in settings.json - "
              "skill is idle until configured.")
    else:
        print(f"[skills] printer: backend = {cfg.get('type', '?')} "
              f"@ {cfg.get('host', '?')}")
