import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Tuple
from enum import Enum, auto


class Mode(Enum):
    IDLE     = auto()
    LISTING  = auto()
    PLAYING  = auto()
    BROWSING = auto()   # YouTube HUD browser is open and taking feed commands


@dataclass
class Converse:
    """A claim on upcoming utterances by a command handler. Modeled on OVOS
    ConverseService: while active, gets first crack at each new utterance
    BEFORE normal intent matching. The handler returns a response object to
    claim the utterance, or None to decline (dispatch then falls through to
    normal routing).

    Decays two ways so a stale context can't hijack speech forever:
      - `turns`: follow-ups it can still claim before auto-expiring.
      - `ttl`:   seconds of inactivity before auto-expiring.
    Declining an utterance costs nothing (the wall-clock TTL still applies);
    only a successful claim spends a turn and refreshes the deadline."""
    handler: Callable[[str], Any]    # (text) -> response | None
    label:   str                     # human description, for logging/debug
    turns:   int   = 3               # follow-ups remaining before auto-expire
    ttl:     float = 60.0            # seconds of inactivity before auto-expire
    _deadline: float = field(default=0.0, repr=False)

    def __post_init__(self):
        self._deadline = time.monotonic() + self.ttl

    def alive(self) -> bool:
        return self.turns > 0 and time.monotonic() < self._deadline

    def touch(self) -> None:
        """Spend a turn and refresh the deadline after a successful claim."""
        self.turns -= 1
        self._deadline = time.monotonic() + self.ttl


@dataclass
class LastAction:
    """The most recent side-effecting thing Eve did. Populated by handlers
    that want to support non-specific follow-up commands like 'go back',
    'close that window', 'cancel it'. Cleared when consumed."""
    description: str                          # "Snapped Firefox to top of monitor 2"
    undo:        Optional[Callable] = None    # revert callable (e.g. move window back)
    target_hwnd: Optional[int]      = None    # window we operated on
    cancelable:  Optional[Callable] = None    # for timers / reminders / shutdown


@dataclass
class Session:
    mode: Mode = Mode.IDLE
    video_list: list = field(default_factory=list)
    site_list:  list = field(default_factory=list)
    selected_url: Optional[str] = None
    selected_title: Optional[str] = None
    # Single-turn confirmation: when set, the next utterance is checked for
    # yes/no. Tuple of (callable, args_tuple, label) — label is shown back
    # to the user on confirm. Cleared on yes/no/any other utterance.
    pending_confirm: Optional[Tuple[Callable[..., Any], tuple, str]] = None
    # Pronoun-target slot for "go back" / "close that" / "cancel it".
    last_action: Optional[LastAction] = None
    # Active multi-turn converse context (see Converse). Gets first crack at
    # the next utterance. Set via start_converse(); cleared on expiry/cancel.
    converse: Optional[Converse] = None


_session = Session()


def get() -> Session:
    return _session


def set_last_action(act: LastAction) -> None:
    """Side-effect handlers call this so follow-ups can target them."""
    _session.last_action = act


def start_converse(handler: Callable[[str], Any], label: str,
                   turns: int = 3, ttl: float = 60.0) -> None:
    """Side-effect handlers call this to claim upcoming follow-up utterances.
    The handler receives the (already wake-word-stripped, lowercased) text and
    returns a response to claim it, or None to decline."""
    _session.converse = Converse(handler=handler, label=label,
                                 turns=turns, ttl=ttl)


def clear_converse() -> None:
    _session.converse = None


def reset():
    global _session
    _session = Session()
