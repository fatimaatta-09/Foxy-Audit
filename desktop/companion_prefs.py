"""Foxy Audit desktop — what the companion settings MEAN (D13, plan §9.2).

The catalogue in §9.2 is mostly booleans the caller can read directly, but
four of the controls are choices that have to be turned into numbers, and one
is a rule about time. Those live here, with no Qt, so the arithmetic is
testable and there is exactly one definition of each.

* **frequency → tick range.** The fox's idle breaks and compliance tips are
  driven by a countdown on the 100 ms animation tick, so "normal" has to
  become a pair of tick counts. Naming the presets rather than exposing raw
  numbers keeps a user from setting a 200 ms idle-break loop.
* **quiet hours.** A range that can wrap past midnight, which is the case
  everybody gets wrong: 22:00–07:00 is not "22 <= h <= 7", it is the union of
  two spans.

**What quiet hours suppress, and what they must not.** They silence the BEEP
and the native toast. They do not stop the fox reacting on screen, they do not
stop the event being recorded, and they do not stop the weekly tally. A
compliance companion that quietly dropped a breach because it was late would
be lying by omission — the user asked for silence, not for less evidence.
"""

from __future__ import annotations

#: (key, label, (min_ticks, max_ticks)) — ticks are 100 ms animation frames.
#: `None` means the behaviour is off entirely, which is a real option and not
#: the same as "very rare".
FREQUENCIES = (
    ("off", "Off", None),
    ("rare", "Rare", (1800, 3600)),          # ~3-6 min
    ("normal", "Normal", (600, 1200)),       # ~1-2 min — the shipped default
    ("frequent", "Frequent", (200, 450)),    # ~20-45 s
)

FREQUENCY_KEYS = tuple(key for key, _label, _range in FREQUENCIES)
DEFAULT_FREQUENCY = "normal"

_RANGES = {key: rng for key, _label, rng in FREQUENCIES}


def frequency_range(key: str | None) -> tuple[int, int] | None:
    """Tick bounds for a named frequency, or `None` when it is off.

    An unknown key falls back to the default rather than to off: a settings
    file written by a newer build must not silently switch the fox's idle
    breaks off on an older one.
    """
    return _RANGES.get((key or "").strip().lower(), _RANGES[DEFAULT_FREQUENCY])


def next_countdown(key: str | None, randint) -> int | None:
    """The next countdown value, or `None` if the behaviour is off.

    `randint` is passed in rather than imported so a test can pin it — the
    same reason `breach_poll.plan_reactions` takes its cursor as an argument.
    """
    bounds = frequency_range(key)
    if bounds is None:
        return None
    return randint(bounds[0], bounds[1])


#: What a left click on the fox does. The existing behaviour is `chat`, which
#: stays the default — a settings catalogue is not a licence to change what a
#: click already did for everyone.
CLICK_ACTIONS = (("chat", "Open the chat"),
                 ("panel", "Open the quick status panel"),
                 ("console", "Open the console"))
CLICK_ACTION_KEYS = tuple(key for key, _label in CLICK_ACTIONS)
DEFAULT_CLICK_ACTION = "chat"


def click_action(key: str | None) -> str:
    value = (key or "").strip().lower()
    return value if value in CLICK_ACTION_KEYS else DEFAULT_CLICK_ACTION


# ── fox appearance ──────────────────────────────────────────────────────────
SCALE_MIN, SCALE_MAX, SCALE_DEFAULT = 50, 150, 100
OPACITY_MIN, OPACITY_MAX, OPACITY_DEFAULT = 60, 100, 100


def scaled_size(width: int, height: int, percent: int) -> tuple[int, int]:
    """The sprite cell at `percent`, never smaller than something clickable.

    The floor is not decoration: at 50% of a 192×208 cell the fox is still
    96×104, but the same code runs if a settings file carries a smaller
    number, and a 6-pixel fox is unclickable and unfindable.
    """
    return (max(48, round(width * _pct(percent, SCALE_DEFAULT, SCALE_MIN,
                                       SCALE_MAX) / 100)),
            max(52, round(height * _pct(percent, SCALE_DEFAULT, SCALE_MIN,
                                        SCALE_MAX) / 100)))


def opacity_fraction(percent: int) -> float:
    """0.6-1.0. Clamped low so the fox can never be made invisible and then
    impossible to find in order to turn back up."""
    return _pct(percent, OPACITY_DEFAULT, OPACITY_MIN, OPACITY_MAX) / 100.0


def _pct(value, default: int, low: int, high: int) -> int:
    """Clamp a percentage, treating ONLY a missing value as "use the default".

    `int(value or default)` reads 0 as unset, so an opacity of 0 came back as
    100 — the opposite of what was asked for, and the opposite of the clamp
    this function exists to apply. A test caught it; the falsy-zero trap is
    worth the extra four lines.
    """
    if value is None or value == "":
        value = default
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


# ── alerts ──────────────────────────────────────────────────────────────────
POLL_MIN, POLL_MAX, POLL_DEFAULT = 5, 60, 10


def poll_interval(seconds) -> int:
    """Breach-poll seconds, clamped to §9.2's 5-60. Below 5 s a desktop app
    hammering a shared backend is a self-inflicted rate limit; above 60 s the
    fox stops being live, which is the point of it."""
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        return POLL_DEFAULT
    return max(POLL_MIN, min(POLL_MAX, value))


DEFAULT_QUIET_FROM, DEFAULT_QUIET_TO = "22:00", "07:00"


def parse_time(text: str | None) -> tuple[int, int] | None:
    """"HH:MM" → (hour, minute). `None` for anything unparseable, which the
    caller treats as "no quiet hours" rather than as midnight."""
    parts = (text or "").strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def in_quiet_hours(now_hm: tuple[int, int], start: str | None,
                   end: str | None, *, enabled: bool = True) -> bool:
    """Is `now` inside the quiet window?

    The window WRAPS: 22:00-07:00 is two spans, not one comparison, and
    treating it as `start <= now <= end` silences nothing at all. Equal
    endpoints mean a zero-length window, not a 24-hour one — a user who set
    both to the same time asked for no quiet hours, not permanent silence.
    """
    if not enabled:
        return False
    begin, finish = parse_time(start), parse_time(end)
    if begin is None or finish is None or begin == finish:
        return False
    now = now_hm[0] * 60 + now_hm[1]
    first = begin[0] * 60 + begin[1]
    last = finish[0] * 60 + finish[1]
    if first < last:
        return first <= now < last
    return now >= first or now < last          # wraps past midnight


def silence(react: dict | None, quiet: bool) -> dict | None:
    """Strip the noise from a reaction during quiet hours.

    The fox still animates and the overlay still flashes — what goes is the
    beep and the native toast. Dropping the reaction entirely would mean a
    breach at 3 a.m. left no trace on screen at all.
    """
    if react is None or not quiet:
        return react
    return {**react, "sound": False, "toast": None}


__all__ = ["CLICK_ACTIONS", "CLICK_ACTION_KEYS", "DEFAULT_CLICK_ACTION",
           "DEFAULT_FREQUENCY", "DEFAULT_QUIET_FROM", "DEFAULT_QUIET_TO",
           "FREQUENCIES", "FREQUENCY_KEYS", "OPACITY_DEFAULT", "OPACITY_MAX",
           "OPACITY_MIN", "POLL_DEFAULT", "POLL_MAX", "POLL_MIN",
           "SCALE_DEFAULT", "SCALE_MAX", "SCALE_MIN", "click_action",
           "frequency_range", "in_quiet_hours", "next_countdown",
           "opacity_fraction", "parse_time", "poll_interval", "scaled_size",
           "silence"]
