"""Dependency-free natural-time parsing for voice reminders.

Turns spoken time phrases into a concrete next-fire `datetime` plus an
optional recurrence spec. Deliberately small and tailored to how people
actually speak to a voice assistant — no external NLP dependency.

Public API:
    parse_when(text, now=None) -> dict | None
        {"trigger": datetime, "recurrence": dict|None, "label": str}

    split(text, now=None) -> (message: str, when: dict|None)
        Separate a reminder body from a trailing time phrase, e.g.
        "call mom at 3pm" -> ("call mom", <when for 3pm>).

Recurrence spec shapes (consumed by commands.reminders):
    {"kind": "daily",    "hour": H, "minute": M}
    {"kind": "weekly",   "hour": H, "minute": M, "weekdays": [0..6]}   # Mon=0
    {"kind": "interval", "seconds": N}
"""
import re
from datetime import datetime, timedelta

# Named times of day -> (hour, minute). Used for "every morning", "tonight", etc.
_NAMED_TIMES = {
    'noon':      (12, 0),
    'midday':    (12, 0),
    'midnight':  (0, 0),
    'morning':   (8, 0),
    'afternoon': (15, 0),
    'evening':   (20, 0),
    'tonight':   (20, 0),
    'night':     (21, 0),
    'lunch':     (12, 0),
    'lunchtime': (12, 0),
}

# Weekday name -> Python weekday() index (Monday=0).
_WEEKDAYS = {
    'monday': 0, 'mon': 0, 'tuesday': 1, 'tue': 1, 'tues': 1,
    'wednesday': 2, 'wed': 2, 'thursday': 3, 'thu': 3, 'thurs': 3,
    'friday': 4, 'fri': 4, 'saturday': 5, 'sat': 5, 'sunday': 6, 'sun': 6,
}
_WEEKDAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday',
                  'Friday', 'Saturday', 'Sunday']

# Connector words that mark where a time phrase begins inside a sentence.
# Longest / most-specific first so "every day" wins over "day".
_CONNECTORS = [
    'every', 'tomorrow', 'tonight', 'today', 'at ', 'in ', 'on ', 'next ',
    'this ',
]


def _fmt_clock(h: int, m: int) -> str:
    """12-hour clock label, e.g. (15, 30) -> '3:30 PM'."""
    suffix = 'AM' if h < 12 else 'PM'
    hh = h % 12 or 12
    return f"{hh}:{m:02d} {suffix}"


def _parse_clock(s: str):
    """Parse a bare clock expression -> (hour, minute) or None.
    Accepts '3pm', '3:30 pm', '15:00', '9', 'noon', 'midnight'."""
    s = s.strip().lower()
    if s in _NAMED_TIMES:
        return _NAMED_TIMES[s]

    m = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?$', s)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = (m.group(3) or '').replace('.', '')
    if hour > 23 or minute > 59:
        return None
    if ampm == 'pm' and hour < 12:
        hour += 12
    elif ampm == 'am' and hour == 12:
        hour = 0
    elif not ampm and hour <= 7:
        # Bare small hour with no am/pm — assume daytime/evening, not 1-7 AM.
        # "remind me at 3" almost always means 3 PM.
        hour += 12
    return hour, minute


def _next_at(now: datetime, hour: int, minute: int) -> datetime:
    """The next datetime at hour:minute that is strictly after `now`."""
    cand = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if cand <= now:
        cand += timedelta(days=1)
    return cand


def _next_weekday(now: datetime, target_wd: int, hour: int, minute: int) -> datetime:
    """Next occurrence of weekday target_wd at hour:minute (could be today)."""
    days = (target_wd - now.weekday()) % 7
    cand = now.replace(hour=hour, minute=minute, second=0, microsecond=0) \
              + timedelta(days=days)
    if cand <= now:
        cand += timedelta(days=7)
    return cand


def _parse_recurring(text: str, now: datetime):
    """Handle 'every ...' phrases. Returns a when-dict or None."""
    t = text.strip().lower()
    m = re.match(r'^every\s+(.*)$', t)
    if not m:
        return None
    rest = m.group(1).strip()

    # "every 30 minutes" / "every 2 hours" / "every hour" / "every minute"
    m2 = re.match(r'^(\d+)?\s*(minute|min|hour)s?$', rest)
    if m2:
        n = int(m2.group(1) or 1)
        unit = m2.group(2)
        secs = n * (60 if unit.startswith('min') else 3600)
        rec = {'kind': 'interval', 'seconds': secs}
        unit_label = 'minute' if unit.startswith('min') else 'hour'
        plural = '' if n == 1 else 's'
        label = f"every {n} {unit_label}{plural}" if n != 1 else f"every {unit_label}"
        return {'trigger': now + timedelta(seconds=secs),
                'recurrence': rec, 'label': label}

    # Split an optional "at <time>" tail off the day spec.
    day_part, time_part = rest, None
    at = re.search(r'\bat\s+(.+)$', rest)
    if at:
        day_part = rest[:at.start()].strip()
        time_part = at.group(1).strip()

    # Determine time-of-day.
    if time_part:
        hm = _parse_clock(time_part)
        if hm is None:
            return None
        hour, minute = hm
    elif day_part in ('morning', 'afternoon', 'evening', 'night'):
        hour, minute = _NAMED_TIMES[day_part]
        day_part = 'day'
    else:
        hour, minute = 9, 0  # sensible default for "every day"

    # Which days?
    if day_part in ('day', 'daily', ''):
        weekdays = list(range(7))
        days_label = 'day'
    elif day_part in ('weekday', 'weekdays'):
        weekdays = [0, 1, 2, 3, 4]
        days_label = 'weekday'
    elif day_part in ('weekend', 'weekends'):
        weekdays = [5, 6]
        days_label = 'weekend'
    elif day_part in _WEEKDAYS:
        weekdays = [_WEEKDAYS[day_part]]
        days_label = _WEEKDAY_NAMES[_WEEKDAYS[day_part]]
    else:
        return None

    rec = {'kind': 'weekly' if len(weekdays) < 7 else 'daily',
           'hour': hour, 'minute': minute}
    if rec['kind'] == 'weekly':
        rec['weekdays'] = weekdays

    # First fire = soonest matching weekday at the time.
    trigger = min(_next_weekday(now, wd, hour, minute) for wd in weekdays)
    label = f"every {days_label} at {_fmt_clock(hour, minute)}"
    return {'trigger': trigger, 'recurrence': rec, 'label': label}


