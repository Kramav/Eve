"""Drop-in skill loader. Lets anyone add new voice commands by dropping a
`.py` file into the repo-root `skills/` directory — no edits to core required.

A skill file defines a module-level `INTENTS` list of `(regex, handler)` tuples
(same shape as `core.dispatcher.INTENTS`). Optional extras:

    PRIORITY = 10            # higher runs first across all skills (default 0)
    FEATURE  = "my_feature"  # gate the whole skill on a features.json flag
    PREEMPT  = True          # run this skill's intents BEFORE the built-ins
    def setup(display): ...  # called once at load with the live Display (or None)

Handlers return a str (spoken), a `core.response.Silent`/`Panel`, or a list
type the dispatcher already understands. Captured regex groups are passed
positionally, exactly like built-in intents.

By default skills are tried after built-in INTENTS and before the fuzzy/LLM
fallback, so they extend the built-ins without overriding them. A skill that
needs to *own* a phrase the built-ins would otherwise claim (e.g. YouTube's
"open youtube" vs. the app launcher's "open X") sets `PREEMPT = True`: its
intents are collected separately and the dispatcher tries them ahead of the
built-in table. Use it sparingly — it lets a skill override core. A skill that
throws at load time is skipped with a logged warning — one bad file never takes
Eve down.
"""
import importlib.util
import re
import traceback
from pathlib import Path

_SKILLS_DIR = Path(__file__).parent.parent / "skills"

# [(priority, compiled_regex, handler, feature_or_None)], sorted priority-desc.
# _loaded runs after the built-ins (extend); _preloaded runs before them (PREEMPT).
_loaded:    list = []
_preloaded: list = []
_names:     list = []


def _import_file(path: Path):
    spec = importlib.util.spec_from_file_location(f"eve_skill_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # type: ignore[union-attr]
    return mod


def load(display=None) -> list:
    """Import every skills/*.py, collect their INTENTS. Returns loaded names.
    Idempotent — safe to call again to reload."""
    _loaded.clear()
    _preloaded.clear()
    _names.clear()
    if not _SKILLS_DIR.is_dir():
        return []

    for f in sorted(_SKILLS_DIR.glob("*.py")):
        if f.name.startswith("_"):        # _private.py / __init__.py skipped
            continue
        try:
            mod = _import_file(f)
        except Exception:
            print(f"[skills] failed to import {f.name}:\n{traceback.format_exc()}")
            continue

        setup = getattr(mod, "setup", None)
        if callable(setup):
            try:
                setup(display)
            except Exception:
                print(f"[skills] {f.name} setup() raised:\n{traceback.format_exc()}")

        priority = int(getattr(mod, "PRIORITY", 0))
        feature  = getattr(mod, "FEATURE", None)
        preempt  = bool(getattr(mod, "PREEMPT", False))
        bucket   = _preloaded if preempt else _loaded
        intents  = getattr(mod, "INTENTS", []) or []
        n = 0
        for entry in intents:
            try:
                pat, handler = entry
                bucket.append((priority, re.compile(pat), handler, feature))
                n += 1
            except Exception:
                print(f"[skills] {f.name}: bad INTENTS entry {entry!r}")
        if n:
            _names.append(f.stem)
            tag = " (preempt)" if preempt else ""
            print(f"[skills] loaded {f.stem} ({n} intent{'s' if n != 1 else ''}){tag}")

    _loaded.sort(key=lambda t: -t[0])
    _preloaded.sort(key=lambda t: -t[0])
    return list(_names)


def _run(intents: list, text: str):
    """Try each (priority, regex, handler, feature) in *intents* against text.
    Returns a handler result or None. A handler that raises is treated as a
    non-match so one broken skill can't break dispatch."""
    from core import features as _features
    for _prio, rx, handler, feature in intents:
        m = rx.search(text)
        if not m:
            continue
        if feature and not _features.get(feature):
            continue
        try:
            groups = m.groups()
            return handler(*groups) if groups else handler()
        except Exception:
            print(f"[skills] handler error on {text!r}:\n{traceback.format_exc()}")
            return None
    return None


def dispatch_preempt(text: str):
    """Run PREEMPT skill intents — tried by the dispatcher BEFORE built-ins."""
    return _run(_preloaded, text)


def dispatch(text: str):
    """Run normal skill intents — tried by the dispatcher AFTER built-ins,
    before the fuzzy/LLM fallback."""
    return _run(_loaded, text)


def loaded_names() -> list:
    return list(_names)


if __name__ == "__main__":
    # ponytail: load whatever's in skills/ and show what registered.
    print("loaded skills:", load())
    print("intents registered:", len(_loaded), "+ preempt:", len(_preloaded))
