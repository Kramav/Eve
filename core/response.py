class Silent(str):
    """Response shown briefly in the status bar only — not spoken, no large text."""
    pass


class Verified(str):
    """An optimistic response whose side effect is confirmed before it's
    reported. A handler returns the message it WOULD say on success, plus a way
    to check the effect actually happened:

      check():   () -> bool — True once the effect is observable.
      on_fail:   message reported instead if it never confirms.
      retry:     optional () -> None — run once if the first check fails, then
                 the effect is re-checked (the user-chosen "retry once" policy).
      announce:  optional message reported up front, BEFORE the check delay —
                 e.g. "Opening Photoshop now, this may take a moment" for an app
                 learned to be slow to launch.
      delay:     seconds to wait before each check (most effects are async — a
                 launched window or a paused printer take a beat to appear).

    Resolved by `core.verify.resolve()`, wired into main.on_command behind the
    `verify_commands` feature flag. Subclasses str, so any caller that doesn't
    know about verification (e.g. a test) just sees the optimistic message."""

    def __new__(cls, message, *, check, on_fail, retry=None, announce=None,
                delay=1.0):
        s = super().__new__(cls, message)
        s.check    = check
        s.on_fail  = on_fail
        s.retry    = retry
        s.announce = announce
        s.delay    = float(delay)
        return s


class Panel(Silent):
    """A panel-open/close/toggle action. The handler has already done the work
    (opened/closed an Electron window) via the Display, so the dispatcher loop
    should hide the HUD immediately and never speak the return string. Lets the
    7 former pre-dispatch shortcuts in main.py live as normal INTENTS without
    gaining a spoken confirmation or a 'Thinking…' delay."""
    pass


class VideoList:
    """A list of videos to show in the overlay panel. Not spoken unless user asks."""

    def __init__(self, items: list, message: str = ""):
        self.items = items
        self.message = message or f"Found {len(items)} video{'s' if len(items) != 1 else ''}"

    def format_items(self) -> list:
        lines = []
        for i, v in enumerate(self.items, 1):
            title = v["title"]
            if len(title) > 62:
                title = title[:59] + "..."
            dur = f"  {v['duration']}" if v.get("duration") else ""
            lines.append(f"{i}.  {title}{dur}")
        return lines

    def __str__(self) -> str:
        return self.message


class SiteList:
    """A list of web search results. User picks one by number to open in browser."""

    def __init__(self, items: list, message: str = ""):
        self.items = items
        self.message = message or f"Found {len(items)} result{'s' if len(items) != 1 else ''}"

    def format_items(self) -> list:
        lines = []
        for i, s in enumerate(self.items, 1):
            title = s['title']
            if len(title) > 52:
                title = title[:49] + '...'
            lines.append(f"{i}.  {title}  —  {s['domain']}")
        return lines

    def __str__(self) -> str:
        return self.message
