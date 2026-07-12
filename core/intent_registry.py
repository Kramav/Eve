"""Declarative intent registry — Tier A of the intent-engine rework (see
ROADMAP P3 Architecture → "Intent engine rework").

Additive + standalone: this is NOT wired into core.dispatcher yet. It replaces
"list position encodes priority" with intents-as-data that carry their own
priority + metadata, and a *scored* matcher that picks the best match instead of
first-match-wins. That removes the ordering fragility the audit
(tests/test_intent_audit.py) found — e.g. every "open <panel>" phrase also
matches apps.open_app and today only wins because it sits higher in the list.

Scoring, best-first:
  1. highest `priority` (explicit specificity — data, not position);
  2. then the most *literal* match — fewest characters captured by wildcard
     groups, i.e. more of the phrase is anchored (so "open app manager", whose
     panel pattern captures nothing, beats open_app which captures "app
     manager") regardless of registration order;
  3. then stable registration order.

`Intent` also carries the provenance/learning metadata the Dynamic Intent
Learning system (ROADMAP P3) needs — source, confidence, success/failure
counts, timestamps — so a *learned* mapping is just an `Intent` with
`source != "builtin"`. Built-ins are confidence 1.0 and never decay.
"""
import re
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple


@dataclass
class Intent:
    name: str
    handler: Callable
    patterns: List[str]                       # regex strings; any may match
    priority: int = 0                         # explicit specificity; higher wins
    feature: Optional[str] = None             # features.json gate (None = always)
    slots: Tuple[str, ...] = ()               # named groups, for docs/introspection
    # ── provenance / learning metadata (Dynamic Intent Learning) ──────────────
    source: str = "builtin"                   # builtin | learned | teach | user
    confidence: float = 1.0                   # builtins = 1.0
    successes: int = 0
    failures: int = 0
    created: float = field(default_factory=time.time)
    last_failure: Optional[float] = None
    _rx: list = field(default_factory=list, repr=False, compare=False)

    def __post_init__(self):
        self._rx = [re.compile(p) for p in self.patterns]

    def match(self, text: str):
        """First WHOLE-UTTERANCE regex Match against `text`, or None.

        `fullmatch`, not `search`: a pattern must account for the entire
        (normalized) utterance, never just a fragment of it. This is the
        architectural fix for greedy-verb misfires — a bare `play`/`mute`/`sleep`
        can no longer be "spotted" inside "look up how to play …". It also makes
        the registry's `_captured_len` specificity scorer sound: once every match
        covers the whole utterance, fewer captured chars really does mean a more
        literal match. Recall for phrasings the strict pattern misses is the LLM
        fallback + Dynamic Intent Learning tier's job, not this one's."""
        for rx in self._rx:
            m = rx.fullmatch(text)
            if m:
                return m
        return None

    def record_success(self):
        self.successes += 1

    def record_failure(self):
        self.failures += 1
        self.last_failure = time.time()


def _captured_len(m) -> int:
    """Characters consumed by wildcard groups — fewer = more literal/specific."""
    return sum(len(g) for g in (m.groups() or ()) if g)


