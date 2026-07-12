"""Eve Conversation Engine — Phase 1 skeleton.

See docs/CONVERSATION_ARCHITECTURE.md. Phase 1 delivers the turn loop, the
no-wake-word follow-up window, extension phrases ("hold on"), cancel, and a
grace window after a reply — and bridges the existing `pending_confirm` /
`converse` so confirmation and did-you-mean can be answered WITHOUT a wake word.

Pure logic: audio capture and response rendering live in main.py. The engine
takes an event (a user turn or a silence timeout) and returns a `StepResult`
describing what to render and whether to keep listening. That keeps it fully
unit-testable with no microphone.

`Outcome` types (Done/NeedConfirm/NeedClarify/NeedSlot/Failed/Handoff) are the
forward contract from docs §5.4. Phase 1 mostly works off raw dispatch results
plus the session engagement flags; Phase 2 migrates features to return Outcomes
and deletes `pending_confirm`. The engine already honors Outcomes if a router
returns one, so migration is incremental.
"""
import random
import re
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional


# ── States (docs §5.1). Phase 1 persists IDLE + the engaged substates; the
# transient ones (LISTENING/PROCESSING/EXECUTING) are driven by main's display,
# and COMPLETED/CANCELLED/TIMED_OUT collapse to IDLE. Full enum kept as the
# documented contract. ──────────────────────────────────────────────────────
class State(Enum):
    IDLE = auto()
    LISTENING = auto()
    PROCESSING = auto()
    EXECUTING = auto()
    AWAITING_CONFIRMATION = auto()
    AWAITING_CLARIFICATION = auto()
    AWAITING_SLOT = auto()
    RETRY_PENDING = auto()
    FOLLOWUP_ACTIVE = auto()
    COMPLETED = auto()
    CANCELLED = auto()
    TIMED_OUT = auto()


# The "Engaged" superstate: mic re-opens without a wake word, extension phrases
# are honored, and "cancel" ends gracefully.
ENGAGED = {State.AWAITING_CONFIRMATION, State.AWAITING_CLARIFICATION,
           State.AWAITING_SLOT, State.RETRY_PENDING, State.FOLLOWUP_ACTIVE}


# ── Events (docs §5.3). Phase 1 uses UserTurn + SilenceTimeout; SpeechFinished
# / Proactive arrive in later phases. ───────────────────────────────────────
@dataclass
class UserTurn:
    text: str


@dataclass
class SilenceTimeout:
    pass


# ── Outcomes (docs §5.4) — the forward contract features return. ────────────
@dataclass
class Done:
    message: str = ""
    followup: bool = True


@dataclass
class NeedConfirm:
    action: Callable
    prompt: str
    on_no: Optional[Callable] = None


@dataclass
class NeedClarify:
    prompt: str
    options: list = field(default_factory=list)   # [(label, action), …]


@dataclass
class NeedSlot:
    name: str
    prompt: str


@dataclass
class Failed:
    message: str
    recovery: list = field(default_factory=list)   # [(label, action), …]


@dataclass
class Handoff:
    text: str
    target: str = "llm"


# ── ConversationContext (docs §5.2). Lean in Phase 1; the slot/referent fields
# are the documented shape, populated in later phases. ──────────────────────
@dataclass
class ConversationContext:
    state:    State = State.IDLE
    deadline: float = 0.0
    prompts:  list  = field(default_factory=list)
    retry_count: int = 0
    # filled in later phases (slot filling, pronoun resolution):
    entities:  dict = field(default_factory=dict)
    referents: dict = field(default_factory=dict)


@dataclass
class StepResult:
    """What main should do with a turn. `response` is a raw dispatch result to
    render (resolve + present); `say` is an engine-owned line to speak directly
    (an ack or cancel). `listen` opens a no-wake follow-up window for `ttl` s."""
    response: Any = None
    say: Optional[str] = None
    listen: bool = False
    ttl: float = 0.0


