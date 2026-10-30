"""What makes the Open roles view feel like Scout (SC-69): day groups, short times, company monograms and Scout's line.
Pure functions, so the rules can be tested without a page or a database.

Days are the person's calendar days in APP_TZ (US Central, where Scout's people are), not UTC: a job found at 9:40 PM
Tuesday Central is "Today" on Tuesday evening, not "Yesterday"."""
import re
import zlib
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    APP_TZ = ZoneInfo("America/Chicago")
except Exception:  # noqa: BLE001 - no timezone database: days fall back to UTC rather than failing the page
    APP_TZ = timezone.utc

DAY_LABELS = ("Today", "Yesterday", "Earlier this week", "Last week", "Earlier")
TINTS = ("sage", "gold", "clay", "forest")  # Field Guide tints: support, discovery, attention, progress (all subtle)
_STOP = {"the", "and", "of", "inc", "llc", "ltd", "co", "corp", "company"}


def day_label(when, now=None):
    """Today, Yesterday, Earlier this week, Last week or Earlier, by calendar day in APP_TZ. Weeks run Sunday to Saturday
    (as in the US): "Earlier this week" is the rest of this week before yesterday, "Last week" is the whole week before
    it, and anything older is "Earlier"."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(APP_TZ).date()
    day = when.astimezone(APP_TZ).date()
    days = (today - day).days
    if days <= 0:
        return DAY_LABELS[0]
    if days == 1:
        return DAY_LABELS[1]
    this_week = today - timedelta(days=(today.weekday() + 1) % 7)   # the Sunday that began this week
    if day >= this_week:
        return DAY_LABELS[2]
    if day >= this_week - timedelta(days=7):
        return DAY_LABELS[3]
    return DAY_LABELS[4]


def short_time(when, now=None):
    """The time on a row: "9:40 AM" for today and yesterday, "Mon" for this week and last week, "Oct 14" beyond that."""
    local = when.astimezone(APP_TZ)
    label = day_label(when, now)
    if label in (DAY_LABELS[0], DAY_LABELS[1]):
        return local.strftime("%-I:%M %p")
    if label in (DAY_LABELS[2], DAY_LABELS[3]):
        return local.strftime("%a")
    return local.strftime("%b %-d")


def group_by_day(postings, now=None):
    """[(label, how many in all, [the postings of that group in the order given])] for the labels that have any.
    `postings` is every posting that matches (so the counts are true); the caller passes the page it shows as
    `shown` to build the rows (see rows_by_day)."""
    counts = {}
    for p in postings:
        label = day_label(p["first_seen_at"], now)
        counts[label] = counts.get(label, 0) + 1
    return counts


def rows_by_day(shown, counts, now=None):
    """The shown postings grouped under their day heading, each with the group's true count."""
    groups = {}
    for p in shown:
        groups.setdefault(day_label(p["first_seen_at"], now), []).append(p)
    return [(label, counts[label], groups[label]) for label in DAY_LABELS if label in groups]


def monogram(company):
    """Two letters for a company: the first letters of its first two words, or the first two letters of a single
    word. "Juniper Labs" -> JL, "Docker" -> DO, "Harbor & Co." -> HA ("&" and company suffixes like Co. and Inc are skipped)."""
    words = [w for w in re.findall(r"[A-Za-z0-9]+", company or "") if w.lower() not in _STOP]
    if not words:
        words = re.findall(r"[A-Za-z0-9]+", company or "")
    if not words:
        return ""
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


def tint(company):
    """The same soft tint every time for a company (stable across visits and servers)."""
    return TINTS[zlib.crc32((company or "").strip().lower().encode("utf-8")) % len(TINTS)]


def scout_line(new_today, total, caught_up, close=""):
    """Scout's line above the board, built from real counts, or "" when there is nothing true to say (a filter has
    emptied the list). `close` is the fit sentence (dashboard.fit.close_phrase), added when some jobs score 4 or 5 pips."""
    if caught_up:
        return "You’re caught up. I’ll keep looking."
    if not total:
        return ""
    first = f"{new_today} new since yesterday." if new_today else "Nothing new since yesterday."
    return f"{first} {close}" if close else first
