"""Post-execution verification — did the command actually take?

Some handlers reply optimistically ("Opening Firefox") before the side effect
is observable. A `core.response.Verified` wraps that reply with a `check()` that
becomes true once the effect lands. `resolve()` runs the check, retries the
action once if it didn't, and returns the message to actually report plus
whether it was confirmed.

Wired into `main.on_command` behind the `verify_commands` feature flag, so the
latency (one or two short check delays) is opt-out. Pure stdlib; never raises.
"""
import time

from core.response import Verified


def resolve(resp):
    """Confirm a Verified response's side effect. Returns ``(message, ok)``.

    Waits ``resp.delay``, runs ``check()``; on failure runs ``retry()`` once
    (if provided), waits again, and re-checks. A non-Verified input passes
    through unchanged as ``(resp, True)``. A ``check``/``retry`` that throws is
    treated as "not confirmed", never propagated."""
    if not isinstance(resp, Verified):
        return resp, True

    if _confirmed(resp):
        return str(resp), True

    if resp.retry is not None:
        try:
            resp.retry()
        except Exception:
            pass
        if _confirmed(resp):
            return str(resp), True

    return resp.on_fail, False


def _confirmed(resp) -> bool:
    if resp.delay > 0:
        time.sleep(resp.delay)
    try:
        return bool(resp.check())
    except Exception:
        return False
