"""SC-73: the check-in pushes ("Did you hear back?", "Should I close it?"), sent by the collector.

The rules (days 5, 12 and 21; at most three per job) are in checkins.py. On top of them, here: one push a day per person
at most, with the jobs due together grouped into it; none before 9 AM Central (the first run after 9 sends it); none for
a person whose alerts are paused or not started; none without an ntfy topic. A push that fails is not counted, so the next
run tries again."""
from datetime import datetime, timezone

from checkins import PUSH_FROM_HOUR, push_level
from db.db import ensure_alert_token, get_checkin_candidates, mark_checkins_pushed
from role_categories import alerts_active
from scheduler.notify import LOCAL_TZ, checkin_link, notify_checkins, tracking_allowed


def plan(candidates, profiles, now):
    """{user_id: {"topic", "items": [...], "levels": {posting_id: level}}} for the pushes to send at `now`, and the Central
    date they are for. Nothing is planned before PUSH_FROM_HOUR. `profiles` is {user_id: profile} (alerts_active)."""
    local = now.astimezone(LOCAL_TZ)
    if local.hour < PUSH_FROM_HOUR:
        return {}, local.date()
    out = {}
    for c in candidates:
        uid = c["user_id"]
        if c.get("checkin_pushed_on") == local.date() or not c.get("ntfy_topic"):
            continue
        profile = profiles.get(uid)
        if profile is not None and not alerts_active(profile):
            continue
        level = push_level(c, now)
        if not level:
            continue
        entry = out.setdefault(uid, {"topic": c["ntfy_topic"], "items": [], "levels": {}})
        entry["items"].append({"posting_id": c["posting_id"], "company": c["company_name"], "title": c["title"],
                               "level": level, "days": (now - c["applied_at"]).days})
        entry["levels"][c["posting_id"]] = level
    return out, local.date()


def send(conn, profiles, health_alerts, now=None):
    """Send today's check-in pushes. Never raises: a failure is reported in health_alerts and the run carries on."""
    now = now or datetime.now(timezone.utc)
    try:
        pushes, today = plan(get_checkin_candidates(conn), profiles, now)
        failures = 0
        for uid, entry in pushes.items():
            token = ensure_alert_token(conn, uid, entry["items"][0]["posting_id"]) if tracking_allowed(now) else None
            click = checkin_link(token, grouped=len(entry["items"]) > 1)
            if notify_checkins(entry["items"], entry["topic"], click):
                mark_checkins_pushed(conn, uid, entry["levels"], today)
            else:
                failures += 1
            conn.commit()
        if pushes:
            print(f"{len(pushes)} check-in push(es) for {len(pushes)} user(s)" + (f", {failures} failed" if failures else ""))
        if failures:
            health_alerts.append(f"Check-in push failed for {failures} of {len(pushes)} user(s) this run; it is tried again next run.")
    except Exception as e:  # noqa: BLE001 - a check-in must never take the run down
        print(f"check-in pushes failed: {type(e).__name__}: {e}")
        health_alerts.append(f"Check-in pushes failed this run: {type(e).__name__}: {e}")
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
