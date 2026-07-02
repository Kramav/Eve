"""Dynamic Intent Learning — Phase 1: verified outcome tracking (ROADMAP P3).

The substrate the rest of DIL builds on. Every time an intent runs we VERIFY it
actually did something and record the outcome, persisted to intent_training.json
at the repo root (same place as custom_commands.json / aliases.json). From those
counts we derive a confidence via the Wilson score lower bound — honest on small
samples (0 trials → 0.0, not a misleading 1.0). Builtins start pinned at 1.0 and
this only *observes* them (useful on its own: an intent that keeps failing is a
broken handler or a bad pattern); a LEARNED intent (source != "builtin") will
gate promotion on this exact confidence.

Deliberately not a framework: one JSON file, one verifier, one score, one
hydrate. The promotion ladder, phrase clustering and Teach Mode (DIL Phase 2+)
consume these counts — they aren't here because nothing yet *produces* learned
intents to promote. See [[roadmap]] P3.
"""
import json
import math
import os

_STORE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "intent_training.json")


def verify(result, error) -> bool:
    """Did the handler actually do its job? Naive but honest: an exception, or a
    None/False/blank return, is a failure; any other return is success.

    # ponytail: exception-or-falsey heuristic. Handlers already return a spoken
    # string on success and raise on hard failure, so this is right for the
    # common path. Upgrade to per-intent verifiers (did the window actually
    # move?) only when a *learned* intent needs proof before promotion.
    """
    if error is not None:
        return False
    if result is None or result is False:
        return False
    if isinstance(result, str) and not result.strip():
        return False
    return True


def wilson_lower_bound(successes: int, failures: int, z: float = 1.96) -> float:
    """Lower bound of the Wilson score interval for a binomial success rate.
    Small-sample-honest: 0 trials → 0.0; a few successes stay cautious and only
    approach 1.0 with sustained evidence. This is the 'confidence' a learned
    intent must clear to graduate."""
    n = successes + failures
    if n == 0:
        return 0.0
    p = successes / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


class TrainingStore:
    """Per-intent verified outcome counts, persisted to one JSON file.
    `{intent_name: [successes, failures]}`. Best-effort: a read or write failure
    degrades to empty / no-op and never breaks dispatch."""

    def __init__(self, path: str = _STORE_FILE):
        self.path = path
        self.counts = {}
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            # tolerate hand-edits: keep only well-formed [int, int] rows
            self.counts = {k: [int(v[0]), int(v[1])]
                           for k, v in data.items()
                           if isinstance(v, (list, tuple)) and len(v) == 2}
        except (OSError, ValueError, TypeError, KeyError):
            self.counts = {}

    def _save(self):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.counts, f)
            os.replace(tmp, self.path)          # atomic; no half-written file
        except OSError:
            pass  # learning is best-effort — never break dispatch on a write fail

    def record(self, intent_name: str, ok: bool) -> bool:
        s, f = self.counts.get(intent_name, [0, 0])
        if ok:
            s += 1
        else:
            f += 1
        self.counts[intent_name] = [s, f]
        self._save()
        return ok

    def record_result(self, intent_name: str, result, error) -> bool:
        """verify() the outcome, then record it. Returns the verified ok flag."""
        return self.record(intent_name, verify(result, error))

    def confidence(self, intent_name: str) -> float:
        s, f = self.counts.get(intent_name, [0, 0])
        return wilson_lower_bound(s, f)

    def apply_to(self, registry) -> None:
        """Hydrate a registry's Intents with persisted counts at startup so
        learning survives restarts. Builtins keep confidence 1.0 (pinned);
        learned intents get their evidence-based confidence."""
        by_name = {it.name: it for it in registry.all()}
        for name, (s, f) in self.counts.items():
            it = by_name.get(name)
            if it is None:
                continue
            it.successes, it.failures = s, f
            if it.source != "builtin":
                it.confidence = wilson_lower_bound(s, f)
