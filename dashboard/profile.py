"""The profile screens (SC-46): /profile for first-time setup and for settings, its live preview, and
pause/resume alerts. One template, two modes: "setup" until the person has onboarded, "settings" after.

Form parsing and the preview rule are plain functions so they can be tested without a database; the
routes at the bottom are registered by dashboard/app.py (register)."""
from datetime import datetime, timedelta, timezone

from flask import g, jsonify, redirect, render_template, request, url_for

from dashboard.filters import WORK_TYPES, work_type
from dashboard.skill_filter import apply_skill_cutoff
from db.db import (
    create_profile_from_legacy,
    get_conn,
    get_open_postings,
    get_resume,
    get_resume_parse_result,
    get_user_keywords,
    get_user_profile,
    get_user_role_categories,
    get_user_skill_cutoff,
    get_user_work_types,
    get_week_stats,
    is_onboarded,
    legacy_alerts_start,
    request_wide_catch_up,
    save_profile,
    set_alerts_paused,
    set_user_skill_cutoff,
)
from role_categories import (
    DEFAULT_WORK_TYPES,
    LEVEL_LABELS,
    LEVELS,
    gate_widened,
    matches_city,
    matches_profile_titles,
    matches_work_type,
    profile_from_legacy,
)

ANY_LEVEL = "Any level"
LEVEL_OPTIONS = [ANY_LEVEL] + [LEVEL_LABELS[level] for level in LEVELS]
_LEVEL_BY_LABEL = {label: key for key, label in LEVEL_LABELS.items()}
_WORK_KEY_BY_LABEL = {label: key for key, label in WORK_TYPES.items()}
WORK_OPTIONS = list(WORK_TYPES.values())

MAX_TITLES = 20
MAX_SKILLS = 30
MAX_VALUE_LENGTH = 60
MAX_CITY_LENGTH = 80
MAX_SKILL_CUTOFF = 10
PREVIEW_DAYS = 7
PREVIEW_SHOW = 3
NO_TITLE_ERROR = "Add at least one job title to continue."


def clean_values(values, max_items, max_length=MAX_VALUE_LENGTH):
    """Chip values from a form: whitespace collapsed, 2 to max_length characters, no repeats
    (ignoring case), in the order given, at most max_items."""
    seen, out = set(), []
    for raw in values:
        value = " ".join((raw or "").split())
        key = value.lower()
        if len(value) < 2 or len(value) > max_length or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out[:max_items]


def parse_cutoff(raw):
    """The minimum number of skills a role must mention, from the form: 1 to MAX_SKILL_CUTOFF, or None for no minimum."""
    try:
        n = int((raw or "").strip())
    except ValueError:
        return None
    return n if 1 <= n <= MAX_SKILL_CUTOFF else None


def cutoff_options():
    """The choices for the minimum, as (value, label) pairs: no minimum, then 1 of my skills, 2 of my skills..."""
    return [("", "No minimum")] + [(str(n), f"{n} of my skills" if n > 1 else "1 of my skills") for n in range(1, MAX_SKILL_CUTOFF + 1)]


def parse_profile_form(form):
    """The profile as the person filled it in. Anything typed into an "Add" field but not yet added
    counts too. No level means every level; no work type means the default (Remote), kept as None."""
    level = _LEVEL_BY_LABEL.get(form.get("level", ""))
    chosen = {_WORK_KEY_BY_LABEL[w] for w in form.getlist("work") if w in _WORK_KEY_BY_LABEL}
    note = " ".join((form.get("level_note") or "").split())[:200] or None
    return {
        "titles": clean_values(form.getlist("titles") + [form.get("titles_new", "")], MAX_TITLES),
        "skills": clean_values(form.getlist("skills") + [form.get("skills_new", "")], MAX_SKILLS),
        "level": level,
        "level_note": note if level and form.get("level_note_level") == level else None,
        "one_level_up": bool(level) and level != LEVELS[-1] and "one_level_up" in form,
        "city": " ".join((form.get("city") or "").split())[:MAX_CITY_LENGTH] or None,
        "work_types": [key for key in WORK_TYPES if key in chosen] or None,
        "skill_cutoff": parse_cutoff(form.get("skill_cutoff")),
    }


def ask_for_catch_up(conn):
    """Someone's settings now let in roles that discovered boards did not store. Ask the collector to pick
    them up quietly on its next scans (SC-51 slice 3). Never fails the save that led here: that is already
    committed, and a missed request only means roles are picked up when the next widening asks."""
    try:
        request_wide_catch_up(conn)
        conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"catch-up request failed: {type(e).__name__}: {e}")
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return False


def effective_work_types(work_types):
    """The Work types a profile stands for: what was chosen, or the default (Remote)."""
    return list(work_types) if work_types is not None else list(DEFAULT_WORK_TYPES)