def parse_when(text, now=None):
    """Parse a time phrase into {trigger, recurrence, label} or None."""
    if now is None:
        now = datetime.now()
    t = (text or '').strip().lower()
    if not t:
        return None

    # Recurring first ("every ...").
    if t.startswith('every'):
        return _parse_recurring(t, now)

    # "in N minutes/hours"
    m = re.match(r'^in\s+(\d+)\s+(second|sec|minute|min|hour)s?$', t)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        secs = n * (1 if unit.startswith('sec') else 60 if unit.startswith('min') else 3600)
        trigger = now + timedelta(seconds=secs)
        return {'trigger': trigger, 'recurrence': None,
                'label': f"in {n} {unit}{'s' if n != 1 else ''}"}

    # Relative day prefixes: tomorrow / today / tonight / this evening
    day_offset = 0
    forced_named = None
    if t.startswith('tomorrow'):
        day_offset = 1
        t = t[len('tomorrow'):].strip()
    elif t.startswith('today'):
        t = t[len('today'):].strip()
    elif t.startswith('tonight'):
        forced_named = 'tonight'
        t = t[len('tonight'):].strip()
    elif t.startswith('this '):
        rest = t[len('this '):].strip()
        if rest.split(' ')[0] in _NAMED_TIMES:
            forced_named = rest.split(' ')[0]
            t = rest[len(forced_named):].strip()

    # "on monday at 9" / "next monday" / "monday at 9am"
    wd_match = re.match(r'^(?:on\s+|next\s+)?(' + '|'.join(_WEEKDAYS) + r')\b(.*)$', t)
    if wd_match:
        wd = _WEEKDAYS[wd_match.group(1)]
        rest = wd_match.group(2).strip()
        at = re.search(r'\bat\s+(.+)$', rest) or re.match(r'^(.+)$', rest)
        hm = _parse_clock(at.group(1)) if (at and at.group(1).strip()) else (9, 0)
        if hm is None:
            hm = (9, 0)
        trigger = _next_weekday(now, wd, hm[0], hm[1])
        return {'trigger': trigger, 'recurrence': None,
                'label': f"{_WEEKDAY_NAMES[wd]} at {_fmt_clock(*hm)}"}

    # Strip a leading "at".
    at = re.match(r'^at\s+(.+)$', t)
    if at:
        t = at.group(1).strip()

    # Resolve the clock.
    if forced_named:
        hm = _NAMED_TIMES[forced_named]
    elif t:
        hm = _parse_clock(t)
    elif day_offset:
        hm = (9, 0)            # "tomorrow" with no time -> 9 AM
    else:
        hm = None

    if hm is None:
        return None

    base = now + timedelta(days=day_offset)
    cand = base.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
    if cand <= now:
        cand += timedelta(days=1)

    if day_offset == 1:
        label = f"tomorrow at {_fmt_clock(*hm)}"
    elif forced_named in ('tonight',):
        label = f"tonight at {_fmt_clock(*hm)}"
    else:
        label = f"at {_fmt_clock(*hm)}"
    return {'trigger': cand, 'recurrence': None, 'label': label}


def split(text, now=None):
    """Split a reminder body from a trailing time phrase.

    "call mom at 3pm"        -> ("call mom", <when at 3pm>)
    "stretch every day at 8" -> ("stretch", <daily when>)
    "check the oven"         -> ("check the oven", None)
    """
    if now is None:
        now = datetime.now()
    t = (text or '').strip()
    low = t.lower()

    # Find the earliest connector that introduces a parseable time tail.
    best_idx = None
    for conn in _CONNECTORS:
        start = 0
        while True:
            i = low.find(conn, start)
            if i < 0:
                break
            # Require a word boundary before the connector.
            if i == 0 or not low[i - 1].isalnum():
                tail = t[i:].strip()
                if parse_when(tail, now) is not None:
                    if best_idx is None or i < best_idx:
                        best_idx = i
                    break
            start = i + 1

    if best_idx is None:
        return t, None

    message = t[:best_idx].strip(' ,.')
    when = parse_when(t[best_idx:].strip(), now)
    return message, when
