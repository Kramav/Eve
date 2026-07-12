"""Streaming / platform search — "look up <query> on <platform>".

The explicit trailing-marker counterpart to leading "play/watch" (docs: match on
an explicit intent marker, not greedy position). "stranger things on netflix" →
opens Netflix's search for it. YouTube has its own HUD skill and is deliberately
NOT handled here, so "on youtube" still routes to the feed.

Focus-safe by design: every open goes through core.browser.open_url, which uses
SW_SHOWNOACTIVATE and backs off entirely when a fullscreen game owns the screen
— so a search never yanks focus off your game (the flagship invariant).

Custom platforms: drop a platform_searches.json at the repo root to add or
override entries — {"crunchyroll": "https://www.crunchyroll.com/search?q={q}"}.
Use {q} for the URL-encoded query. Merged over the defaults below; a restart (or
hot-reload) picks up new names.
"""
import json
import re
import urllib.parse
from pathlib import Path

from core import browser

PREEMPT = True          # explicit "on <platform>" beats the web-search catch-all

# {q} is replaced with the URL-encoded query. Multiple spoken forms map to the
# same service on purpose (voice variance). YouTube is intentionally absent.
_DEFAULTS = {
    "netflix":      "https://www.netflix.com/search?q={q}",
    "spotify":      "https://open.spotify.com/search/{q}",
    "hulu":         "https://www.hulu.com/search?q={q}",
    "disney plus":  "https://www.disneyplus.com/search?q={q}",
    "disney+":      "https://www.disneyplus.com/search?q={q}",
    "disney":       "https://www.disneyplus.com/search?q={q}",
    "prime video":  "https://www.primevideo.com/search/?phrase={q}",
    "amazon prime": "https://www.primevideo.com/search/?phrase={q}",
    "prime":        "https://www.primevideo.com/search/?phrase={q}",
    "twitch":       "https://www.twitch.tv/search?term={q}",
    "hbo max":      "https://play.max.com/search?q={q}",
    "hbomax":       "https://play.max.com/search?q={q}",
    "max":          "https://play.max.com/search?q={q}",
    "peacock":      "https://www.peacocktv.com/search?q={q}",
    "apple tv":     "https://tv.apple.com/search?term={q}",
    "paramount plus": "https://www.paramountplus.com/search/?query={q}",
    "paramount+":   "https://www.paramountplus.com/search/?query={q}",
}

_CUSTOM_FILE = Path(__file__).parent.parent / "platform_searches.json"

# Leading search verbs stripped off the query ("look up X on netflix" → "X").
_LEAD = re.compile(
    r"^(?:look up|search(?:\s+for)?|find|pull up|show me|get me|watch|play|"
    r"put on|bring up|browse)\s+", re.I)


def _custom() -> dict:
    """User overrides from platform_searches.json (best-effort; {} on any error)."""
    try:
        data = json.loads(_CUSTOM_FILE.read_text())
        return {str(k).lower(): str(v) for k, v in data.items()
                if isinstance(v, str) and "{q}" in v}
    except Exception:
        return {}


def _platforms() -> dict:
    return {**_DEFAULTS, **_custom()}


def search_on_platform(query: str, platform: str):
    """Open <platform>'s search for <query>, focus-safe. Returns None (declines)
    if the platform is unknown, so the utterance falls through to normal
    dispatch instead of being wrongly claimed."""
    url_tmpl = _platforms().get(platform.strip().lower())
    if not url_tmpl:
        return None                              # not a known platform → decline
    q = _LEAD.sub("", query.strip())
    if not q:
        return None
    browser.open_url(url_tmpl.replace("{q}", urllib.parse.quote_plus(q)))
    return f"Searching {platform} for {q}"


# The platform alternation is built from the known names at load time so the
# regex only claims real platforms (a broad ".+ on .+" would swallow unrelated
# commands like "snap firefox on the left"). Longer names first so "disney plus"
# wins over "disney". Custom names are included when the module loads.
def _build_intents():
    names = sorted(_platforms().keys(), key=len, reverse=True)
    alt = "|".join(re.escape(n) for n in names)
    # "<query> on <platform>" with the platform at the END of the command — the
    # natural phrasing. End-anchoring stops false positives where a platform
    # name is just a word mid-sentence ("turn on max volume", "put it on hulu
    # later"). (query, platform) → search_on_platform.
    pattern = rf"^(.+?)\s+on\s+({alt})$"
    return [(pattern, search_on_platform)]


INTENTS = _build_intents()


if __name__ == "__main__":
    # ponytail: no network — prove routing + query/platform extraction + the
    # unknown-platform decline, without opening a browser.
    rx = re.compile(INTENTS[0][0])
    m = rx.search("look up stranger things on netflix")
    assert m and m.group(2) == "netflix"
    assert _LEAD.sub("", m.group(1)) == "stranger things"
    m = rx.search("the office on hbo max")
    assert m and m.group(2) == "hbo max" and m.group(1) == "the office"
    assert rx.search("snap firefox to the left") is None      # not "on <platform>"
    assert rx.search("play despacito on youtube") is None     # youtube not ours
    assert rx.search("turn on max volume") is None            # 'max' mid-sentence
    assert rx.search("put the window on the left") is None
    # unknown platform declines even if the shape matches (handler returns None)
    assert search_on_platform("stuff", "myspace") is None
    print("ok")