def differs(a, b):
    """Do two parsed profiles say something different? Order of chips doesn't count."""
    def norm(p):
        return (
            sorted(t.lower() for t in p["titles"]), sorted(s.lower() for s in p["skills"]), p["level"],
            p["one_level_up"], (p["city"] or "").lower(), tuple(effective_work_types(p["work_types"])),
            p.get("skill_cutoff") or 0,
        )
    return norm(a) != norm(b)


def changed_count(saved, draft):
    """How many parts of the profile the unsaved form changes (for the action bar's note)."""
    pairs = [
        (sorted(t.lower() for t in saved["titles"]), sorted(t.lower() for t in draft["titles"])),
        (sorted(s.lower() for s in saved["skills"]), sorted(s.lower() for s in draft["skills"])),
        (saved["level"], draft["level"]),
        (saved["one_level_up"], draft["one_level_up"]),
        ((saved["city"] or "").lower(), (draft["city"] or "").lower()),
        (effective_work_types(saved["work_types"]), effective_work_types(draft["work_types"])),
        (saved.get("skill_cutoff") or 0, draft.get("skill_cutoff") or 0),
    ]
    return sum(1 for a, b in pairs if a != b)


def snapshot_of(model):
    """The profile as profile.js reads the form (same normalisation), so the page can tell what has changed
    from the saved profile even when it is opened already holding an unsaved draft."""
    return {
        "titles": "|".join(sorted(t.lower() for t in model["titles"])),
        "skills": "|".join(sorted(s.lower() for s in model["skills"])),
        "level": LEVEL_LABELS.get(model["level"], ANY_LEVEL),
        "up": "1" if model["one_level_up"] else "",
        "city": (model["city"] or "").lower(),
        "work": "|".join(sorted(WORK_TYPES[k] for k in effective_work_types(model["work_types"]))),
        "cutoff": str(model.get("skill_cutoff") or ""),
    }


def preview_rows(rows, draft):
    """Of these recent postings, the ones this profile would have shown (newest first)."""
    prefs = draft["work_types"]
    out = []
    for row in rows:
        wtype = work_type(row["location"], row["remote_type"])
        if (matches_profile_titles(row["title"], draft["titles"], draft["level"], draft["one_level_up"])
                and matches_work_type(wtype, prefs)
                and matches_city(wtype, row["location"], draft["city"])):
            out.append(row)
    return out


def preview_for(conn, user_id, draft, days=PREVIEW_DAYS):
    """Postings first seen in the last `days` that this profile would have shown, newest first."""
    if not draft["titles"]:
        return []
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = preview_rows(get_open_postings(conn, user_id, since=since, keywords=draft["titles"]), draft)
    # The count has to follow the minimum, or it would promise roles the dashboard then hides.
    return apply_skill_cutoff(conn, rows, draft.get("skills"), draft.get("skill_cutoff"))[0]


def _legacy_titles(conn, user_id):
    return profile_from_legacy(get_user_role_categories(conn, user_id), get_user_keywords(conn, user_id))


def saved_profile(conn, user_id, onboarded):
    """The profile as saved, in the parsed shape, plus its alert state. Someone with no profile row
    yet is shown what their old role categories and keywords stand for, so nothing they have changes
    until they save. A person who never onboarded and has no old picks starts empty."""
    profile = get_user_profile(conn, user_id)
    work_types = get_user_work_types(conn, user_id)
    if profile is not None:
        model = {"titles": profile["titles"], "skills": profile["skills"], "level": profile["level"],
                 "level_note": profile.get("level_note"), "one_level_up": profile["one_level_up"],
                 "city": profile["city"]}
        started, paused = profile["alerts_started_at"], profile["alerts_paused_at"]
    else:
        has_old_picks = bool(get_user_role_categories(conn, user_id) or get_user_keywords(conn, user_id))
        titles = _legacy_titles(conn, user_id) if (onboarded or has_old_picks) else []
        model = {"titles": titles, "skills": [], "level": None, "level_note": None, "one_level_up": False,
                 "city": None}
        started, paused = (legacy_alerts_start(conn, user_id) if onboarded else None), None
    model["work_types"] = work_types
    model["skill_cutoff"] = get_user_skill_cutoff(conn, user_id)
    model["alerts_started_at"], model["alerts_paused_at"] = started, paused
    return model


def ensure_profile(conn, user_id):
    """Give someone a profile row made from their old picks if they have none (for pausing)."""
    if get_user_profile(conn, user_id) is None:
        create_profile_from_legacy(conn, user_id, _legacy_titles(conn, user_id), legacy_alerts_start(conn, user_id))


def date_label(dt):
    return f"{dt:%b} {dt.day}" if dt else ""


def alerts_switch_state(model):
    """(on, detail line) for the alerts switch: "On since Oct 1" or "Paused since Oct 21"."""
    paused, started = model.get("alerts_paused_at"), model.get("alerts_started_at")
    if paused:
        return False, f"Paused since {date_label(paused)}"
    return True, (f"On since {date_label(started)}" if started else "On")


