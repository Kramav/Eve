"""llama-swap lifecycle: make sure something answers at config.LLM_BASE_URL.

If the LLM fallback is enabled but nothing is listening, spawn llama-swap
(config.LLAMA_SWAP_EXE or PATH) with the repo's llama-swap.yaml and kill it on
exit. If a server is already up — llama-swap run as a service, a bare
llama-server, Ollama — detect it and do nothing. Best-effort throughout: no
LLM host just means the fallback quietly stays unavailable.
"""
import atexit
import json
import os
import shutil
import subprocess
import urllib.request

import config

_proc = None


def _server_up(timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(
                f"{config.LLM_BASE_URL.rstrip('/')}/models", timeout=timeout) as r:
            json.loads(r.read())
        return True
    except Exception:
        return False


def _repo_bin(name: str):
    """bin/<name> in the repo (where setup drops llama-swap.exe), or None."""
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "bin", name)
    return p if os.path.exists(p) else None


def _listen_addr() -> str:
    """':port' derived from LLM_BASE_URL so the spawn serves where Eve looks."""
    from urllib.parse import urlparse
    return f":{urlparse(config.LLM_BASE_URL).port or 8080}"


def ensure_running() -> bool:
    """Idempotent; called once from main.py. True if a server is (now) up."""
    global _proc
    if (config.FALLBACK_LLM or "none").lower() not in ("local", "ollama"):
        return False
    if _server_up():
        return True
    exe = (config.LLAMA_SWAP_EXE or shutil.which("llama-swap")
           or _repo_bin("llama-swap.exe"))
    if not exe or not os.path.exists(config.LLAMA_SWAP_CONFIG):
        return False
    try:
        _proc = subprocess.Popen(
            [exe, "--config", config.LLAMA_SWAP_CONFIG, "--listen", _listen_addr()],
            cwd=os.path.dirname(config.LLAMA_SWAP_CONFIG),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        atexit.register(stop)
        return True                 # model loads lazily on first request
    except OSError:
        _proc = None
        return False


def stop():
    global _proc
    if _proc and _proc.poll() is None:
        _proc.terminate()
    _proc = None


if __name__ == "__main__":
    # ponytail: no live server in CI — prove the off-switch short-circuits.
    config.FALLBACK_LLM = "none"
    assert ensure_running() is False
    print("ok")
