"""SC-62: the sign-in page says what is happening. The states themselves are driven by field-guide/js/signin.js and
checked in the browser by scripts/quality_check.py; these tests cover what the server renders and the rules that
must hold with no script at all."""
import re
import unittest
from unittest import mock

from dashboard import app as dapp


def fresh_client():
    return dapp.app.test_client()


class Page(unittest.TestCase):
    def setUp(self):
        self.html = fresh_client().get("/login").get_data(as_text=True)

    def test_the_button_and_label_say_what_will_happen(self):
        self.assertRegex(self.html, r'<label class="fg-label" for="email">Email</label>')
        button = re.search(r'<button class="signin-btn[^"]*"[^>]*id="signin-button">.*?</button>', self.html, re.S).group(0)
        self.assertIn("fg-button--primary", button)
        self.assertIn(">Email me a sign-in link</span>", button)
        self.assertNotIn("aria-busy", button)                      # at rest it is not working
        self.assertRegex(button, r'class="fg-spinner"[^>]*\bhidden\b')
        self.assertRegex(button, r'class="signin-check"[^>]*\bhidden\b')

    def test_the_status_area_is_always_there_as_a_polite_live_region_after_the_button(self):
        self.assertRegex(self.html, r'<div class="auth-status" role="status" aria-live="polite">')
        self.assertLess(self.html.index('id="signin-button"'), self.html.index('class="auth-status"'))

    def test_every_message_is_in_the_page_and_none_is_showing(self):
        for state in ("sent", "invalid", "throttled", "failed"):
            tag = re.search(rf'<div class="fg-message[^"]*" data-state="{state}"[^>]*>', self.html).group(0)
            self.assertIn("hidden", tag, state)
        self.assertIn("That email looks incomplete", self.html)
        self.assertIn("Too many tries for now", self.html)

    def test_the_form_carries_its_wording_and_limits_and_the_script_is_loaded(self):
        for attr in ('data-resend-seconds="30"', 'data-max-sends="5"', 'data-window-minutes="15"',
                     'data-label-ready="Email me a sign-in link"', 'data-label-sending="Sending…"',
                     'data-label-sent="Link sent"', 'data-label-resend="Send another link"'):
            self.assertIn(attr, self.html)
        self.assertIn('<script src="/field-guide/js/signin.js" defer></script>', self.html)

    def test_the_form_does_its_own_checking_so_the_message_can_be_ours(self):
        form = re.search(r'<form class="auth"[^>]*>', self.html).group(0)
        self.assertIn("novalidate", form)
        self.assertNotRegex(re.search(r'<input[^>]*id="email"[^>]*>', self.html).group(0), r"\brequired\b")

    def test_copy_is_sentence_case_and_never_says_whether_an_address_is_registered(self):
        self.assertIn("If you have access, a sign-in link is on its way to", self.html)
        for forbidden in ("not registered", "no account", "isn't registered", "unknown email", "we found"):
            self.assertNotIn(forbidden, self.html.lower())


class AfterAPlainPost(unittest.TestCase):
    """What a person with no script sees: the page itself says the link is on its way and offers another."""

    def post(self, email, registered=True, capped=False):
        user = {"id": 5} if registered else None
        with mock.patch.object(dapp, "get_conn", lambda: mock.MagicMock()), \
             mock.patch.object(dapp, "get_user_by_email", lambda c, e: user), \
             mock.patch.object(dapp, "too_many_recent_links", lambda c, uid: capped), \
             mock.patch.object(dapp, "create_magic_link", lambda c, uid: "tok"), \
             mock.patch.object(dapp, "send_magic_link_email", lambda e, url: None):
            return fresh_client().post("/login", data={"email": email})

    def test_the_sent_message_names_the_address_and_the_button_offers_another_link(self):
        html = self.post("pat@example.com").get_data(as_text=True)
        sent = re.search(r'<div class="fg-message fg-message--success" data-state="sent"[^>]*>(.*?)</div>', html, re.S)
        self.assertNotIn("hidden", re.search(r'<div class="fg-message fg-message--success" data-state="sent"[^>]*>', html).group(0))
        self.assertIn("Check your email", sent.group(1))
        self.assertIn("<strong data-email>pat@example.com</strong>", sent.group(1))
        button = re.search(r'<button class="signin-btn[^"]*"[^>]*id="signin-button">.*?</button>', html, re.S).group(0)
        self.assertIn("fg-button--secondary", button)
        self.assertIn(">Send another link</span>", button)

    def test_registered_unknown_and_capped_addresses_get_the_same_answer(self):
        # SC-33: the response must not reveal whether an address is registered or has been rate limited.
        a = self.post("pat@example.com").get_data(as_text=True)
        b = self.post("pat@example.com", registered=False).get_data(as_text=True)
        c = self.post("pat@example.com", capped=True).get_data(as_text=True)
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_a_hostile_address_cannot_inject_markup(self):
        evil = '"><script>alert(1)</script>'
        html = self.post(evil, registered=False).get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)


class LinkProblem(unittest.TestCase):
    def test_a_bad_sign_in_link_shows_the_reason_below_the_button(self):
        with mock.patch.object(dapp, "get_conn", lambda: mock.MagicMock()), \
             mock.patch.object(dapp, "verify_magic_link", lambda c, t: None):
            html = fresh_client().get("/verify?token=nope").get_data(as_text=True)
        box = re.search(r'<div class="fg-message fg-message--attention" data-state="error">(.*?)</div>', html, re.S)
        self.assertIsNotNone(box)
        self.assertIn("That link is invalid, expired, or already used.", box.group(1))
        self.assertLess(html.index('id="signin-button"'), html.index('data-state="error"'))


if __name__ == "__main__":
    unittest.main()
