"""Local LLM fallback with tool calling. When no built-in intent and no skill
matches, a local model gets one shot at the utterance:

  - If it's an *actionable* request the regex missed ("could you throw firefox
    on my left screen"), the model emits a tool call that maps to a real Eve
    handler (`apps.open_app`, `tiling.snap_app`, …) and we execute it. This is
    what turns "not recognized" into "handled weird phrasing."
  - Otherwise it just answers in a sentence or two (general Q&A).

Protocol: OpenAI chat-completions against config.LLM_BASE_URL — the one shape
llama-swap, bare llama-server (--jinja), Ollama (/v1) and LM Studio all speak.
Default target is llama-swap (config.LLM_MODEL names a llama-swap.yaml entry).
Any failure — server down, model missing, no tool support, timeout — degrades
gracefully: tool-calling falls back to plain answering, and plain answering
falls back to None so dispatch() shows its normal reply. Nothing ever hangs or
crashes the pipeline.

Dynamic Intent Learning: every VERIFIED successful tool call is captured as a
learned candidate (core/intent_learning.py → learned_intents.json), and
`learned_answer()` serves repeat/similar phrasings locally so the LLM grows
rare over time. Dispatch order: learned tier first, then the LLM.
"""
import json
import urllib.request
import urllib.error

import config

_TIMEOUT_S = 45  # covers llama-swap cold-loading the model + CPU first token

_SYSTEM = (
    "You are Eve, a local Windows voice assistant. If the user is asking you to "
    "DO something (open/close an app, move or snap a window, search the web, go "
    "to a site, play a video, set a timer or reminder, bring a window to front), "
    "call the matching tool. Otherwise just answer in one or two short spoken "
    "sentences — no markdown, no lists. Only call a tool when you are confident."
)

# ── Tool schemas (OpenAI function-calling shape) ─────────────────────────────

def _fn(name, desc, props, required):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required}}}

_TOOLS = [
    _fn("open_app", "Open or launch an application by name (firefox, spotify, discord, …).",
        {"name": {"type": "string", "description": "the application name"}}, ["name"]),
    _fn("close_app", "Close an application by name.",
        {"name": {"type": "string"}}, ["name"]),
    _fn("snap_window", "Snap/move a window to a tiling zone, optionally on a specific monitor.",
        {"app": {"type": "string", "description": "window/app name"},
         "zone": {"type": "string", "description": "zone like top, bottom, left, right, top-left, full"},
         "monitor": {"type": "string", "description": "optional, e.g. '2', 'primary', 'left'"}},
        ["app", "zone"]),
    _fn("bring_to_front", "Raise a window to the front without stealing focus.",
        {"app": {"type": "string"}}, ["app"]),
    _fn("web_search", "Search the web and show results.",
        {"query": {"type": "string"}}, ["query"]),
    _fn("go_to_site", "Open a website / URL in the browser.",
        {"url": {"type": "string"}}, ["url"]),
    _fn("play_youtube", "Search or play something on YouTube.",
        {"query": {"type": "string"}}, ["query"]),
    _fn("set_timer", "Start a countdown timer for a number of minutes.",
        {"minutes": {"type": "integer"}}, ["minutes"]),
    _fn("set_reminder", "Set a reminder. Include the natural-language time in the task if given.",
        {"task": {"type": "string", "description": "e.g. 'call mom at 3pm'"}}, ["task"]),
]


# ── Tool handlers: (callable(args)->response, feature_gate_key|None) ─────────

def _h_open_app(a):       from commands import apps;      return apps.open_app(str(a.get("name", "")))
def _h_close_app(a):      from commands import apps;      return apps.close_app(str(a.get("name", "")))
def _h_snap(a):           from commands import tiling;    return tiling.snap_app(str(a.get("app", "")), str(a.get("zone", "full")), a.get("monitor") or None)
def _h_front(a):          from commands import window_manager as wm; return wm.bring_to_front(str(a.get("app", "")))
def _h_search(a):         from commands import search;    return search.web_search_list(str(a.get("query", "")))
def _h_goto(a):           from commands import search;    return search.go_to_site(str(a.get("url", "")))
def _h_youtube(a):        from core import skills;        return skills.dispatch_preempt("play " + str(a.get("query", ""))) or "YouTube isn't available right now."
def _h_timer(a):          from commands import reminders; return reminders.set_timer(str(a.get("minutes", "")))
def _h_remind(a):         from commands import context;   return context.remind(str(a.get("task", "")))

_TOOL_HANDLERS = {
    "open_app":       (_h_open_app, "apps"),
    "close_app":      (_h_close_app, "apps"),
    "snap_window":    (_h_snap, "tiling"),
    "bring_to_front": (_h_front, None),
    "web_search":     (_h_search, "web_search"),
    "go_to_site":     (_h_goto, "web_search"),
    "play_youtube":   (_h_youtube, "youtube"),
    "set_timer":      (_h_timer, "reminders"),
    "set_reminder":   (_h_remind, "reminders"),
}


