"""Dynamic Intent Learning — verified outcome tracking + the learned-intents store.

Phase 1 substrate: every time an intent runs we VERIFY it actually did
something and record the outcome (intent_training.json). From those counts we
derive a confidence via the Wilson score lower bound — honest on small samples
(0 trials → 0.0, not a misleading 1.0). Builtins start pinned at 1.0 and this
only *observes* them.

Phase 2/3 (the learning loop): the LLM fallback is the TEACHER. When it
resolves an unfamiliar phrasing into a tool call that VERIFIABLY succeeds,
`LearnedStore.capture()` records the mapping in learned_intents.json (repo
root — transferable, like intent_training.json). The learned tier
(commands/fallback.py `learned_answer`, consulted by dispatch just before the
LLM) then serves it locally:

  - the exact phrase is served immediately after one verified success;
  - a TEMPLATED pattern (arg values swapped for named groups, so "throw
    chrome on my left screen" generalizes from "throw firefox on my left
    screen") only turns on once the entry is TRUSTED — Wilson confidence ≥
    TRUST_CONFIDENCE, i.e. ~3 clean verified successes.

Core principle (ROADMAP): never learn from an LLM response alone — only from
verified outcomes. Safety is class-based, never confidence-based: DESTRUCTIVE
tools (close/kill) are captured for evidence but NEVER auto-served by the
learned tier, regardless of confidence.
"""
import json
import math
import os
import re
import time

_STORE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "intent_training.json")
_LEARNED_FILE = os.path.join(os.path.dirname(_STORE_FILE), "learned_intents.json")

# Trust bar for pattern (generalizing) matches: wilson(3, 0) ≈ 0.44 clears it,
# wilson(2, 0) ≈ 0.34 doesn't → three clean verified successes.
TRUST_CONFIDENCE = 0.4

# Class-based safety (ROADMAP guardrail): these are never served by the
# learned tier — an LLM interpretation of a destructive verb must stay a
# per-call LLM decision until the confirmation flow exists.
DESTRUCTIVE_TOOLS = {"close_app"}


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


# ── Learned intents (the LLM-fallback capture → local-serve loop) ────────────

def normalize(text: str) -> str:
    """Canonical phrase form: lowercase, collapsed whitespace, no trailing
    punctuation. Matching and storage both go through this."""
    t = re.sub(r"\s+", " ", (text or "").lower().strip())
    return t.rstrip(".!?,;: ")


def make_template(phrase: str, args: dict):
    """Turn a captured phrase into a generalizing regex by replacing each arg
    VALUE that appears verbatim (word-bounded) with a named group:

        "throw firefox on my left screen", {app: firefox, zone: left}
        → "throw (?P<app>.+?) on my (?P<zone>.+?) screen"

    Returns None unless EVERY non-empty arg value is found exactly once and
    the resulting pattern round-trips (re-extracts the original args from the
    original phrase). None/empty args are allowed to be absent (optional
    params like snap_window's monitor)."""
    spans = []
    for key, val in (args or {}).items():
        v = normalize(str(val)) if val is not None else ""
        if not v:
            continue                                   # optional/absent arg
        hits = [m.span() for m in re.finditer(rf"(?<!\w){re.escape(v)}(?!\w)", phrase)]
        if len(hits) != 1:
            return None                                # missing or ambiguous
        spans.append((hits[0][0], hits[0][1], key))
    if not spans:
        return None                                    # nothing to generalize
    spans.sort()
    for (_, end, _), (start, _, _) in zip(spans, spans[1:]):
        if start < end:
            return None                                # overlapping values
    pattern, pos = "", 0
    for start, end, key in spans:
        pattern += re.escape(phrase[pos:start]) + rf"(?P<{key}>.+?)"
        pos = end
    pattern += re.escape(phrase[pos:])
    m = re.fullmatch(pattern, phrase)
    if not m:
        return None
    extracted = {k: normalize(v) for k, v in m.groupdict().items()}
    expected = {key: normalize(str(args[key])) for _, _, key in spans}
    return pattern if extracted == expected else None


class LearnedStore:
    """learned_intents.json — the auto-updating list of learned mappings.
    Entries: {phrase, tool, args, pattern|null, s, f, created, last_used}.
    Best-effort persistence like TrainingStore; never breaks dispatch."""

    def __init__(self, path: str = _LEARNED_FILE):
        self.path = path
        self.entries = []
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.entries = [e for e in data if isinstance(e, dict)
                            and e.get("phrase") and e.get("tool")] if isinstance(data, list) else []
        except (OSError, ValueError, TypeError):
            self.entries = []

    def _save(self):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.entries, f, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def _find(self, tool: str, phrase: str):
        for e in self.entries:
            if e.get("tool") == tool and e.get("phrase") == phrase:
                return e
        return None

    def capture(self, text: str, tool: str, args: dict):
        """Record a VERIFIED successful LLM tool call as a learned candidate.
        Callers verify first — this trusts its input is a real outcome.
        Destructive tools are captured too (evidence accrues for the future
        confirmation flow) but match() never serves them."""
        if not tool:
            return None
        phrase = normalize(text)
        if not phrase:
            return None
        args = {k: v for k, v in (args or {}).items()}
        entry = self._find(tool, phrase)
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        if entry is None:
            entry = {"phrase": phrase, "tool": tool, "args": args,
                     "pattern": make_template(phrase, args),
                     "s": 1, "f": 0, "created": now, "last_used": now}
            self.entries.append(entry)
        else:
            entry["s"] = entry.get("s", 0) + 1
            entry["last_used"] = now
        self._save()
        return entry

    def confidence(self, entry: dict) -> float:
        return wilson_lower_bound(entry.get("s", 0), entry.get("f", 0))

    def match(self, text: str):
        """(entry, args) for the learned mapping that should handle `text`,
        or None. Exact phrases serve immediately; patterns (generalization)
        only once TRUSTED. Destructive tools never serve."""
        phrase = normalize(text)
        if not phrase:
            return None
        for e in self.entries:                          # exact, any confidence
            if e["phrase"] == phrase and e["tool"] not in DESTRUCTIVE_TOOLS:
                return e, dict(e.get("args") or {})
        trusted = [e for e in self.entries
                   if e.get("pattern") and e["tool"] not in DESTRUCTIVE_TOOLS
                   and self.confidence(e) >= TRUST_CONFIDENCE]
        trusted.sort(key=self.confidence, reverse=True)  # most evidence wins
        for e in trusted:
            try:
                m = re.fullmatch(e["pattern"], phrase)
            except re.error:
                continue
            if m:
                args = dict(e.get("args") or {})
                args.update({k: v.strip() for k, v in m.groupdict().items()})
                return e, args
        return None

    def record(self, entry: dict, ok: bool):
        """Verified outcome of executing a learned mapping."""
        entry["s" if ok else "f"] = entry.get("s" if ok else "f", 0) + 1
        entry["last_used"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        if not ok:
            entry["last_failure"] = entry["last_used"]
        self._save()


_learned = None


def learned() -> LearnedStore:
    """Singleton LearnedStore (lazy so tests can point _LEARNED_FILE elsewhere
    by constructing their own)."""
    global _learned
    if _learned is None:
        _learned = LearnedStore()
    return _learned
