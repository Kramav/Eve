from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Tuple
from enum import Enum, auto


class Mode(Enum):
    IDLE    = auto()
    LISTING = auto()
    PLAYING = auto()


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


_session = Session()


def get() -> Session:
    return _session


def set_last_action(act: LastAction) -> None:
    """Side-effect handlers call this so follow-ups can target them."""
    _session.last_action = act


def reset():
    global _session
    _session = Session()
