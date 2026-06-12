class Silent(str):
    """Response shown briefly in the status bar only — not spoken, no large text."""
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
