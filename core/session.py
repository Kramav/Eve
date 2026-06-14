from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Tuple
from enum import Enum, auto


class Mode(Enum):
    IDLE    = auto()
    LISTING = auto()
    PLAYING = auto()


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


_session = Session()


def get() -> Session:
    return _session


def reset():
    global _session
    _session = Session()