class IntentRegistry:
    """Ordered-insensitive intent store. Registration order is only the final
    tie-breaker; correctness comes from priority + literal-match specificity."""

    def __init__(self):
        self._intents: List[Intent] = []

    def add(self, intent: Intent) -> Intent:
        self._intents.append(intent)
        return intent

    def all(self) -> List[Intent]:
        return list(self._intents)

    def matches(self, text: str, feature_get: Optional[Callable[[str], bool]] = None):
        """All (intent, match) pairs whose pattern matches and whose feature (if
        gated) is enabled, best-first. `feature_get(name) -> bool` supplies gate
        state (injected so this stays decoupled from core.features + testable);
        default treats every feature as enabled."""
        fg = feature_get or (lambda _n: True)
        hits = []
        for it in self._intents:
            if it.feature and not fg(it.feature):
                continue
            m = it.match(text)
            if m is not None:
                hits.append((it, m))
        hits.sort(key=lambda im: (-im[0].priority, _captured_len(im[1])))
        return hits

    def best(self, text: str, feature_get: Optional[Callable[[str], bool]] = None):
        hits = self.matches(text, feature_get)
        return hits[0] if hits else None

    def resolve(self, text: str, feature_get: Optional[Callable[[str], bool]] = None):
        """Run the best-matching intent's handler with its captured groups.
        Returns (intent, handler_result), or None if nothing matched."""
        hit = self.best(text, feature_get)
        if hit is None:
            return None
        it, m = hit
        groups = m.groups()
        return it, (it.handler(*groups) if groups else it.handler())

    def explain(self, text: str, feature_get: Optional[Callable[[str], bool]] = None) -> dict:
        """Intent Explanation (Dynamic Intent Learning transparency): why did /
        would `text` route the way it does? Returns the chosen intent, WHY it won
        (priority vs literal-match vs sole match), and every other candidate —
        so a self-modifying router stays inspectable."""
        hits = self.matches(text, feature_get)

        def _row(it: Intent, m) -> dict:
            return {"name": it.name, "priority": it.priority,
                    "captured_chars": _captured_len(m), "source": it.source,
                    "confidence": it.confidence,
                    "successes": it.successes, "failures": it.failures}

        if not hits:
            return {"text": text, "chosen": None, "candidates": [], "reason": "no intent matched"}
        candidates = [_row(it, m) for it, m in hits]
        if len(hits) == 1:
            reason = "only match"
        elif hits[0][0].priority > hits[1][0].priority:
            reason = "highest priority"
        else:
            reason = "more literal match (fewer captured chars) at equal priority"
        return {"text": text, "chosen": candidates[0], "candidates": candidates, "reason": reason}

    def explain_str(self, text: str, feature_get: Optional[Callable[[str], bool]] = None) -> str:
        """One-line human phrasing of explain() — for a spoken 'why did you do that'."""
        e = self.explain(text, feature_get)
        c = e["chosen"]
        if c is None:
            return f"Nothing matched {text!r}."
        s = (f"{text!r} routed to {c['name']} "
             f"(priority {c['priority']}, {c['captured_chars']} captured chars, "
             f"source {c['source']}) because {e['reason']}.")
        others = [x["name"] for x in e["candidates"][1:]]
        if others:
            s += f" Also matched: {', '.join(others)}."
        return s


def from_intents(intents, feature_map=None, priority_map=None) -> "IntentRegistry":
    """Build a registry from a first-match-ordered ``[(regex, handler)]`` list
    (i.e. core.dispatcher.INTENTS).

    Two modes:
      * **Bridge (priority_map=None):** earlier entries get strictly higher
        priority, so best-match == the list's first-match *exactly*. Used to
        prove the swap is behaviour-preserving (tests/test_intent_registry_parity).
      * **Banded (priority_map given):** every handler defaults to band 0; the
        map overrides specific handlers (e.g. greedy catch-alls → -1). Within a
        band the scorer's literal-match specificity + stable order decide — so
        routing no longer depends on list position, only on how specific each
        pattern is. This is what dispatch() uses; a catch-all can never shadow a
        more specific intent regardless of where it sits in the table.
    """
    reg = IntentRegistry()
    n = len(intents)
    fmap = feature_map or {}
    for i, (pat, handler) in enumerate(intents):
        if priority_map is None:
            prio = n - i                    # bridge: position → unique descending
        else:
            prio = priority_map.get(handler, 0)   # banded: default 0, overrides demote/promote
        reg.add(Intent(
            name=getattr(handler, "__name__", f"intent_{i}"),
            handler=handler,
            patterns=[pat],
            priority=prio,
            feature=fmap.get(handler),
        ))
    return reg
