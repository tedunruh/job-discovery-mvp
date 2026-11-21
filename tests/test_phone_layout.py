"""SC-75: open roles on a phone. The server draws the pieces the phone layout needs (the account menu, compact filter
pills with their short wording, the Viewed pill, the state line's hidden words); CSS decides what shows at 680px, and
scripts/quality_check.py measures that in a browser. These tests hold the markup and the copy."""
import re
import unittest

from dashboard import app as dapp
from tests.test_return_ask import Base


class Header(Base):
    def setUp(self):
        super().setUp()
        self.html = re.sub(r"<template.*?</template>", "", self.live(), flags=re.S)

    def test_a_wide_screen_keeps_both_buttons_and_the_phone_gets_one_account_menu(self):
        self.assertIn('<span class="header-wide"><a class="fg-button fg-button--ghost" href="/profile">Role preferences</a>', self.html)
        self.assertIn('<form method="post" action="/logout"><button class="fg-button fg-button--secondary" type="submit">Sign out</button></form></span>', self.html)
        self.assertIn('<details class="fg-filter fg-account" id="account-menu">', self.html)

    def test_the_account_button_is_named_and_says_it_opens_a_menu(self):
        self.assertIn('<summary class="fg-icon-button" aria-label="Account: role preferences and sign out" aria-haspopup="menu">', self.html)

    def test_the_menu_is_a_real_menu_with_two_rows(self):
        menu = re.search(r'<div class="fg-menu" role="menu"[^>]*>(.*?)</div>', self.html, re.S).group(1)
        self.assertEqual(re.findall(r'role="menuitem"', menu), ['role="menuitem"', 'role="menuitem"'])
        self.assertIn('<a class="fg-menu-item" role="menuitem" href="/profile">Role preferences</a>', menu)
        self.assertIn('<form role="none" method="post" action="/logout"><button class="fg-menu-item" role="menuitem" type="submit">Sign out</button></form>', menu)


class Filters(Base):
    def setUp(self):
        super().setUp()
        self.html = re.sub(r"<template.*?</template>", "", self.live(), flags=re.S)

    def test_the_filter_row_is_one_named_group(self):
        self.assertIn('<div class="toolbar toolbar--filters" role="group" aria-label="Filters">', self.html)

    def test_both_pills_are_compact_and_keep_their_label_for_a_screen_reader(self):
        for fid, label in (("filter-work", "Work type"), ("filter-posted", "Posted")):
            pill = re.search(r'<details class="fg-filter fg-filter--compact" id="%s">(.*?)</summary>' % fid, self.html, re.S).group(1)
            self.assertIn('<span class="fg-filter-label">%s<span class="fg-visually-hidden">: </span></span>' % label, pill)

    def test_the_posted_pill_says_this_week_on_a_phone_and_within_a_week_otherwise(self):
        self.assertIn('<span class="fg-filter-full">within a week</span><span class="fg-filter-short">This week</span>', self.html)

    def test_every_posted_range_has_a_short_wording(self):
        for key, short in dapp.RANGES.items():
            r = self.client.get("/?range=%s" % key, follow_redirects=True)
            html = r.get_data(as_text=True)
            self.assertRegex(html, r'<span class="fg-filter-short">[A-Z][^<]+</span>', key)

    def test_viewed_is_a_link_that_is_a_pill_on_a_phone_and_the_count_stays_for_the_script(self):
        self.assertIn('<a class="fg-link fg-filter-link" href="/?view=viewed" data-viewed-link>', self.html)
        self.assertIn('data-open-count', self.html)   # hidden by CSS on a phone, still the script's to update


class Card(Base):
    def test_discovered_on_is_there_for_a_screen_reader_and_the_source_is_the_visible_word(self):
        html = re.sub(r"<template.*?</template>", "", self.live(), flags=re.S)
        self.assertIn('<span class="fg-job-pre">Discovered on </span>Ashby', html)


if __name__ == "__main__":
    unittest.main()