# ── Conversation-management phrases (honored only while engaged) ─────────────
_EXTENSION = re.compile(
    r"\b(one moment|one sec(ond)?|hold on|hold up|hang on|give me (a|one) "
    r"(second|sec|minute|moment)|just a (minute|sec|second|moment)|"
    r"let me (check|think|see)|i'?m thinking|stand ?by|wait a (sec|second|minute)|"
    r"wait)\b", re.I)
_CANCEL = re.compile(
    r"\b(cancel|never ?mind|nevermind|nvm|forget it|forget about it|drop it|"
    r"abort|stop it|stop)\b", re.I)

_EXTEND_ACKS = ["No problem.", "Take your time.", "Sure, standing by.",
                "Okay, no rush.", "Whenever you're ready."]
_CANCEL_ACKS = ["Okay, cancelled.", "Never mind then.", "Alright, dropping that."]

# Yes/no for confirmations (was dispatcher._YES_RE/_NO_RE — now the engine owns it).
_YES = re.compile(r"^(yes|yeah|yep|yup|sure|ok(ay)?|do it|please|go ahead|"
                  r"confirm(ed)?|correct|that'?s right|right|affirmative)\b", re.I)
_NO  = re.compile(r"^(no|nope|nah|don'?t|do not|wrong|negative)\b", re.I)


def _menu(options) -> str:
    """Spoken menu from recovery/clarify option labels: [(a),(b),(c)] →
    'A, b, or c?'. Empty when there are no options (bare message)."""
    labels = [str(label) for label, _ in options]
    if not labels:
        return ""
    if len(labels) == 1:
        body = labels[0]
    elif len(labels) == 2:
        body = f"{labels[0]} or {labels[1]}"
    else:
        body = f"{', '.join(labels[:-1])}, or {labels[-1]}"
    return body[0].upper() + body[1:] + "?"


# Normalize common recovery phrasings to the labels features use, so "retry"
# resolves a "try again" option and "forget it" a "skip" one.
# (Cancel phrases like "forget it" / "never mind" are handled earlier by
# _CANCEL as "give up" — not listed here.)
_RECOVERY_SYN = [
    (re.compile(r"\b(retry|try it again|again)\b", re.I), "try again"),
    (re.compile(r"\b(leave it|skip it|skip this)\b", re.I), "skip"),
    (re.compile(r"\b(kill it|force kill|force close)\b", re.I), "force it"),
    (re.compile(r"\b(a different one|another one)\b", re.I), "another"),
]


def _match_option(text: str, options):
    """Best (label, action) match for a clarification / recovery answer, or
    None. Recovery synonyms first, then exact / substring, then a fuzzy fallback
    so 'the upstairs one' matches 'upstairs'."""
    for rx, repl in _RECOVERY_SYN:
        text = rx.sub(repl, text)
    for label, action in options:
        lab = _norm(label)
        if lab and (lab in text or text in lab):
            return action
    try:
        from rapidfuzz import fuzz
        best, score = None, 0
        for label, action in options:
            s = fuzz.token_set_ratio(text, _norm(label))
            if s > score:
                best, score = action, s
        if score >= 80:
            return best
    except ImportError:
        pass
    return None


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower()).rstrip(".!?,;: ")


def _pick(options):
    return random.choice(options)


def _is_panel(result) -> bool:
    try:
        from core.response import Panel
        return isinstance(result, Panel)
    except Exception:
        return False


