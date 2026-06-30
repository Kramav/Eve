"""Local LLM fallback with tool calling. When no built-in intent and no skill
matches, a local Ollama model gets one shot at the utterance:

  - If it's an *actionable* request the regex missed ("could you throw firefox
    on my left screen"), the model emits a tool call that maps to a real Eve
    handler (`apps.open_app`, `tiling.snap_app`, …) and we execute it. This is
    what turns "not recognized" into "handled weird phrasing."
  - Otherwise it just answers in a sentence or two (general Q&A).

Off by default (config.FALLBACK_LLM = "none"). Set it to "ollama" and run a
local Ollama server with a **tool-capable** model pulled (llama3.1+, qwen3,
mistral-nemo, …). Any failure — server down, model missing, no tool support,
timeout — degrades gracefully: tool-calling falls back to plain answering, and
plain answering falls back to None so dispatch() shows its normal reply. Nothing
ever hangs or crashes the pipeline.
"""
import json
import urllib.request
import urllib.error

import config

_TIMEOUT_S = 30  # tool round-trips on CPU can be slow on first token

_SYSTEM = (
    "You are Eve, a local Windows voice assistant. If the user is asking you to "
    "DO something (open/close an app, move or snap a window, search the web, go "
    "to a site, play a video, set a timer or reminder, bring a window to front), "
    "call the matching tool. Otherwise just answer in one or two short spoken "
    "sentences — no markdown, no lists. Only call a tool when you are confident."
)

# ── Tool schemas (OpenAI/Ollama function-calling shape) ─────────────────────

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


# ── HTTP ────────────────────────────────────────────────────────────────────

def _post(path: str, body: dict):
    req = urllib.request.Request(
        f"{config.OLLAMA_HOST.rstrip('/')}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def _run_tool(call: dict):
    fn = call.get("function") or {}
    name = fn.get("name")
    args = fn.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    entry = _TOOL_HANDLERS.get(name)
    if not entry:
        return None
    handler, feature = entry
    if feature:
        from core import features
        if not features.get(feature):
            return None
    try:
        return handler(args)
    except Exception:
        return None


def _chat(text: str):
    """Tool-capable chat. Returns a handler result, a spoken answer, or None.
    Falls back to plain generation if the chat/tools endpoint isn't usable."""
    data = _post("/api/chat", {
        "model": config.OLLAMA_MODEL,
        "messages": [{"role": "system", "content": _SYSTEM},
                     {"role": "user", "content": text}],
        "tools": _TOOLS,
        "stream": False,
    })
    if data is None:
        return _plain(text)
    msg = data.get("message") or {}
    calls = msg.get("tool_calls") or []
    if calls:
        result = _run_tool(calls[0])
        if result is not None:
            return result
    content = (msg.get("content") or "").strip()
    return content or None


def _plain(text: str):
    """General Q&A with no tools — the original behavior, kept as a floor for
    models that don't support tool calling."""
    data = _post("/api/generate", {
        "model": config.OLLAMA_MODEL,
        "prompt": text,
        "system": "You are Eve. Answer in one or two short spoken sentences. "
                  "No markdown, no lists.",
        "stream": False,
    })
    if data is None:
        return None
    return (data.get("response") or "").strip() or None


def answer(text: str):
    """Entry point used by dispatch(). None when off/unavailable."""
    if (config.FALLBACK_LLM or "none").lower() != "ollama":
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
