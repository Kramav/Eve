"""Example Eve skill — roll dice by voice. Copy this file to make your own.

Drop any `*.py` here that defines an `INTENTS` list and it loads at startup
with no changes to core. Try: "roll a die", "roll 2d6", "flip a coin".
"""
import random
import re

# Optional. Higher = matched before other skills. Default 0.
PRIORITY = 0

# Optional. Gate this whole skill on a features.json flag (omit to always run).
# FEATURE = "dice"


def _roll(spec: str | None = None) -> str:
    # spec like "2d6" → 2 dice, 6 sides; default 1d6
    count, sides = 1, 6
    if spec:
        m = re.fullmatch(r"\s*(\d*)\s*d\s*(\d+)\s*", spec.lower())
        if m:
            count = int(m.group(1) or 1)
            sides = int(m.group(2))
    count = max(1, min(count, 20))      # keep it sane
    sides = max(2, min(sides, 1000))
    rolls = [random.randint(1, sides) for _ in range(count)]
    if count == 1:
        return f"You rolled a {rolls[0]}."
    return f"You rolled {', '.join(map(str, rolls))} — total {sum(rolls)}."


def _flip() -> str:
    return f"It's {random.choice(('heads', 'tails'))}."


# (regex, handler) — same shape as core.dispatcher.INTENTS. Captured groups are
# passed positionally to the handler.
INTENTS = [
    (r"\b(?:flip|toss)\s+(?:a\s+)?coin\b",                 _flip),
    (r"\broll\s+(?:a\s+)?(\d*\s*d\s*\d+)\b",               _roll),   # "roll 2d6"
    (r"\broll\s+(?:a\s+)?(?:dice|die|d\d+)\b",             _roll),   # "roll a die"
]