class ConversationEngine:
    """Owns conversational state across turns. `router(text)` is the command
    router (dispatch); `engaged_signal()` reports whether the router left a
    legacy multi-turn context open (pending_confirm / active converse) so the
    engine keeps the mic open to answer it without a wake word."""

    def __init__(self, router: Callable[[str, bool], Any],
                 engaged_signal: Optional[Callable[[], bool]] = None, *,
                 resolver: Optional[Callable[[Any], Any]] = None,
                 followup_ttl: float = 0.0, awaiting_ttl: float = 12.0,
                 extend_by: float = 20.0):
        # router(text, followup) → response | Outcome. `followup` is True on
        # no-wake turns so the router can gate the LLM fallback.
        self.router = router
        # resolver(result) → result: main-provided hook that turns a Verified
        # into its final message (or a Failed with recovery when a checked side
        # effect didn't take). Runs on every result the engine applies — router
        # output AND recovery/confirmation action returns. Identity by default.
        self.resolver = resolver or (lambda r: r)
        self.engaged_signal = engaged_signal or (lambda: False)
        self.followup_ttl = float(followup_ttl)
        self.awaiting_ttl = float(awaiting_ttl)
        self.extend_by = float(extend_by)
        self.state = State.IDLE
        self._deadline = 0.0
        # Engine-owned pending resolution (Phase 2): the action(s) awaiting a
        # yes/no or an option pick. Replaces the session.pending_confirm bridge
        # for migrated features; resolved internally, never re-routed.
        self._pending_confirm = None    # (action, on_no) | None
        self._pending_options = None    # [(label, action), …] | None

    # ── public API ───────────────────────────────────────────────────────
    def engaged(self) -> bool:
        return self.state in ENGAGED

    def handle(self, event) -> StepResult:
        if isinstance(event, SilenceTimeout):
            self._end()
            return StepResult()
        if isinstance(event, UserTurn):
            return self._user_turn(event.text)
        return StepResult()

    # ── turn handling ────────────────────────────────────────────────────
    def _user_turn(self, text: str) -> StepResult:
        # A turn that arrives while already engaged is a no-wake FOLLOW-UP; the
        # router gates the LLM fallback off for these so ambient speech / stray
        # noise on the open mic can't trigger a chatty LLM loop.
        followup = self.state is not State.IDLE
        norm = _norm(text)
        if not norm:                                   # empty transcript
            return StepResult(listen=self.engaged(), ttl=self._await_ttl())

        # Conversation-management + cancel are honored ONLY while engaged, and
        # BEFORE routing — so "hold on" during a confirmation extends the wait
        # instead of being treated as a (non-yes/no) answer that cancels it.
        if self.engaged():
            if _CANCEL.search(norm):
                self._end()
                return StepResult(say=_pick(_CANCEL_ACKS), listen=False)
            if _EXTENSION.search(norm):
                self._extend()
                return StepResult(say=_pick(_EXTEND_ACKS), listen=True,
                                  ttl=self.awaiting_ttl)
            # A pending confirmation / clarification resolves against this turn.
            resolved = self._resolve_pending(norm)
            if resolved is not None:
                return resolved

        result = self.router(text, followup)
        return self._apply(result)

    def _resolve_pending(self, norm: str) -> Optional[StepResult]:
        """Resolve a pending NeedConfirm / NeedClarify against `norm`. Returns a
        StepResult, or None to let an unrelated utterance route as a new command
        (clearing the pending, matching the old any-other-utterance behavior)."""
        if self._pending_confirm is not None:
            action, on_no = self._pending_confirm
            if _YES.search(norm):
                self._clear_pending()
                return self._apply(action())
            if _NO.search(norm):
                self._clear_pending()
                if on_no is not None:
                    return self._apply(on_no())
                self._end()
                return StepResult(say="Okay.", listen=False)
            self._clear_pending()           # unrelated → new command
            return None
        if self._pending_options is not None:
            action = _match_option(norm, self._pending_options)
            self._clear_pending()
            if action is not None:
                return self._apply(action())
            return None                     # no option matched → new command
        return None

    def fail(self, message: str, recovery=None) -> StepResult:
        """Enter error recovery directly (e.g. from a handler exception in main).
        Speaks the message + a recovery menu and stays engaged."""
        return self._apply(Failed(message, recovery or []))

    def _apply(self, result) -> StepResult:
        # Resolve a Verified (run its side-effect check) to its final message or
        # a Failed-with-recovery, before deciding state. Covers router output
        # and confirmation/recovery action returns alike.
        result = self.resolver(result)

        # A migrated feature returned a structured Outcome. The prompt is SPOKEN
        # (docs audit #3 — prompts were previously Silent/unspoken) and the mic
        # stays open so the answer needs no wake word.
        if isinstance(result, NeedConfirm):
            self._pending_confirm = (result.action, result.on_no)
            self.state = State.AWAITING_CONFIRMATION
            self._touch(self.awaiting_ttl)
            return StepResult(say=result.prompt, listen=True, ttl=self.awaiting_ttl)
        if isinstance(result, NeedClarify):
            self._pending_options = list(result.options)
            self.state = State.AWAITING_CLARIFICATION
            self._touch(self.awaiting_ttl)
            return StepResult(say=result.prompt, listen=True, ttl=self.awaiting_ttl)
        if isinstance(result, NeedSlot):
            # Slot resolution is Phase 5; Phase 2 just speaks the prompt + waits.
            self.state = State.AWAITING_SLOT
            self._touch(self.awaiting_ttl)
            return StepResult(say=result.prompt, listen=True, ttl=self.awaiting_ttl)
        if isinstance(result, Failed):
            # Recoverable failure → speak the problem + a spoken recovery menu
            # ("try again, force it, or skip?") and resolve the answer against
            # the options next turn. Conversation stays alive (docs §9).
            self._pending_options = list(result.recovery)
            self.state = State.RETRY_PENDING
            self._touch(self.awaiting_ttl)
            prompt = result.message
            menu = _menu(result.recovery)
            if menu:
                prompt = f"{result.message} {menu}"
            return StepResult(say=prompt, listen=True, ttl=self.awaiting_ttl)
        if isinstance(result, Done):
            result = result.message         # render/engage like a plain reply

        # Legacy bridge: dispatch may have set pending_confirm / an active
        # converse — keep the mic open so it's answered without a wake word.
        if self.engaged_signal():
            self.state = State.AWAITING_CONFIRMATION
            self._touch(self.awaiting_ttl)
            return StepResult(response=result, listen=True, ttl=self.awaiting_ttl)

        # Panels and no-ops end the turn (nothing to continue). A grace window
        # disabled (followup_ttl <= 0) also just ends.
        if result is None or _is_panel(result) or self.followup_ttl <= 0:
            self._end()
            return StepResult(response=result, listen=False)

        # Plain reply → open a short grace window so a natural continuation
        # ("actually, turn it back on") needs no wake word. Times out on silence.
        self.state = State.FOLLOWUP_ACTIVE
        self._touch(self.followup_ttl)
        return StepResult(response=result, listen=True, ttl=self.followup_ttl)

    # ── state helpers ────────────────────────────────────────────────────
    def _end(self):
        self.state = State.IDLE
        self._deadline = 0.0
        self._clear_pending()

    def _clear_pending(self):
        self._pending_confirm = None
        self._pending_options = None

    def _touch(self, ttl: float):
        self._deadline = time.monotonic() + ttl

    def _extend(self):
        self._deadline = time.monotonic() + self.extend_by

    def _await_ttl(self) -> float:
        return (self.awaiting_ttl if self.state in
                {State.AWAITING_CONFIRMATION, State.AWAITING_CLARIFICATION,
                 State.AWAITING_SLOT, State.RETRY_PENDING} else self.followup_ttl)


if __name__ == "__main__":
    # ponytail: quick self-check without audio. Full coverage in
    # tests/test_conversation.py.
    seen = []
    eng = ConversationEngine(router=lambda t, f: seen.append((t, f)) or "Done",
                             followup_ttl=5, awaiting_ttl=10)
    r = eng.handle(UserTurn("what time is it"))
    assert r.response == "Done" and r.listen and r.ttl == 5      # grace window
    assert eng.engaged() and seen == [("what time is it", False)]  # wake turn
    r = eng.handle(UserTurn("hold on"))                          # extension
    assert r.say and r.listen and len(seen) == 1                 # not routed
    r = eng.handle(SilenceTimeout())
    assert not eng.engaged()                                     # back to idle
    print("ok")
