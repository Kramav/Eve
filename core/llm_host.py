"""llama-swap lifecycle + LLM options. Nothing machine-specific is baked in:

  - Options live in settings.json under "llm" (same store the panels use),
    with env/config.py values as defaults underneath. See DEFAULTS.
  - llama-swap.yaml is GENERATED on first run from llama-swap.example.yaml by
    discovering llama-server (PATH, then the winget install dir) and whichever
    model files are actually present in models/llm/. The generated file is
    gitignored and never overwritten — hand-edits stick. Delete it to
    regenerate.

If the fallback is enabled but nothing answers at the base URL, spawn
llama-swap (settings/PATH/repo bin/) and kill it on exit; a server that's
already up — llama-swap as a service, bare llama-server, Ollama — is detected
and left alone. Best-effort throughout: no LLM host just means the fallback
quietly stays unavailable.
"""
import atexit
import glob
import json
import os
import shutil
import subprocess
import urllib.request

import config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXAMPLE_YAML = os.path.join(_ROOT, "llama-swap.example.yaml")

# Every knob, with its default. settings.json {"llm": {...}} overrides any of
# these (the panel-editable store wins, like api_keys); env vars feed the
# config.py defaults for headless setups.
DEFAULTS = {
    "enabled":         None,     # None → follow config.FALLBACK_LLM
    "base_url":        None,     # None → config.LLM_BASE_URL
    "model":           None,     # None → config.LLM_MODEL
    "model_mini":      "eve-fallback-mini",
    "gpu":             True,     # offload the main model (Vulkan/CUDA -ngl 99)
    "swap_when_busy":  True,     # game/high-RAM → use model_mini on CPU
    "busy_ram_pct":    80,       # RAM load % that counts as busy
    "main_model_file": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
    "mini_model_file": "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf",
    "ctx_main":        8192,
    "ctx_mini":        4096,
    "ttl_main":        600,      # idle seconds before the model unloads
    "ttl_mini":        300,
}

_proc = None


def settings() -> dict:
    """DEFAULTS ← settings.json 'llm' section, with the config.py-backed keys
    resolved. Read fresh each call so panel edits apply without a restart."""
    s = dict(DEFAULTS)
    try:
        with open(os.path.join(_ROOT, "settings.json"), encoding="utf-8") as f:
            user = json.load(f).get("llm") or {}
        s.update({k: v for k, v in user.items() if v is not None})
    except (OSError, ValueError):
        pass
    if s["enabled"] is None:
        s["enabled"] = (config.FALLBACK_LLM or "none").lower() in ("local", "ollama")
    if s["base_url"] is None:
        s["base_url"] = config.LLM_BASE_URL
    if s["model"] is None:
        s["model"] = config.LLM_MODEL
    return s


def _server_up(base_url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/models", timeout=timeout) as r:
            json.loads(r.read())
        return True
    except Exception:
        return False


def find_llama_server():
    """llama-server discovery: PATH, then the winget llama.cpp install (which
    doesn't shim onto PATH). None if absent."""
    hit = shutil.which("llama-server")
    if hit:
        return hit
    pattern = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                           "Microsoft", "WinGet", "Packages",
                           "ggml.llamacpp*", "llama-server.exe")
    hits = glob.glob(pattern)
    return hits[0] if hits else None


def _find_llama_swap():
    return (config.LLAMA_SWAP_EXE or shutil.which("llama-swap")
            or _existing(os.path.join(_ROOT, "bin", "llama-swap.exe")))


def _existing(p):
    return p if p and os.path.exists(p) else None


# First line of every file generate_config() writes. Regeneration (e.g. after
# a settings change in the UI) only touches files that still carry it — delete
# the line to lock your hand-edits.
GENERATED_MARKER = "# eve:generated — delete this line to stop Eve regenerating this file"


