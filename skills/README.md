# Eve Skills

Drop a `.py` file in this folder to add voice commands — **no changes to Eve's
core code.** Files are imported at startup by [core/skills.py](../core/skills.py).
See [example_dice.py](example_dice.py) for a working template ("roll a die",
"roll 2d6", "flip a coin").

## Minimum skill

```python
# skills/hello.py
def _hello():
    return "Hello from a skill!"

INTENTS = [
    (r"\bsay hello\b", _hello),
]
```

That's it. Restart Eve and say "say hello".

## How it works

- Each skill defines a module-level `INTENTS` list of `(regex, handler)` tuples
  — the same shape as the built-in table. First match wins; captured regex
  groups are passed to the handler positionally.
- Handlers return a `str` (spoken aloud), or a `core.response.Silent` / `Panel`
  for status-only/panel actions, or a `VideoList` / `SiteList` for pick-lists.
- Skills run **after** the built-in intents and **before** the fuzzy/LLM
  fallback, so they extend Eve rather than override it.

## Optional hooks

```python
PRIORITY = 10                  # higher runs first across skills (default 0)
FEATURE  = "my_feature"        # gate the skill on a features.json flag
PREEMPT  = True                # run this skill BEFORE the built-ins (default off)

def setup(display):            # called once at load; `display` may be None
    ...                        # stash it if your handlers need to drive the UI
```

### `PREEMPT` — own a phrase the built-ins would otherwise take

By default skills run *after* the built-in commands, so they extend Eve without
overriding it. If your skill needs to claim a phrase a built-in would catch
first — e.g. "open youtube" would otherwise become "open the *youtube* app" — set
`PREEMPT = True` and its intents are tried *before* the built-in table. Use it
sparingly; it lets a skill shadow core. See [youtube.py](youtube.py), which uses
`PREEMPT` for its entry commands and the **converse layer**
(`core.session.start_converse`) for stateful follow-ups (feed scrolling, list
selection, mpv playback) instead of any core routing.

## Rules of the road

- Files starting with `_` (and `__init__.py`) are ignored.
- A skill that raises at import or whose handler raises is skipped/treated as a
  non-match and logged — one bad skill never takes Eve down.
- Keep regexes anchored with `\b` so they don't match inside other words.
