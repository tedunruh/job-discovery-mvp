"""Offline tests for the profile screen (SC-46): form parsing, the preview rule, city matching and the
template in both modes (no network/DB)."""
import unittest
from datetime import datetime, timezone

from werkzeug.datastructures import MultiDict

import dashboard.app as dapp
from dashboard import profile as pf
from role_categories import matches_city


def form(**fields):
    md = MultiDict()
    for key, value in fields.items():
        for v in (value if isinstance(value, list) else [value]):
            md.add(key, v)
    return md


class CleanValues(unittest.TestCase):
    def test_collapses_dedupes_and_limits(self):
        out = pf.clean_values(["  Product   Designer ", "product designer", "x", "", "a" * 80, "UX Researcher"], 5)
        self.assertEqual(out, ["Product Designer", "UX Researcher"])

    def test_caps_the_count(self):
        self.assertEqual(len(pf.clean_values([f"title {i}" for i in range(50)], 20)), 20)


class ParseForm(unittest.TestCase):
    def test_a_full_form(self):
        p = pf.parse_profile_form(form(titles=["Product Designer", "UX Researcher"], skills=["Figma"], level="Senior",
                                       one_level_up="1", city="  Chicago ", work=["Remote", "Hybrid"]))
        self.assertEqual(p["titles"], ["Product Designer", "UX Researcher"])
        self.assertEqual(p["skills"], ["Figma"])
        self.assertEqual(p["level"], "senior")
        self.assertTrue(p["one_level_up"])
        self.assertEqual(p["city"], "Chicago")
        self.assertEqual(p["work_types"], ["remote", "hybrid"])

    def test_text_typed_but_not_added_still_counts(self):
        p = pf.parse_profile_form(form(titles="Product Designer", titles_new="Design Manager", skills_new="Prototyping"))
        self.assertEqual(p["titles"], ["Product Designer", "Design Manager"])
        self.assertEqual(p["skills"], ["Prototyping"])

    def test_any_level_and_nothing_chosen_are_empty(self):
        for fields in (form(titles="x1", level="Any level", one_level_up="1"), form(titles="x1")):
            p = pf.parse_profile_form(fields)
            self.assertIsNone(p["level"])
            self.assertFalse(p["one_level_up"])  # nothing to go one level up from

    def test_one_level_up_is_ignored_at_the_top_level(self):
        p = pf.parse_profile_form(form(titles="x1", level="Lead or manager", one_level_up="1"))
        self.assertEqual(p["level"], "lead_manager")
        self.assertFalse(p["one_level_up"])

    def test_no_work_type_chosen_stays_none_so_the_default_applies(self):
        self.assertIsNone(pf.parse_profile_form(form(titles="x1"))["work_types"])
        self.assertEqual(pf.effective_work_types(None), ["remote"])

    def test_unknown_values_are_ignored(self):
        p = pf.parse_profile_form(form(titles="x1", level="Wizard", work=["Remote", "Underwater"], city="  "))
        self.assertIsNone(p["level"])
        self.assertEqual(p["work_types"], ["remote"])
        self.assertIsNone(p["city"])

    def test_the_level_reason_is_kept_only_for_the_level_it_was_written_for(self):
        keep = pf.parse_profile_form(form(titles="x1", level="Senior", level_note="Led a team.", level_note_level="senior"))
        self.assertEqual(keep["level_note"], "Led a team.")
        changed = pf.parse_profile_form(form(titles="x1", level="Staff", level_note="Led a team.", level_note_level="senior"))
        self.assertIsNone(changed["level_note"])
        none = pf.parse_profile_form(form(titles="x1", level="Any level", level_note="Led a team.", level_note_level="senior"))
        self.assertIsNone(none["level_note"])

    def test_no_title_means_an_empty_list(self):
        self.assertEqual(pf.parse_profile_form(form(level="Senior"))["titles"], [])


class CityMatching(unittest.TestCase):
    def test_no_city_matches_everything(self):
        self.assertTrue(matches_city("onsite", "Paris, France", None))

    def test_city_only_narrows_hybrid_and_onsite(self):
        self.assertTrue(matches_city("remote", "Remote - US", "Chicago"))
        self.assertTrue(matches_city(None, "Remote - Brazil", "Chicago"))
        self.assertFalse(matches_city("onsite", "Austin, TX", "Chicago"))
        self.assertFalse(matches_city("hybrid", None, "Chicago"))

    def test_city_must_be_named_as_a_whole_word_in_any_case(self):
        self.assertTrue(matches_city("onsite", "Chicago, IL; New York, NY", "chicago"))
        self.assertFalse(matches_city("onsite", "Chicagoland Area", "Chicago"))


def posting(title, location="Remote - US", remote_type="remote"):
    return {"title": title, "location": location, "remote_type": remote_type, "company_name": "Acme",
            "first_seen_at": datetime(2026, 10, 18, tzinfo=timezone.utc)}


def draft(**over):
    base = {"titles": ["product designer"], "skills": [], "level": None, "one_level_up": False, "city": None,
            "work_types": None}
    base.update(over)
    return base