def generate_config(path: str = None) -> str | None:
    """Write llama-swap.yaml from llama-swap.example.yaml with the discovered
    llama-server path, present model files, and the gpu/ctx/ttl options
    substituted. Returns the path, or None when prerequisites are missing.
    Never overwrites an existing file."""
    path = path or config.LLAMA_SWAP_CONFIG
    if os.path.exists(path):
        return path
    server = find_llama_server()
    if not server or not os.path.exists(_EXAMPLE_YAML):
        return None
    s = settings()
    main_gguf = _existing(os.path.join(_ROOT, "models", "llm", s["main_model_file"]))
    mini_gguf = _existing(os.path.join(_ROOT, "models", "llm", s["mini_model_file"]))
    if not main_gguf and not mini_gguf:
        return None
    with open(_EXAMPLE_YAML, encoding="utf-8") as f:
        text = f.read()
    text = (text
            .replace("{{LLAMA_SERVER}}", server)
            .replace("{{GPU_FLAGS}}", "-ngl 99" if s["gpu"] else "")
            .replace("{{MAIN_MODEL}}", main_gguf or "")
            .replace("{{MINI_MODEL}}", mini_gguf or "")
            .replace("{{CTX_MAIN}}", str(s["ctx_main"]))
            .replace("{{CTX_MINI}}", str(s["ctx_mini"]))
            .replace("{{TTL_MAIN}}", str(s["ttl_main"]))
            .replace("{{TTL_MINI}}", str(s["ttl_mini"])))
    # Drop the whole model block for a missing file (marker lines in the example).
    lines, skip = [], None
    for line in text.splitlines():
        if "#if-main" in line:
            skip = not main_gguf; continue
        if "#if-mini" in line:
            skip = not mini_gguf; continue
        if "#endif" in line:
            skip = None; continue
        if not skip:
            lines.append(line)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(GENERATED_MARKER + "\n" + "\n".join(lines) + "\n")
        return path
    except OSError:
        return None


def list_model_files() -> list:
    """*.gguf filenames present in models/llm/ (what the UI offers)."""
    try:
        d = os.path.join(_ROOT, "models", "llm")
        return sorted(f for f in os.listdir(d) if f.lower().endswith(".gguf"))
    except OSError:
        return []


def save_settings(patch: dict) -> dict:
    """Merge known keys into settings.json 'llm' (the single user-facing home
    for these options; same read-merge-write the other panels use)."""
    clean = {k: patch[k] for k in patch if k in DEFAULTS}
    sf = os.path.join(_ROOT, "settings.json")
    try:
        with open(sf, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    llm = data.get("llm") or {}
    llm.update(clean)
    data["llm"] = llm
    try:
        with open(sf, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass
    return settings()


def apply_settings() -> bool:
    """Make new options take effect: regenerate llama-swap.yaml (only when it
    still carries GENERATED_MARKER — hand-edited files are respected) and
    bounce the llama-swap we spawned. Returns _server_up-equivalent of
    ensure_running()."""
    path = config.LLAMA_SWAP_CONFIG
    try:
        with open(path, encoding="utf-8") as f:
            regen = f.readline().startswith(GENERATED_MARKER.split(" — ")[0])
    except OSError:
        regen = True                      # missing → fresh generation
    if regen:
        try:
            os.remove(path)
        except OSError:
            pass
        generate_config(path)
    if _proc is not None:                 # only bounce a server WE started
        stop()
    return ensure_running()


def _listen_addr(base_url: str) -> str:
    """':port' derived from the base URL so the spawn serves where Eve looks."""
    from urllib.parse import urlparse
    return f":{urlparse(base_url).port or 8080}"


def ensure_running() -> bool:
    """Idempotent; called once from main.py. True if a server is (now) up."""
    global _proc
    s = settings()
    if not s["enabled"]:
        return False
    if _server_up(s["base_url"]):
        return True
    exe = _find_llama_swap()
    cfg = generate_config()
    if not exe or not cfg:
        return False
    try:
        _proc = subprocess.Popen(
            [exe, "--config", cfg, "--listen", _listen_addr(s["base_url"])],
            cwd=os.path.dirname(cfg),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        atexit.register(stop)
        return True                 # models load lazily on first request
    except OSError:
        _proc = None
        return False


def stop():
    global _proc
    if _proc and _proc.poll() is None:
        _proc.terminate()
    _proc = None


if __name__ == "__main__":
    # ponytail: no live server in CI — prove the off-switch short-circuits and
    # settings merge sanely. Run as a module:  python -m core.llm_host
    config.FALLBACK_LLM = "none"
    s = settings()
    assert s["base_url"] and s["model"]
    assert isinstance(s["gpu"], bool) and isinstance(s["busy_ram_pct"], int)
    assert ensure_running() is False
    print("ok")