def view_context(conn, user_id, mode, model, saved=None, **extra):
    """Everything profile.html and _profile_preview.html need. `saved` is the saved profile when
    `model` is an unsaved draft of it (the settings preview then also shows the change)."""
    from dashboard import resume as resume_mod  # imported here: resume.py imports this module

    rows = preview_for(conn, user_id, model)
    ctx = {
        "mode": mode,
        "model": model,
        "level_options": LEVEL_OPTIONS,
        "level_label": LEVEL_LABELS.get(model["level"], ANY_LEVEL),
        "work_options": WORK_OPTIONS,
        "work_checked": [WORK_TYPES[k] for k in effective_work_types(model["work_types"])],
        "cutoff_options": cutoff_options(),
        "cutoff_selected": str(model.get("skill_cutoff") or ""),
        "preview_count": len(rows),
        "preview_items": rows[:PREVIEW_SHOW],
        "has_titles": bool(model["titles"]),
        "compare": None,
        "week": get_week_stats(conn, user_id) if mode == "settings" else None,
        "alerts_on": alerts_switch_state(model)[0],
        "alerts_detail": alerts_switch_state(model)[1],
        "changes": 0,
        "saved_snapshot": snapshot_of(saved if saved is not None else model),
        "resume": get_resume(conn, user_id),
        "reading": resume_mod.reading_state(conn, user_id),
        "notice": None,
        "reading_steps": resume_mod.READING_STEPS,
    }
    if saved is not None and differs(saved, model):
        ctx["compare"] = (len(preview_for(conn, user_id, saved)), len(rows))
        ctx["changes"] = changed_count(saved, model)
    ctx.update(extra)
    return ctx


def register(app, require_login):
    @app.route("/profile", methods=["GET", "POST"])
    @require_login
    def profile():
        user_id = g.user_id
        conn = get_conn()
        try:
            onboarded = is_onboarded(conn, user_id)
            mode = "settings" if onboarded else "setup"
            if request.method == "POST":
                draft = parse_profile_form(request.form)
                if not draft["titles"]:
                    saved = saved_profile(conn, user_id, onboarded)
                    draft.update(alerts_started_at=saved["alerts_started_at"], alerts_paused_at=saved["alerts_paused_at"])
                    return render_template("profile.html", **view_context(
                        conn, user_id, mode, draft, saved=saved if onboarded else None, error=NO_TITLE_ERROR)), 400
                before = saved_profile(conn, user_id, onboarded)
                save_profile(conn, user_id, **{k: v for k, v in draft.items() if k != "skill_cutoff"})
                set_user_skill_cutoff(conn, user_id, draft["skill_cutoff"])
                conn.commit()
                if gate_widened(before, draft):
                    ask_for_catch_up(conn)
                return redirect(url_for("profile") if onboarded else url_for("index"))
            from dashboard import resume as resume_mod

            saved = saved_profile(conn, user_id, onboarded)
            model = saved
            parse_id = request.args.get("from_resume", type=int)
            fields = get_resume_parse_result(conn, user_id, parse_id) if parse_id else None
            if fields:
                model = resume_mod.merge_fields(saved, fields)
            return render_template("profile.html", **view_context(
                conn, user_id, mode, model, saved=saved if onboarded else None,
                notice=resume_mod.pop_notice()))
        finally:
            conn.close()

    @app.route("/profile/preview", methods=["POST"])
    @require_login
    def profile_preview():
        """The side panel for the form as it stands now, as an HTML fragment the page swaps in."""
        user_id = g.user_id
        conn = get_conn()
        try:
            onboarded = is_onboarded(conn, user_id)
            mode = "settings" if onboarded else "setup"
            draft = parse_profile_form(request.form)
            saved = saved_profile(conn, user_id, onboarded)
            draft.update(alerts_started_at=saved["alerts_started_at"], alerts_paused_at=saved["alerts_paused_at"])
            return render_template("_profile_preview.html", **view_context(
                conn, user_id, mode, draft, saved=saved if onboarded else None))
        finally:
            conn.close()

    @app.route("/profile/alerts", methods=["POST"])
    @require_login
    def profile_alerts():
        user_id = g.user_id
        conn = get_conn()
        try:
            pause = request.form.get("action") == "pause"
            if is_onboarded(conn, user_id):
                ensure_profile(conn, user_id)
                set_alerts_paused(conn, user_id, pause)
                conn.commit()
            on, detail = alerts_switch_state(saved_profile(conn, user_id, is_onboarded(conn, user_id)))
            # The switch (field-guide/js/switch.js) asks for JSON and flips in place, so unsaved edits
            # elsewhere on the page survive. A plain form post just reloads: the switch and its date
            # line say what happened, so there is no notice banner.
            if request.accept_mimetypes.best == "application/json":
                message = "Alerts on." if on else "Alerts paused."  # read out by screen readers only
                return jsonify(on=on, value="pause" if on else "resume", detail=detail, message=message)
            return redirect(url_for("profile"))
        finally:
            conn.close()