def _enabled() -> bool:
    from core import llm_host
    return bool(llm_host.settings()["enabled"])


# ── busy-aware model choice ──────────────────────────────────────────────────
# While a game/protected app is foreground (core/essential.py — the same
# signal the focus policy uses) or RAM is tight, request the mini CPU model so
# Eve never contends with the game for GPU/VRAM; llama-swap does the actual
# swap/unload. All knobs in settings.json "llm" (see core/llm_host.DEFAULTS).

def _ram_load_pct() -> int:
    """System memory load 0–100 via GlobalMemoryStatusEx; 0 on any failure."""
    try:
        import ctypes
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(st)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return int(st.dwMemoryLoad)
    except Exception:
        pass
    return 0


def _busy(s: dict) -> bool:
    try:
        from core import essential
        if essential.should_defer():
            return True
    except Exception:
        pass
    return _ram_load_pct() >= int(s["busy_ram_pct"])


def _model() -> str:
    from core import llm_host
    s = llm_host.settings()
    if s["swap_when_busy"] and _busy(s):
        return s["model_mini"]
    return s["model"]


# ── HTTP (OpenAI chat-completions) ───────────────────────────────────────────

def _post(body: dict):
    from core import llm_host
    req = urllib.request.Request(
        f"{llm_host.settings()['base_url'].rstrip('/')}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def _message(data):
    """choices[0].message from a chat-completions response, or None."""
    try:
        return (data.get("choices") or [{}])[0].get("message") or None
    except (AttributeError, IndexError, TypeError):
        return None


def _run_tool(name: str, args):
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    if not isinstance(args, dict):
        args = {}
    entry = _TOOL_HANDLERS.get(name)
    if not entry:
        return None, args
    handler, feature = entry
    if feature:
        from core import features
        if not features.get(feature):
            return None, args
    try:
        return handler(args), args
    except Exception:
        return None, args


def _chat(text: str):
    """Tool-capable chat. Returns a handler result, a spoken answer, or None.
    Falls back to plain generation if the tools endpoint isn't usable.
    Verified successful tool calls are captured as learned candidates."""
    data = _post({
        "model": _model(),
        "messages": [{"role": "system", "content": _SYSTEM},
                     {"role": "user", "content": text}],
        "tools": _TOOLS,
    })
    if data is None:
        return _plain(text)
    msg = _message(data)
    if msg is None:
        return _plain(text)
    calls = msg.get("tool_calls") or []
    if calls:
        fn = (calls[0].get("function") or {})
        result, args = _run_tool(fn.get("name"), fn.get("arguments") or {})
        if result is not None:
            from core import intent_learning
            if intent_learning.verify_for_learning(result, None):
                intent_learning.learned().capture(text, fn.get("name"), args)
            return result
    content = (msg.get("content") or "").strip()
    return content or None


def _plain(text: str):
    """General Q&A with no tools — the floor for models/servers that don't
    support tool calling."""
    data = _post({
        "model": _model(),
        "messages": [
            {"role": "system", "content": "You are Eve. Answer in one or two "
             "short spoken sentences. No markdown, no lists."},
            {"role": "user", "content": text},
        ],
    })
    msg = _message(data) if data else None
    if msg is None:
        return None
    return (msg.get("content") or "").strip() or None


# ── Learned tier — serve captured mappings locally, before the LLM ──────────

def learned_answer(text: str):
    """The learned tiers, in the user's mandated order: IMPORTED packs
    (someone else's trained intents) first, then the PERSONAL learned store.
    Executes the mapped tool; returns the handler result or None (no match /
    failed / gated) — a failure falls through to the next tier / the LLM.
    Works even with FALLBACK_LLM off."""
    from core import intent_learning
    for store in (intent_learning.imported(), intent_learning.learned()):
        hit = store.match(text)
        if hit is None:
            continue
        entry, args = hit
        result, _ = _run_tool(entry["tool"], args)
        ok = intent_learning.verify_for_learning(result, None)
        store.record(entry, ok)
        if ok:
            return result
    return None


def answer(text: str):
    """Entry point used by dispatch(). None when off/unavailable."""
    if not _enabled():
        return None
    return _chat(text)


if __name__ == "__main__":
    # ponytail: no live server in CI — prove the off-switch short-circuits and
    # the tool registry is internally consistent. Run as a module so `import
    # config` resolves:  python -m commands.fallback
    config.FALLBACK_LLM = "none"
    assert answer("what is the capital of france") is None
    for tool in _TOOLS:
        assert tool["function"]["name"] in _TOOL_HANDLERS, tool["function"]["name"]
    assert set(_TOOL_HANDLERS) == {t["function"]["name"] for t in _TOOLS}
    print("ok")
