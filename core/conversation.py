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


_AWAIT_STATE = {
    NeedConfirm:  State.AWAITING_CONFIRMATION,
    NeedClarify:  State.AWAITING_CLARIFICATION,
    NeedSlot:     State.AWAITING_SLOT,
}


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

    def __init__(self, router: Callable[[str], Any],
                 engaged_signal: Optional[Callable[[], bool]] = None, *,
                 followup_ttl: float = 6.0, awaiting_ttl: float = 12.0,
                 extend_by: float = 20.0):
        self.router = router
        self.engaged_signal = engaged_signal or (lambda: False)
        self.followup_ttl = float(followup_ttl)
        self.awaiting_ttl = float(awaiting_ttl)
        self.extend_by = float(extend_by)
        self.state = State.IDLE
        self._deadline = 0.0

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

        result = self.router(text)
        return self._apply(result)

    def _apply(self, result) -> StepResult:
        # A migrated feature returned a structured Outcome.
        if type(result) in _AWAIT_STATE:
            self.state = _AWAIT_STATE[type(result)]
            self._touch(self.awaiting_ttl)
            return StepResult(response=result, listen=True, ttl=self.awaiting_ttl)
        if isinstance(result, Failed):
            self.state = State.RETRY_PENDING
            self._touch(self.awaiting_ttl)
            return StepResult(response=result, listen=True, ttl=self.awaiting_ttl)

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
    eng = ConversationEngine(router=lambda t: seen.append(t) or "Done",
                             followup_ttl=5, awaiting_ttl=10)
    r = eng.handle(UserTurn("what time is it"))
    assert r.response == "Done" and r.listen and r.ttl == 5      # grace window
    assert eng.engaged()
    r = eng.handle(UserTurn("hold on"))                          # extension
    assert r.say and r.listen and seen == ["what time is it"]    # not routed
    r = eng.handle(SilenceTimeout())
    assert not eng.engaged()                                     # back to idle
    print("ok")
