"""What makes the Open roles view feel like Scout (SC-69): day groups, short times, company monograms and Scout's line;
and (SC-73) the Applications tab's groups and status lines.
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


# ---------------------------------------------------------------------------------------------------------------------
# SC-73: the Applications tab. A person's stage for a job (db: user_postings.stage) plus whether the employer still has
# the role open decide which group a row is in and what its one status line says.
# ---------------------------------------------------------------------------------------------------------------------
APPLICATION_GROUPS = ("Waiting to hear", "Interviewing", "Offer", "Closed")
_STAGE_GROUP = {"waiting": "Waiting to hear", "interviewing": "Interviewing", "offer": "Offer", "not": "Closed", "mine": "Closed"}
_LONG_AGO = datetime.min.replace(tzinfo=timezone.utc)


def short_date(when):
    """"Nov 4": the date on a status line, in the person's calendar (APP_TZ)."""
    return when.astimezone(APP_TZ).strftime("%b %-d")


def application_group(a):
    """Which group an application is in. A role the employer has closed is Closed, unless the person is already talking
    to them (Interviewing, Offer), which is the better thing to show."""
    stage = a.get("stage") or "waiting"
    if stage == "waiting" and a.get("posting_status") == "closed":
        return "Closed"
    return _STAGE_GROUP.get(stage, APPLICATION_GROUPS[0])


def application_status(a):
    """(top, bottom): the one status a row shows, never its history. `applied_ago` is "5 days ago" or "" (an application
    from before the date was kept). The bottom line is "" when there is nothing more to say."""
    stage = a.get("stage") or "waiting"
    changed = short_date(a["stage_changed_at"]) if a.get("stage_changed_at") else ""
    if stage == "interviewing":
        return "Interviewing", f"Since {changed}" if changed else ""
    if stage == "offer":
        return "Offer", f"Since {changed}" if changed else ""
    if stage == "not":
        return "Not moving forward", changed
    if stage == "mine":
        return "Closed by you", changed
    if a.get("posting_status") == "closed":
        return "The employer closed this role", ""
    return "Waiting to hear", ("Applied " + a["applied_ago"]).strip() if a.get("applied_ago") else "Applied"


def group_applications(applications):
    """[(label, how many, [rows])] for the groups that have any, in APPLICATION_GROUPS order; inside a group the most
    recent activity comes first. Each row gets `group`, `status_top`, `status_bottom` and `closed` (dimmed)."""
    groups = {label: [] for label in APPLICATION_GROUPS}
    for a in applications:
        a["group"] = application_group(a)
        a["status_top"], a["status_bottom"] = application_status(a)
        a["closed"] = a["group"] == "Closed"
        groups[a["group"]].append(a)
    out = []
    for label in APPLICATION_GROUPS:
        rows = sorted(groups[label], key=lambda r: r.get("activity_at") or _LONG_AGO, reverse=True)
        if rows:
            out.append((label, len(rows), rows))
    return out


def applications_line(grouped):
    """Scout's line above the applications: what is going, from real counts ("1 interview going. 3 still waiting to
    hear."), or "" when nothing is going (an empty tab, or only closed ones)."""
    counts = {label: count for label, count, _ in grouped}
    parts = []
    if counts.get("Offer"):
        n = counts["Offer"]
        parts.append(f"{n} offer{'s' if n != 1 else ''} to answer.")
    if counts.get("Interviewing"):
        n = counts["Interviewing"]
        parts.append(f"{n} interview{'s' if n != 1 else ''} going.")
    if counts.get("Waiting to hear"):
        parts.append(f"{counts['Waiting to hear']} still waiting to hear.")
    return " ".join(parts)
