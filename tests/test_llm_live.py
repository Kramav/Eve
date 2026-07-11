"""LIVE smoke test for the LLM fallback stack: llama-swap → llama-server →
Qwen tool calling → Eve's OpenAI client (commands/fallback.py).

Skips (passes trivially) when nothing answers at config.LLM_BASE_URL, so the
suite stays green on machines without the LLM host. When the server IS up it
proves the whole teacher path at the protocol level — the model loads, answers
plainly, and emits a tool call for an actionable phrasing — WITHOUT executing
real handlers (no desktop side effects from a test run).

Run either way:
    pytest tests/
    python tests/test_llm_live.py
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from commands import fallback


def _server_up() -> bool:
    try:
        with urllib.request.urlopen(
                f"{config.LLM_BASE_URL.rstrip('/')}/models", timeout=2) as r:
            json.loads(r.read())
        return True
    except Exception:
        return False


_UP = _server_up()
if _UP:
    # Warm the model once so per-test timeouts measure inference, not load.
    fallback._post({"model": config.LLM_MODEL,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 4})


def test_plain_answer():
    if not _UP:
        return  # SKIP: no LLM host running
    out = fallback._plain("What is two plus two? Answer with just the number.")
    assert out and ("4" in out or "four" in out.lower()), f"unexpected: {out!r}"


def test_tool_call_emitted_for_actionable_phrase():
    if not _UP:
        return  # SKIP: no LLM host running
    data = fallback._post({
        "model": config.LLM_MODEL,
        "messages": [{"role": "system", "content": fallback._SYSTEM},
                     {"role": "user", "content":
                      "could you throw firefox onto the left half of my screen"}],
        "tools": fallback._TOOLS,
    })
    msg = fallback._message(data)
    assert msg, f"no message in response: {data!r}"
    calls = msg.get("tool_calls") or []
    assert calls, f"model emitted no tool call: {msg!r}"
    name = (calls[0].get("function") or {}).get("name")
    assert name in fallback._TOOL_HANDLERS, f"unknown tool: {name!r}"


# ── Zero-dependency runner ────────────────────────────────────────────────────

if __name__ == "__main__":
    if not _UP:
        print("  SKIP  llm host not running — all live tests passed vacuously")
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