class PreviewRule(unittest.TestCase):
    rows = [
        posting("Senior Product Designer"),
        posting("Staff Product Designer"),
        posting("Product Designer", "Chicago, IL", None),
        posting("Product Designer", "Austin, TX", "hybrid"),
        posting("Software Engineer"),
    ]

    def titles(self, d):
        return [r["title"] + "|" + (r["location"] or "") for r in pf.preview_rows(self.rows, d)]

    def test_default_work_type_is_remote(self):
        self.assertEqual(len(pf.preview_rows(self.rows, draft())), 2)

    def test_level_narrows(self):
        got = pf.preview_rows(self.rows, draft(level="senior"))
        self.assertEqual([r["title"] for r in got], ["Senior Product Designer"])
        up = pf.preview_rows(self.rows, draft(level="senior", one_level_up=True))
        self.assertEqual(len(up), 2)

    def test_city_narrows_onsite_and_hybrid_only(self):
        d = draft(work_types=["remote", "hybrid", "onsite"], city="Chicago")
        got = [r["location"] for r in pf.preview_rows(self.rows, d)]
        self.assertIn("Chicago, IL", got)
        self.assertNotIn("Austin, TX", got)
        self.assertEqual(got.count("Remote - US"), 2)

    def test_no_titles_matches_nothing(self):
        self.assertEqual(pf.preview_rows(self.rows, draft(titles=[])), [])


class Changes(unittest.TestCase):
    def test_order_and_case_do_not_count(self):
        a = draft(titles=["Product Designer", "UX Researcher"])
        b = draft(titles=["ux researcher", "product designer"])
        self.assertFalse(pf.differs(a, b))
        self.assertEqual(pf.changed_count(a, b), 0)

    def test_counts_changed_parts(self):
        saved = draft()
        new = draft(titles=["product designer", "design manager"], level="senior", city="Chicago")
        self.assertTrue(pf.differs(saved, new))
        self.assertEqual(pf.changed_count(saved, new), 3)

    def test_every_part_is_counted_once(self):
        saved = draft()
        for change in (dict(skills=["Figma"]), dict(level="staff"), dict(city="Chicago"),
                       dict(work_types=["remote", "onsite"]), dict(titles=["design manager"])):
            self.assertEqual(pf.changed_count(saved, draft(**change)), 1, change)
        self.assertEqual(pf.changed_count(draft(level="senior"), draft(level="senior", one_level_up=True)), 1)

    def test_unset_work_type_equals_remote(self):
        self.assertFalse(pf.differs(draft(work_types=None), draft(work_types=["remote"])))


def render(mode, **over):
    model = draft(titles=["product designer", "design manager"], skills=["Figma"], level="senior", one_level_up=True,
                  city="Chicago", work_types=["remote", "hybrid"], alerts_started_at=None, alerts_paused_at=None)
    ctx = {
        "mode": mode, "model": model, "level_options": pf.LEVEL_OPTIONS, "level_label": "Senior",
        "work_options": pf.WORK_OPTIONS, "work_checked": ["Remote", "Hybrid"], "preview_count": 2,
        "preview_items": [posting("Senior Product Designer")], "has_titles": True, "compare": None,
        "week": {"reached": 21, "opened": 9, "applied": 2}, "started_label": "Oct 6", "paused_label": "",
        "changes": 0, "error": None, "saved_notice": False,
        "saved_snapshot": pf.snapshot_of(model), "resume": None, "reading": ("ok", None),
        "notice": None, "resume_added": False,
    }
    ctx.update(over)
    with dapp.app.test_request_context("/profile"):
        return dapp.app.jinja_env.get_template("profile.html").render(**ctx)


