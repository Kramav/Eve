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
        """First regex Match against `text`, or None."""
        for rx in self._rx:
            m = rx.search(text)
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


def from_intents(intents, feature_map=None) -> "IntentRegistry":
    """Migration bridge: build a registry from a first-match-ordered
    ``[(regex, handler)]`` list (i.e. core.dispatcher.INTENTS), *preserving its
    semantics exactly* — earlier entries get strictly higher priority, so the
    registry's best-match == the list's first-match.

    This lets the registry drop in for the ordered `for … in INTENTS` loop with
    provably identical routing (see tests/test_intent_registry_parity.py). After
    the swap, priorities can be flattened incrementally — letting the literal-
    match specificity scorer take over — one cluster at a time, with the parity
    + audit tests catching any behaviour change.
    """
    reg = IntentRegistry()
    n = len(intents)
    fmap = feature_map or {}
    for i, (pat, handler) in enumerate(intents):
        reg.add(Intent(
            name=getattr(handler, "__name__", f"intent_{i}"),
            handler=handler,
            patterns=[pat],
            priority=n - i,                 # position → strictly descending priority
            feature=fmap.get(handler),
        ))
    return reg
