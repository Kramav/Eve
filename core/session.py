from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class Mode(Enum):
    IDLE    = auto()
    LISTING = auto()
    PLAYING = auto()


@dataclass
class Session:
    mode: Mode = Mode.IDLE
    video_list: list = field(default_factory=list)
    selected_url: Optional[str] = None
    selected_title: Optional[str] = None


_session = Session()


def get() -> Session:
    return _session


def reset():
    global _session
    _session = Session()