class Template(unittest.TestCase):
    def test_chips_carry_their_values_into_the_form(self):
        html = render("settings")
        self.assertIn('<input type="hidden" name="titles" value="product designer">', html)
        self.assertIn('<input type="hidden" name="skills" value="Figma">', html)
        self.assertIn('id="titles-list-template"', html)

    def test_current_choices_are_selected(self):
        html = render("settings")
        self.assertIn('value="Senior" checked', html)
        self.assertIn('name="one_level_up" value="1" checked', html)
        self.assertIn('value="Chicago"', html)
        self.assertIn('value="Remote" checked', html)
        self.assertNotIn('value="On-site" checked', html)

    def test_the_level_choices_include_any_level_and_no_other_extras(self):
        html = render("settings")
        for label in ["Any level", "Mid", "Senior", "Staff", "Principal", "Lead or manager"]:
            self.assertIn(f'value="{label}"', html)

    def test_one_level_up_is_hidden_with_no_level(self):
        html = render("settings", model=draft(level=None, alerts_started_at=None, alerts_paused_at=None), level_label="Any level")
        self.assertRegex(html, r"data-needs-level hidden")

    def test_settings_mode_has_status_pause_and_save_bar(self):
        html = render("settings")
        self.assertIn("I&#39;m watching.", html)
        self.assertIn("Alerts have been on since Oct 6.", html)
        self.assertIn('value="pause"', html)
        self.assertIn("Save changes", html)
        self.assertIn("All changes saved.", html)
        self.assertNotIn("start watching", html)
        self.assertIn("This week", html)

    def test_paused_shows_resume(self):
        model = draft(alerts_started_at=None, alerts_paused_at=datetime(2026, 10, 10))
        html = render("settings", model=model)
        self.assertIn("Alerts are paused.", html)
        self.assertIn('value="resume"', html)
        self.assertIn("Resume alerts", html)

    def test_setup_mode_has_start_button_and_no_status_or_finish_later(self):
        html = render("setup")
        self.assertIn("Looks right, start watching", html)
        self.assertNotIn("finish later", html.lower())
        self.assertNotIn("Pause alerts", html)
        self.assertIn("If you&#39;d started last week", html)

    def test_setup_start_button_is_disabled_until_there_is_a_title(self):
        empty = render("setup", model=draft(titles=[], alerts_started_at=None, alerts_paused_at=None), has_titles=False)
        self.assertRegex(empty, r'id="save-button" disabled')
        self.assertIn("Add a title to continue.", empty)
        self.assertNotRegex(render("setup"), r'id="save-button" disabled')

    def test_unsaved_changes_show_the_compare_card_and_count(self):
        html = render("settings", compare=(12, 15), changes=2)
        self.assertIn("With your unsaved change", html)
        self.assertIn("2 changes not saved.", html)
        self.assertNotRegex(html, r'id="save-button" disabled')

    def test_errors_are_announced(self):
        html = render("setup", error=pf.NO_TITLE_ERROR)
        self.assertIn('role="alert"', html)
        self.assertIn(pf.NO_TITLE_ERROR, html)

    def test_resume_card_without_a_resume_offers_the_dropzone(self):
        html = render("settings")
        self.assertIn("Your resume", html)
        self.assertIn("No resume yet", html)
        self.assertIn('form="resume-form"', html)
        self.assertNotIn('href="/resume/file"', html)

    def test_resume_card_with_a_resume_offers_view_replace_delete(self):
        html = render("settings", resume={"filename": "cv.pdf", "size_bytes": 10,
                                          "uploaded_at": datetime(2026, 10, 19, tzinfo=timezone.utc)})
        self.assertIn("cv.pdf", html)
        self.assertIn('href="/resume/file"', html)
        self.assertIn("Replace", html)
        self.assertIn('form="resume-delete-form"', html)
        self.assertIn('action="/resume/delete"', html)
        self.assertNotIn("No resume yet", html)

    def test_resume_card_when_reading_is_unavailable(self):
        html = render("settings", reading=("unavailable", "Reading resumes isn't available right now."))
        self.assertIn("available right now", html)
        self.assertNotIn("No resume yet", html)

    def test_at_the_monthly_limit_a_file_can_be_viewed_and_deleted_but_not_replaced(self):
        html = render("settings", reading=("limit", "You've used this month's resume reads."),
                      resume={"filename": "cv.pdf", "size_bytes": 10, "uploaded_at": datetime(2026, 10, 19, tzinfo=timezone.utc)})
        self.assertIn('href="/resume/file"', html)
        self.assertIn('form="resume-delete-form"', html)
        self.assertNotIn("Replace", html)
        self.assertIn("used this month", html)

    def test_the_level_reason_is_shown_and_posted_back(self):
        model = draft(level="senior", level_note="Led design for 4 years.", alerts_started_at=None, alerts_paused_at=None)
        html = render("settings", model=model, level_label="Senior")
        self.assertIn("I picked this from your resume: Led design for 4 years.", html)
        self.assertIn('name="level_note" value="Led design for 4 years."', html)
        self.assertIn('name="level_note_level" value="senior"', html)

    def test_the_saved_profile_is_handed_to_the_page_script(self):
        model = draft(titles=["new one"], alerts_started_at=None, alerts_paused_at=None)
        html = render("settings", model=model, saved_snapshot=pf.snapshot_of(draft(titles=["old one"])), changes=1)
        self.assertIn('data-saved=\'{"city": "", "level": "Any level", "skills": "", "titles": "old one"', html)
        self.assertIn("1 change not saved.", html)

    def test_a_read_resume_message_and_notices(self):
        self.assertIn("I read your resume and filled in what I could", render("settings", resume_added=True))
        html = render("settings", notice={"text": "That doesn't look like a PDF.", "kind": "attention"})
        self.assertIn("fg-message--attention", html)
        self.assertIn('role="alert"', html)

    def test_the_snapshot_matches_what_the_page_script_reads(self):
        snap = pf.snapshot_of(draft(titles=["B title", "a title"], level="senior", one_level_up=True, city="Chicago",
                                    work_types=["remote", "hybrid"]))
        self.assertEqual(snap, {"titles": "a title|b title", "skills": "", "level": "Senior", "up": "1",
                                "city": "chicago", "work": "Hybrid|Remote"})

    def test_no_deal_breakers_card(self):
        self.assertNotIn("rather not hear", render("settings"))


if __name__ == "__main__":
    unittest.main()
