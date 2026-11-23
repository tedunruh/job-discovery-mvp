"""Automated UI standing checks (SC-32) - the mechanical ones from QUALITY_LOG.md.

    python scripts/quality_check.py                  # run all checks
    python scripts/quality_check.py --update-baseline  # accept current computed styles

Run before calling any UI change done. Exits non-zero on any failure.

What it checks, at phone / tablet / desktop sizes, on /login, /profile (set up and settings views), /start and /:
  1. viewport meta present, and a mobile-emulated page reports its true width
  2. no horizontal page overflow
  3. login route artwork (pins, dots) stays inside the viewport once animations end
  4. dashboard toolbar items (filter pills, role count) are single-line, items that
     share a row share a centerY, and the toolbar is padded >= 16px
  5. each dashboard filter menu, once opened, sits fully inside the viewport
  6. computed styles of the toolbar vs. a saved baseline - flags *changes* for
     review (sibling-regression catcher; not a failure, since edits are often intended)

It boots the real Flask app against the DB in .env.local with a throwaway user
(deleted on exit), and drives it with headless Chromium via Playwright
(`pip install playwright`; uses an already-installed Chromium if the pinned
build isn't present).
"""
import glob
import json
import re
import logging
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env.local"))

from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from dashboard import resume as resume_module  # noqa: E402
from dashboard import letter as letter_module  # noqa: E402
from dashboard import tailor  # noqa: E402
from dashboard.app import app  # noqa: E402
from db.db import create_session, get_conn  # noqa: E402

BASELINE_PATH = os.path.join(ROOT, "scripts", "quality_baseline.json")
TEST_EMAIL = "quality-check@example.invalid"
TEST_EMAIL_SETUP = "quality-check-setup@example.invalid"  # never onboarded: sees the profile setup view
# Narrowest supported phone is 360 (small Android). 320 (iPhone SE 1st gen) was dropped
# deliberately: the three toolbar items can't fit one line in its 288px of content.
VIEWPORTS = [(360, 740), (390, 844), (768, 1024), (800, 600), (1280, 800), (1440, 900)]
MOBILE_MAX_WIDTH = 768  # below this, emulate a touch phone (viewport meta must work)
ALIGN_TOLERANCE_PX = 1.0
MIN_SIDE_PADDING_PX = 16
STYLE_PROPS = ["font-size", "line-height", "font-weight", "color", "display", "padding", "margin"]
STYLE_SELECTORS = [".toolbar", ".fg-filter-pill", ".fg-filter-label", ".fg-filter-value", ".role-count"]
TOOLBAR_ITEMS = ".toolbar .fg-filter-pill, .toolbar .role-count"
PHONE_TOOLBAR_ITEMS = ".toolbar .fg-filter-pill, .toolbar .fg-filter-link"   # the count is gone on a phone; Viewed is a pill
PHONE_MAX_WIDTH = 680
PHONE_SIDE_PADDING_PX = 12   # the board's own padding on a phone
PHONE_FOLD_PX = 640          # content height of a 390pt phone with Safari's toolbar showing (SC-75)

logging.getLogger("werkzeug").setLevel(logging.ERROR)  # quiet the per-request access log

results = []  # (ok, label, detail)


def check(ok, label, detail=""):
    results.append((bool(ok), label, detail))


# ---- setup -----------------------------------------------------------------


def provision_user(email=TEST_EMAIL, onboarded=True):
    """Throwaway user (every user sees every company); onboarded unless told otherwise."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE email = %s", (email,))
    cur.execute(
        "INSERT INTO users (email, ntfy_topic, onboarded_at) VALUES (%s, %s, CASE WHEN %s THEN now() END) RETURNING id",
        (email, "quality-check-" + ("on" if onboarded else "setup"), onboarded),
    )
    user_id = cur.fetchone()[0]
    token = create_session(conn, user_id)
    conn.commit()
    conn.close()
    return user_id, token


def remove_user():
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE email = ANY(%s)", ([TEST_EMAIL, TEST_EMAIL_SETUP],))  # cascades sessions/events
    conn.commit()
    conn.close()


def find_chromium():
    """Prefer Playwright's own pinned build; fall back to any installed headless shell."""
    shells = sorted(glob.glob(os.path.expanduser(
        "~/Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell"
    )) + glob.glob(os.path.expanduser(
        "~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell"
    )))
    return shells[-1] if shells else None


# ---- checks ----------------------------------------------------------------


def settle(page):
    """Wait for run-once CSS animations to finish so rects are final."""
    page.evaluate("Promise.all(document.getAnimations().map(a => a.finished.catch(() => null)))")


def check_page_basics(page, path, w):
    tag = f"{path} @{w}"
    has_meta = page.evaluate("!!document.querySelector('meta[name=viewport]')")
    check(has_meta, f"viewport meta present  [{tag}]")
    over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    check(over <= 0, f"no horizontal overflow  [{tag}]", f"scrollWidth exceeds innerWidth by {over}px" if over > 0 else "")


def check_login_artwork(page, w, h):
    settle(page)
    bad = page.evaluate(
        """
        () => {
          const W = innerWidth, H = innerHeight, out = [];
          for (const el of document.querySelectorAll('.rt-dot, .rt-pin')) {
            const r = el.getBoundingClientRect();
            if (r.width === 0 && r.height === 0) continue;   // the other (hidden) layer
            if (r.left < -0.5 || r.top < -0.5 || r.right > W + 0.5 || r.bottom > H + 0.5)
              out.push(`${el.getAttribute('class')} [${r.left.toFixed(0)},${r.top.toFixed(0)} -> ${r.right.toFixed(0)},${r.bottom.toFixed(0)}]`);
          }
          return out;
        }
        """
    )
    check(not bad, f"login artwork inside viewport  [@{w}x{h}]", "; ".join(bad))


def check_toolbar(page, w):
    """Toolbar items may wrap onto more than one row at phone widths (two filter
    pills plus the count don't fit 328px), so alignment is checked per row: items
    whose boxes overlap vertically are on the same row and must share a centerY."""
    info = page.evaluate(
        """
        (sel) => {
          const items = [...document.querySelectorAll(sel)].map(e => {
            const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
            const lh = parseFloat(cs.lineHeight) || r.height;
            const inner = r.height - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom)
                          - parseFloat(cs.borderTopWidth) - parseFloat(cs.borderBottomWidth);
            return { s: e.className, top: r.top, bottom: r.bottom, left: r.left, w: r.width, cy: r.top + r.height / 2, lines: inner / lh };
          }).filter(i => i.w > 0);   // a hidden item (the count on a phone) is not in the row
          return { items, toolbarLeft: Math.min(...items.map(i => i.left)) };
        }
        """,
        PHONE_TOOLBAR_ITEMS if w <= PHONE_MAX_WIDTH else TOOLBAR_ITEMS,
    )
    items = info["items"]
    want = 3
    if len(items) < want:
        check(False, f"toolbar elements found  [@{w}]", f"expected 2 filter pills + {'Viewed' if w <= PHONE_MAX_WIDTH else 'role count'}, found {len(items)}")
        return
    rows = []
    for it in sorted(items, key=lambda i: i["top"]):
        row = next((r for r in rows if it["top"] < r[0]["bottom"] and it["bottom"] > r[0]["top"]), None)
        (row.append(it) if row is not None else rows.append([it]))
    worst = max((max(i["cy"] for i in r) - min(i["cy"] for i in r)) for r in rows)
    detail = " | ".join(", ".join(f"{i['s'].split()[0]}={i['cy']:.1f}" for i in r) for r in rows)
    check(worst <= ALIGN_TOLERANCE_PX, f"toolbar items in a row share a centerY  [@{w}]", f"spread {worst:.2f}px ({detail})")
    wrapped = [f"{i['s'].split()[0]} ({i['lines']:.1f} lines)" for i in items if i["lines"] > 1.5]
    check(not wrapped, f"toolbar text stays on one line  [@{w}]", "wrapped: " + ", ".join(wrapped))
    side = PHONE_SIDE_PADDING_PX if w <= PHONE_MAX_WIDTH else MIN_SIDE_PADDING_PX
    check(info["toolbarLeft"] >= side - 0.5, f"toolbar side padding >= {side}px  [@{w}]",
          f"left edge at {info['toolbarLeft']:.1f}px")


def check_filter_states(page, base):
    """Load the dashboard under every Work type combination and every Posted range
    and require a 200. The default view ("Any", 24 hours) rendered fine while any
    other Work type selection 500'd - and the selection is saved in the session,
    so one bad state locks the user out of the dashboard until it's reset."""
    from itertools import combinations
    from dashboard.app import RANGES
    from dashboard.filters import WORK_TYPES
    keys = list(WORK_TYPES)
    combos = [c for n in range(len(keys) + 1) for c in combinations(keys, n)]
    for combo in combos:
        qs = "work_set=1" + "".join(f"&work={k}" for k in combo)
        resp = page.goto(f"{base}/?{qs}", wait_until="load")
        check(resp.status == 200, f"dashboard renders with Work type {'+'.join(combo) or 'none'}", f"HTTP {resp.status}")
    for key in RANGES:
        resp = page.goto(f"{base}/?range={key}", wait_until="load")
        check(resp.status == 200, f"dashboard renders with Posted {key}", f"HTTP {resp.status}")
    page.goto(f"{base}/?work_set=1&range=24h", wait_until="load")  # leave the session at defaults


def check_ui_font(page, w):
    """The UI face (Albert Sans) is self-hosted. A bad path or
    route 404s silently and the UI drops to the browser's generic sans-serif, so
    confirm every weight the UI uses actually loads."""
    loaded = page.evaluate(
        """
        async () => {
          const out = {};
          for (const wt of [500, 600]) {
            try {  // a 404'd file rejects here rather than returning an error status
              const faces = await document.fonts.load(`${wt} 16px "Albert Sans"`);
              out[wt] = faces.length > 0 && faces.every(f => f.status === "loaded");
            } catch (e) { out[wt] = false; }
          }
          return out;
        }
        """
    )
    bad = [k for k, ok in loaded.items() if not ok]
    check(not bad, f"UI font (Albert Sans) loads  [@{w}]", "failed weights: " + ", ".join(bad))


def check_row_action(page, w):
    """Each job card keeps its state line and its one action (Apply) inside the card, at every width; on a phone the
    action sits under the state line, not beside the title."""
    rows = page.evaluate(
        """
        () => [...document.querySelectorAll('li.fg-job')].slice(0, 5).map(li => {
          const c = li.getBoundingClientRect(), a = li.querySelector('[data-action] .fg-button').getBoundingClientRect();
          const s = li.querySelector('[data-state]').getBoundingClientRect();
          return { inside: a.left >= c.left && a.right <= c.right && s.left >= c.left && s.right <= c.right,
                   below: a.top >= s.bottom - 1, h: a.height };
        })
        """
    )
    check(rows and all(r["inside"] for r in rows), f"Apply and the state line stay inside the job card  [@{w}]", str(rows[:1]))
    check(rows and all(r["h"] >= 44 - 0.5 for r in rows), f"Apply is at least 44px tall  [@{w}]", str(rows[:1]))
    if w <= 680:
        check(rows and all(r["below"] for r in rows), f"on a phone Apply sits under the state line  [@{w}]", str(rows[:1]))


ALLOWED_FONTS = {"fraunces", "albert sans"}


def check_typefaces(page, path):
    """Field Guide allows two typefaces: Fraunces and Albert Sans. Every rendered
    text element's first-choice family must be one of them - catches a stray monospace, a browser default on a <code>/<button>,
    or a new font slipped into one template."""
    bad = page.evaluate(
        """
        (allowed) => {
          const out = new Set();
          for (const el of document.querySelectorAll('body *:not(script):not(style):not(noscript):not(template)')) {
            if (!el.childNodes.length || ![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
            const cs = getComputedStyle(el);
            const first = cs.fontFamily.split(',')[0].trim().replace(/^["']|["']$/g, '').toLowerCase();
            if (!allowed.includes(first)) out.add(`${el.tagName.toLowerCase()}.${el.className || ''} -> ${first}`);
            // UI face (Albert Sans) is never lighter than Medium
            else if (first !== 'fraunces' && parseInt(cs.fontWeight, 10) < 500) out.add(`${el.tagName.toLowerCase()}.${el.className || ''} -> ${first} weight ${cs.fontWeight}`);
            // the serif (Fraunces, Scout's voice) is always regular: 400, never a weight it inherited (SC-68)
            else if (first === 'fraunces' && parseInt(cs.fontWeight, 10) !== 400) out.add(`${el.tagName.toLowerCase()}.${el.className || ''} -> fraunces weight ${cs.fontWeight}`);
          }
          return [...out].slice(0, 8);
        }
        """,
        sorted(ALLOWED_FONTS),
    )
    check(not bad, f"only Field Guide typefaces, UI text >= Medium, serif exactly Regular  [{path}]", "; ".join(bad))
    faces = page.evaluate("[...document.fonts].filter(f => f.family.replace(/[\"']/g, '') === 'Fraunces').map(f => f.weight)")
    check(all(w == "400" for w in faces), f"Fraunces is loaded at weight 400 only, so a stray weight cannot render  [{path}]", f"{faces}")


def check_gallery_complete(page):
    """Every class components.css defines must be shown in the /field-guide
    gallery. The gallery is the catalog of approved controls - a component added
    to the CSS but not the gallery is invisible to whoever builds the next screen."""
    import re
    css = open(os.path.join(ROOT, "field-guide", "css", "components.css")).read()
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)  # ignore classes named in comments
    classes = sorted(set(re.findall(r"\.(fg-[a-z0-9-]+)", css)))
    missing = page.evaluate("(cls) => cls.filter(c => !document.querySelector('.' + c))", classes)
    check(not missing, "gallery shows every Field Guide class", "missing: " + ", ".join(missing))


def check_leave_warning(ctx, base):
    """The profile screen warns before you walk away from unsaved edits, but never when the page itself
    navigates (pause, resume, save, upload, delete). Browsers show a native "Leave site?" dialog for the
    first and it reads as a bug for the second (SC-46: Replace resume asked "Leave site?")."""
    page = ctx.new_page()
    dialogs, stay = [], {"on": False}
    # Record every dialog. Accept it (so the page moves on) unless we are testing that walking away asks first.
    page.on("dialog", lambda d: (dialogs.append(d.type), d.dismiss() if stay["on"] and d.type == "beforeunload" else d.accept()))
    page.goto(base + "/profile", wait_until="load")
    page.fill("#city", "Chicago")  # an unsaved edit
    # The alerts switch flips in place (switch.js): no navigation, no dialog, the edit survives.
    sw = page.locator("#alerts")
    before = sw.get_attribute("aria-checked")
    url = page.url
    page.evaluate("window.__qcNoReload = true")  # a redirect back to /profile keeps the URL, so mark the page itself
    sw.click()
    try:
        page.wait_for_function("(b) => document.getElementById('alerts')?.getAttribute('aria-checked') !== b",
                               arg=before, timeout=20000)
    except Exception:
        pass  # reported below: the switch didn't flip in place
    page.wait_for_load_state("load")
    reloaded = page.url != url or not page.evaluate("window.__qcNoReload === true")
    check(dialogs == [] and not reloaded and page.locator("#alerts").get_attribute("aria-checked") != before, "flipping the alerts switch raises no 'Leave site?' dialog and stays put",
          f"{dialogs} {page.url}")
    if reloaded:  # the rest of this check can't run on a reloaded page
        page.close()
        return
    check(page.input_value("#city") == "Chicago", "...and the unsaved edit survives it", page.input_value("#city"))
    detail = page.text_content("#alerts-detail") or ""
    check(detail.startswith("Paused since" if before == "true" else "On since"),
          "...and the line under it says the new state in words", detail)
    sw.click()  # put it back
    page.wait_for_function("(b) => document.getElementById('alerts').getAttribute('aria-checked') === b",
                           arg=before, timeout=20000)
    stay["on"] = True
    page.click("a.fg-wordmark")  # walking away
    page.wait_for_timeout(300)
    check(dialogs == ["beforeunload"] and page.url.endswith("/profile"),
          "walking away from an unsaved edit still asks first", f"{dialogs} {page.url}")
    page.close()


def check_skills_minimum(ctx, base):
    """The skills minimum (SC-51 slice 4) is part of the profile like any other field: changing it counts as an
    unsaved change and enables Save; putting it back clears the count. profile.js compares a snapshot of the form
    with the saved one, so a field left out of that snapshot would silently never count."""
    page = ctx.new_page()
    page.goto(base + "/profile", wait_until="load")
    select = page.locator("#skill_cutoff")
    check(select.count() == 1, "the profile has a skills minimum", f"found {select.count()}")
    if select.count() != 1:
        page.close()
        return
    original = select.input_value()
    other = "3" if original != "3" else "2"
    select.select_option(other)
    page.wait_for_timeout(200)
    check((page.text_content(".fg-action-bar-note") or "").strip() == "1 change not saved." and page.is_enabled("#save-button"),
          "changing the skills minimum counts as an unsaved change and enables Save",
          (page.text_content(".fg-action-bar-note") or "").strip())
    select.select_option(original)
    page.wait_for_timeout(200)
    check((page.text_content(".fg-action-bar-note") or "").strip() == "" and not page.is_enabled("#save-button"),
          "...and putting it back clears the count", (page.text_content(".fg-action-bar-note") or "").strip())
    page.close()


def check_chip_scroll(browser, base, token):
    """Removing a chip hands focus back to the add box, and a plain focus() scrolls that box into view: with the
    box above the fold the page jumped away from the chips being removed ("scrolling seems off"). The page must
    stay where it is."""
    ctx = browser.new_context(viewport={"width": 1000, "height": 500})
    ctx.add_cookies([{"name": "scout_session", "value": token, "url": base}])
    page = ctx.new_page()
    page.goto(base + "/profile", wait_until="load")
    row = page.locator(".fg-add-row[data-chip-list]").first
    list_id = row.get_attribute("data-chip-list")
    box = row.locator("input[type=text]")
    for i in range(24):  # enough chips that the add box and the last chip cannot share a screen
        box.fill(f"Qc chip number {i}")
        box.press("Enter")
    page.wait_for_timeout(200)
    last = page.locator(f"#{list_id} .fg-chip-remove").last
    last.evaluate("el => el.scrollIntoView({block: 'center'})")
    page.wait_for_timeout(200)
    before = page.evaluate("window.scrollY")
    box_top = box.evaluate("el => el.getBoundingClientRect().top")
    last.click()
    page.wait_for_timeout(300)
    after = page.evaluate("window.scrollY")
    check(box_top < 0, "chip scroll check: the add box starts above the fold", f"box top {box_top}")
    check(abs(after - before) <= 1, "removing a chip leaves the page where it was",
          f"scrollY {before} before, {after} after")
    ctx.close()


def check_signin_states(browser, base):
    """The sign-in page says what is happening (SC-62). The wordmark, field and button never move; the status area
    sits below the button; sending shows a working button of the same width; a sent link is named; "send another"
    unlocks; a bad address, too many tries and a failed request each say so; and a phone has no overflow. The sign-in
    request is held and answered by the check itself, so no email is ever sent."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    held = []
    page.route("**/login", lambda route: held.append(route) if route.request.method == "POST" else route.continue_())
    page.goto(base + "/login", wait_until="load")

    def box(selector):
        return page.locator(selector).bounding_box()

    def visible_messages():
        return page.evaluate("[...document.querySelectorAll('.auth-status [data-state]')].filter(e => !e.hidden).map(e => e.dataset.state)")

    label = lambda: (page.locator("#signin-button .signin-label").inner_text() or "").strip()
    rest = {k: box(k) for k in (".wordmark", "#email", "#signin-button")}
    check(label() == "Email me a sign-in link" and not visible_messages(), "sign-in at rest says what the button does and shows no message", f"{label()!r} {visible_messages()}")

    # an incomplete address: our message, no request
    page.fill("#email", "pat@example")
    page.click("#signin-button")
    check(visible_messages() == ["invalid"] and not held, "an incomplete email is caught with a message and sends nothing", f"{visible_messages()} requests={len(held)}")

    # sending: busy, same width and colors, label and spinner
    page.fill("#email", "quality-check-signin@example.invalid")
    page.click("#signin-button")
    page.wait_for_function("document.getElementById('signin-button').getAttribute('aria-busy') === 'true'")
    busy = page.evaluate("""() => { const b = document.getElementById('signin-button'); const cs = getComputedStyle(b);
        return {label: b.querySelector('.signin-label').textContent, spinner: !b.querySelector('.fg-spinner').hidden,
                disabled: b.disabled, ariaDisabled: b.getAttribute('aria-disabled'), primary: b.classList.contains('fg-button--primary'),
                bg: cs.backgroundColor, width: b.getBoundingClientRect().width,
                spin: (() => { const sp = getComputedStyle(b.querySelector('.fg-spinner')); return {w: parseFloat(sp.width), anim: sp.animationName}; })()} }""")
    check(busy["label"] == "Sending…" and busy["spinner"] and busy["ariaDisabled"] == "true" and not busy["disabled"] and busy["primary"],
          "while sending the button is busy (spinner, 'Sending…', not disabled) and keeps its colors", str(busy))
    check(busy["spin"]["w"] > 0 and busy["spin"]["anim"] == "fg-spin", "...and the spinner is drawn and turning (the Field Guide style is there)", str(busy["spin"]))
    check(abs(busy["width"] - rest["#signin-button"]["width"]) < 1, "...and keeps its width", f"{busy['width']} vs {rest['#signin-button']['width']}")
    check(len(held) == 1, "...and exactly one request was made", f"requests={len(held)}")
    page.click("#signin-button", force=True)           # pressing it again while busy does nothing
    page.wait_for_timeout(150)
    check(len(held) == 1, "...and pressing it again while busy sends nothing more", f"requests={len(held)}")

    # sent: named address, button done, nothing above the message moved
    page.evaluate("document.getElementById('signin-form').setAttribute('data-resend-seconds', '1')")
    held.pop().fulfill(status=200, body="ok")
    page.wait_for_function("document.querySelector('.auth-status [data-state=sent]') && !document.querySelector('.auth-status [data-state=sent]').hidden")
    sent_box = box(".auth-status [data-state=sent]")
    check(label() == "Link sent" and page.locator("#signin-button").is_disabled(), "once sent the button says 'Link sent' and is done", label())
    check_box = box("#signin-button .signin-check")
    check(bool(check_box) and check_box["width"] > 0 and box("#signin-button .fg-spinner") is None, "...with a check mark showing and no spinner", str(check_box))
    check("quality-check-signin@example.invalid" in page.locator(".auth-status [data-state=sent]").inner_text()
          and page.locator(".auth-status [data-state=sent] .msg-title").inner_text() == "Check your email",
          "the confirmation says 'Check your email' and names the address")
    moved = {k: abs(box(k)["y"] - rest[k]["y"]) + abs(box(k)["x"] - rest[k]["x"]) for k in rest}
    check(max(moved.values()) < 1, "nothing above the message moved when it appeared", str(moved))
    check(sent_box["y"] >= box("#signin-button")["y"] + box("#signin-button")["height"], "the message sits below the button", f"{sent_box['y']} vs {box('#signin-button')}")
    page.wait_for_function("document.querySelector('#signin-button .signin-label').textContent === 'Send another link'", timeout=5000)
    check(page.locator("#signin-button").is_enabled() and "fg-button--secondary" in (page.locator("#signin-button").get_attribute("class") or ""),
          "after the wait 'Send another link' unlocks as a secondary button")

    # typing a new address starts again
    page.fill("#email", "someone-else@example.invalid")
    check(label() == "Email me a sign-in link" and not visible_messages(), "typing a different address starts again")

    # too many tries on this device: seeded memory, no request
    page.evaluate("""() => { const now = Date.now(); localStorage.setItem('scout.signin.sends', JSON.stringify({'busy@example.invalid': [now-1000, now-2000, now-3000, now-4000, now-5000]})); }""")
    page.fill("#email", "busy@example.invalid")
    page.click("#signin-button")
    check(visible_messages() == ["throttled"] and not held, "after too many tries the page says so and sends nothing", f"{visible_messages()} requests={len(held)}")

    # a failed request is said plainly and the button comes back
    page.fill("#email", "failing@example.invalid")
    page.click("#signin-button")
    page.wait_for_function("document.getElementById('signin-button').getAttribute('aria-busy') === 'true'")
    held.pop().fulfill(status=500, body="no")
    page.wait_for_function("[...document.querySelectorAll('.auth-status [data-state]')].some(e => !e.hidden && e.dataset.state === 'failed')")
    check(label() == "Email me a sign-in link" and page.locator("#signin-button").is_enabled(), "a failed request says so and the button is ready again")
    ctx.close()

    # a phone: the confirmation fits the screen
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.route("**/login", lambda route: route.fulfill(status=200, body="ok") if route.request.method == "POST" else route.continue_())
    page.goto(base + "/login", wait_until="load")
    page.fill("#email", "a-rather-long-address-for-a-phone@example.invalid")
    page.click("#signin-button")
    page.wait_for_function("document.querySelector('.auth-status [data-state=sent]') && !document.querySelector('.auth-status [data-state=sent]').hidden")
    geo = page.evaluate("""() => { const r = document.querySelector('.auth-status [data-state=sent]').getBoundingClientRect();
        return {left: r.left, right: r.right, bottom: r.bottom, vw: window.innerWidth, vh: window.innerHeight, sw: document.documentElement.scrollWidth} }""")
    check(geo["left"] >= 0 and geo["right"] <= geo["vw"] and geo["bottom"] <= geo["vh"] and geo["sw"] <= geo["vw"],
          "on a phone the confirmation fits the screen with no sideways scroll", str(geo))
    ctx.close()

    # no script at all: the plain post still works and the page says so
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, java_script_enabled=False)
    page = ctx.new_page()
    page.goto(base + "/login", wait_until="load")
    page.fill("#email", "nobody-qc-nojs@example.invalid")
    page.click("#signin-button")
    page.wait_for_load_state("load")
    text = page.locator(".auth-status").inner_text()
    check("Check your email" in text and "nobody-qc-nojs@example.invalid" in text and page.locator("#signin-button").inner_text().strip() == "Send another link",
          "with no script the plain form still works and the page says the link is on its way", text[:120])
    ctx.close()


def check_return_ask(browser, base, token):
    """Open roles and Applications (SC-63). Open a job in another tab, return, and the job's own row swaps Apply for
    "Did you apply?" with Yes and Not yet, without a reload and without taking focus; every job opened keeps asking
    until it is answered, also after a reload; Not yet puts Apply back for good (until the job is opened again), and
    the Viewed list shows the jobs opened, newest first, with the question still on the ones waiting. Yes sends
    the row out of Open roles at once, moves the counts and shows a toast with View and Undo that never takes focus,
    stays while pointed at, and goes by itself; Undo puts the row back; View lands on Applications with the job
    there. A phone fits all of it. The job's outside page is stubbed, so nothing leaves the machine."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_cookies([{"name": "scout_session", "value": token, "url": base}])
    ctx.route(re.compile(r"^https?://(?!127\.0\.0\.1)"), lambda route: route.fulfill(status=200, body="the job's own page"))
    page = ctx.new_page()
    page.goto(base + "/?range=all", wait_until="load")
    page.goto(base + "/?tab=applications", wait_until="load")
    check("No applications yet." in page.locator("[role=tabpanel]").inner_text()
          and "When you apply to a role from Open roles, it moves here." in page.locator("[role=tabpanel]").inner_text(),
          "Applications starts empty and says what will happen", page.locator("[role=tabpanel]").inner_text()[:120])
    check(page.locator(".toolbar .fg-filter-pill").count() == 0, "Applications has no Work type or Posted filters")
    page.goto(base + "/", wait_until="load")
    ids = page.evaluate("[...document.querySelectorAll('li.fg-job')].slice(0, 3).map(li => li.id.replace('posting-', ''))")
    check(len(ids) == 3, "the dashboard has jobs to open for the coming-back check", f"{ids}")
    if len(ids) < 3:
        ctx.close()
        return
    check(page.locator(".fg-tab[aria-selected=true]").inner_text().startswith("Open roles")
          and page.locator(".fg-tab").count() == 2 and page.locator("#filter-status").count() == 0,
          "Open roles is the selected tab, there are two, and the Status filter is gone")

    def open_in_new_tab(job):
        # What Apply does: open /open/<id> in a new tab (the title opens the panel now, SC-72). Going to the address
        # directly also works for a job that is already asking "Did you apply?", whose own Apply button is gone.
        tab = ctx.new_page()
        tab.goto(f"{base}/open/{job}", wait_until="load")
        tab.close()

    def come_back():
        page.bring_to_front()
        page.evaluate("window.dispatchEvent(new Event('focus'))")

    def asking():
        return page.evaluate("[...document.querySelectorAll('li.fg-job')].filter(li => li.querySelector('[data-ask-yes]')).map(li => li.id.replace('posting-', ''))")

    def counts():
        return page.evaluate("[...document.querySelectorAll('[data-tab-count]')].map(e => e.textContent.trim())")

    check(asking() == [], "nothing is asked before a job has been opened", f"{asking()}")
    open_in_new_tab(ids[0])
    focus_before = page.evaluate("document.activeElement.outerHTML.slice(0, 80)")   # after our own click on the link
    come_back()
    page.wait_for_selector(f"#posting-{ids[0]} [data-ask-yes]", timeout=8000)
    row = page.locator(f"#posting-{ids[0]}")
    check(asking() == [ids[0]] and "Did you apply?" in row.inner_text() and row.locator("button", has_text="Yes").is_visible()
          and row.locator("button", has_text="Not yet").is_visible(),
          "back from a job, its own row asks 'Did you apply?' with Yes and Not yet (no reload)", row.inner_text()[:140])
    check("Viewed" in row.locator("[data-state]").inner_text() and row.locator("[data-apply]").count() == 0,
          "...the state line says Viewed and the question has taken Apply's place", row.inner_text()[:140])
    check_typefaces(page, "dashboard, asking Did you apply?")
    focus_after = page.evaluate("document.activeElement.outerHTML.slice(0, 80)")
    check(focus_after == focus_before, "...without taking focus", f"{focus_before!r} -> {focus_after!r}")
    check(page.locator("#return-live").inner_text().startswith("Did you apply for"), "...and announces it to screen readers", page.locator("#return-live").inner_text())

    # a second job: both keep asking, until each is answered
    open_in_new_tab(ids[1])
    page.wait_for_timeout(2200)       # the script waits a moment between asks
    come_back()
    page.wait_for_selector(f"#posting-{ids[1]} [data-ask-yes]", timeout=8000)
    first = page.locator(f"#posting-{ids[0]}")
    check(sorted(asking()) == sorted([ids[0], ids[1]]) and first.locator("[data-apply]").count() == 0 and "Viewed" in first.locator("[data-state]").inner_text(),
          "after a second job the first keeps asking too, and both say Viewed", f"{asking()} | {first.inner_text()[:100]}")

    # a reload shows it with no script: the server draws it
    page.reload(wait_until="load")
    check(sorted(asking()) == sorted([ids[0], ids[1]]), "after a reload the question is still on both jobs", f"{asking()}")

    # the Viewed list: the way back to the jobs clicked on, newest first, the question still on them
    page.locator("[data-viewed-link]").click()
    page.wait_for_load_state("load")
    listed = page.evaluate("[...document.querySelectorAll('li.fg-job')].map(li => li.id.replace('posting-', ''))")
    check(page.url.endswith("view=viewed") and listed[:2] == [ids[1], ids[0]] and sorted(asking()) == sorted([ids[0], ids[1]]),
          "the Viewed list shows the jobs opened, last opened first, each still asking", f"{listed} | {asking()}")
    check("Opened" in page.locator(f"#posting-{ids[1]} .fg-job-state").inner_text() and page.locator("[data-viewed]").count() == 0
          and page.locator(".toolbar .fg-filter-pill").count() == 0,
          "...a row says when it was opened, and the filters that do not apply here are gone", page.locator(f"#posting-{ids[1]} .fg-job-state").inner_text())
    check_typefaces(page, "dashboard, Viewed list")
    page.locator("a", has_text="Back to open roles").click()
    page.wait_for_load_state("load")

    # Not yet: Apply comes back now and after a reload, the row still says Viewed
    page.locator(f"#posting-{ids[1]} [data-ask-dismiss] button").click()
    page.wait_for_timeout(500)
    second = page.locator(f"#posting-{ids[1]}")
    check(asking() == [ids[0]] and second.locator("[data-apply]").count() == 1 and "Viewed" in second.locator("[data-state]").inner_text(),
          "'Not yet' brings Apply back on that job (the other keeps asking) and the row still says Viewed", f"{asking()} | {second.inner_text()[:100]}")
    page.reload(wait_until="load")
    check(asking() == [ids[0]], "...and it stays closed after a reload (until that job is opened again)", f"{asking()}")

    # opening it again asks again
    page.wait_for_timeout(2200)
    open_in_new_tab(ids[1])
    come_back()
    page.wait_for_selector(f"#posting-{ids[1]} [data-ask-yes]", timeout=8000)
    check(sorted(asking()) == sorted([ids[0], ids[1]]), "opening the job again asks again", f"{asking()}")

    # Yes while the server is failing: nothing is claimed. The row stays, still asking, and there is no toast.
    page.route("**/set_applied/*", lambda route: route.fulfill(status=500, body="no"))
    page.locator(f"#posting-{ids[1]} [data-ask-yes] button").click()
    page.wait_for_timeout(1200)
    check(page.locator(f"#posting-{ids[1]}").is_visible() and ids[1] in asking() and page.locator(".fg-toast").count() == 0,
          "if the server fails, Yes claims nothing: the row stays, still asking, no toast", f"{asking()}")
    page.unroute("**/set_applied/*")

    # Yes: the row leaves at once, the counts move, a toast says where it went
    before = counts()
    page.locator(f"#posting-{ids[1]} [data-ask-yes] button").click()
    page.wait_for_selector(".fg-toast", timeout=8000)
    after = counts()
    check(not page.locator(f"#posting-{ids[1]}").is_visible(), "Yes sends the row out of Open roles at once")
    check(int(after[0]) == int(before[0]) - 1 and int(after[1]) == int(before[1]) + 1,
          "...and the counts move (Open roles down one, Applications up one)", f"{before} -> {after}")
    toast = page.locator(".fg-toast")
    check("You've applied to this role. You can find it in Applications." in toast.inner_text()
          and toast.locator("a", has_text="View").is_visible() and toast.locator("button", has_text="Undo").is_visible(),
          "...a toast says where it went, with View and Undo", toast.inner_text()[:120])
    check(page.evaluate("!document.querySelector('.fg-toast').contains(document.activeElement)"), "...and the toast does not take focus")
    check(page.evaluate("document.querySelector('#toast-region').getAttribute('aria-live')") == "polite", "...and is announced politely")
    check(page.evaluate("getComputedStyle(document.querySelector('.fg-toast-text')).textAlign") in ("start", "left"), "...and its text is left-aligned")

    # nobody points at it: it goes by itself after about eight seconds
    page.wait_for_timeout(7000)
    check(page.locator(".fg-toast").count() == 1, "the toast is still there at seven seconds")
    page.wait_for_function("document.querySelectorAll('.fg-toast').length === 0", timeout=4000)
    check(True, "...and goes by itself by about eight")

    page.reload(wait_until="load")
    check(page.locator(f"#posting-{ids[1]}").count() == 0, "after the toast has gone the job stays in Applications (not in Open roles)")

    # it stays while pointed at, then goes soon after the pointer leaves
    open_in_new_tab(ids[2])
    page.wait_for_timeout(2200)
    come_back()
    page.wait_for_selector(f"#posting-{ids[2]} [data-ask-yes]", timeout=8000)
    page.locator(f"#posting-{ids[2]} [data-ask-yes] button").click()
    page.wait_for_selector(".fg-toast", timeout=8000)
    page.hover(".fg-toast")
    page.wait_for_timeout(9000)
    check(page.locator(".fg-toast").count() == 1, "the toast stays past eight seconds while it is pointed at")
    page.mouse.move(5, 5)
    page.wait_for_timeout(4800)
    check(page.locator(".fg-toast").count() == 0, "...and goes by itself soon after the pointer leaves")

    # Undo: put it back. Then Yes again and View.
    open_in_new_tab(ids[0])
    page.wait_for_timeout(2200)
    come_back()
    page.wait_for_selector(f"#posting-{ids[0]} [data-ask-yes]", timeout=8000)
    before = counts()
    page.locator(f"#posting-{ids[0]} [data-ask-yes] button").click()
    page.wait_for_selector(".fg-toast", timeout=8000)
    page.locator(".fg-toast button", has_text="Undo").click()
    page.wait_for_function("(id) => { const r = document.getElementById('posting-' + id); return r && !r.hidden }", arg=ids[0], timeout=8000)
    check(page.locator(f"#posting-{ids[0]}").is_visible() and page.locator(f"#posting-{ids[0]} [data-apply]").count() == 1
          and counts() == before and page.locator(".fg-toast").count() == 0,
          "Undo puts the row back with Apply, restores the counts and clears the toast", f"{counts()} vs {before}")
    page.reload(wait_until="load")
    check(page.locator(f"#posting-{ids[0]}").count() == 1, "...and after a reload it is still in Open roles")

    open_in_new_tab(ids[0])
    page.wait_for_timeout(2200)
    come_back()
    page.wait_for_selector(f"#posting-{ids[0]} [data-ask-yes]", timeout=8000)
    page.locator(f"#posting-{ids[0]} [data-ask-yes] button").click()
    page.wait_for_selector(".fg-toast", timeout=8000)
    page.locator(".fg-toast a", has_text="View").click()
    page.wait_for_load_state("load")
    apps = page.locator("[role=tabpanel]")
    check(page.locator(".fg-tab[aria-selected=true]").inner_text().startswith("Applications")
          and page.locator(f"#posting-{ids[0]}").count() == 1 and "Applied" in page.locator(f"#posting-{ids[0]} .fg-job-state").inner_text(),
          "View lands on Applications with the job there, saying when", apps.inner_text()[:160])
    check(page.locator("li.fg-job button, li.fg-job form").count() == 0, "...and its rows have nothing to click but the job")
    first_title = page.locator("li.fg-job").first.get_attribute("id")
    check(first_title == f"posting-{ids[0]}", "...and the newest application is first", f"{first_title}")
    ctx.close()

    # a phone: the question and the toast fit
    mctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    mctx.add_cookies([{"name": "scout_session", "value": token, "url": base}])
    mctx.route(re.compile(r"^https?://(?!127\.0\.0\.1)"), lambda route: route.fulfill(status=200, body="the job's own page"))
    mp = mctx.new_page()
    mp.goto(base + "/", wait_until="load")
    other = mp.evaluate("[...document.querySelectorAll('li.fg-job')].map(li => li.id.replace('posting-', '')).find(id => id !== %s && id !== %s)" % (ids[0], ids[1]))
    with mctx.expect_page() as popup:
        mp.locator(f"#posting-{other} [data-apply]").click()   # Apply opens the posting in a new tab
    popup.value.wait_for_load_state("load")
    popup.value.close()
    mp.bring_to_front()
    mp.evaluate("window.dispatchEvent(new Event('focus'))")
    mp.wait_for_selector(f"#posting-{other} [data-ask-yes]", timeout=8000)
    geo = mp.evaluate("""(id) => { const r = document.querySelector('#posting-' + id + ' [data-action]').getBoundingClientRect();
        const c = document.querySelector('#posting-' + id).getBoundingClientRect();
        return {left: r.left, right: r.right, cl: c.left, cr: c.right, vw: window.innerWidth, sw: document.documentElement.scrollWidth} }""", other)
    check(geo["left"] >= geo["cl"] - 0.5 and geo["right"] <= geo["cr"] + 0.5 and geo["sw"] <= geo["vw"],
          "on a phone the question fits its card with no sideways scroll", str(geo))
    mp.locator(f"#posting-{other} [data-ask-yes] button").click()
    mp.wait_for_selector(".fg-toast", timeout=8000)
    geo = mp.evaluate("""() => { const r = document.querySelector('.fg-toast').getBoundingClientRect();
        return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, vw: window.innerWidth, vh: window.innerHeight, sw: document.documentElement.scrollWidth} }""")
    check(geo["left"] >= 0 and geo["right"] <= geo["vw"] and geo["bottom"] <= geo["vh"] and geo["top"] >= 0 and geo["sw"] <= geo["vw"],
          "on a phone the toast fits the screen", str(geo))
    mctx.close()


def check_hide(browser, base, token):
    """Hide a job (SC-59). Hide swaps Apply and Hide for "Why are you hiding?" with a reason menu and Skip; a reason or
    Skip hides the job (it leaves Open roles at once, the counts move, a toast says "Job hidden." with Undo, never
    View); Undo puts it back; the footer then offers "Show N hidden jobs", which opens "Jobs you hid" with when and
    why and an Unhide button; Unhide sends it back with its own toast and Undo; "Already applied" is not hiding (it
    goes to Applications); a failing server hides nothing; a phone fits it all."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_cookies([{"name": "scout_session", "value": token, "url": base}])
    page = ctx.new_page()
    page.goto(base + "/?range=all", wait_until="load")
    page.goto(base + "/", wait_until="load")
    ids = page.evaluate("[...document.querySelectorAll('li.fg-job')].slice(0, 4).map(li => li.id.replace('posting-', ''))")
    check(len(ids) == 4, "the dashboard has jobs to hide", f"{ids}")
    if len(ids) < 4:
        ctx.close()
        return
    a, b, c, d = ids

    def counts():
        return page.evaluate("[...document.querySelectorAll('[data-tab-count]')].map(e => e.textContent.trim())")

    def link():
        return page.evaluate("(() => { const l = document.querySelector('[data-hidden-link]'); return l && !l.hidden ? l.textContent.trim() : null })()")

    def row(i):
        return page.locator(f"#posting-{i}")

    check(link() is None, "there is no hidden-jobs link while nothing is hidden")
    check(row(a).locator("[data-apply]").count() == 1 and row(a).locator("[data-hide] button").count() == 1,
          "each card has Apply and Hide (Hide is the quieter one)", row(a).inner_text()[:120])
    hide_color = page.evaluate("(id) => getComputedStyle(document.querySelector('#posting-' + id + ' [data-hide] button')).color", a)
    forest = page.evaluate("getComputedStyle(document.querySelector('.fg-header-actions .fg-button--ghost')).color")   # a ghost button outside a card is Forest
    check(hide_color != forest, "Hide is in secondary text, not Forest (the colour of a plain ghost button)", f"{hide_color} vs {forest}")

    # Hide asks why: the menu and Skip replace Apply and Hide, and the menu has the focus the person just asked for
    start = counts()
    row(a).locator("[data-hide] button").click()
    page.wait_for_selector(f"#why-{a}")
    check(row(a).locator("[data-apply]").count() == 0 and row(a).locator("[data-hide]").count() == 0
          and "Why are you hiding?" in row(a).inner_text() and row(a).locator("button", has_text="Skip").is_visible(),
          "Hide asks 'Why are you hiding?' with a menu and Skip in place of Apply and Hide", row(a).inner_text()[:120])
    check(page.evaluate("document.activeElement.id") == f"why-{a}", "...and the menu has focus (they just asked for it)")
    check_typefaces(page, "dashboard, asking Why are you hiding?")
    options = page.evaluate("(id) => [...document.querySelectorAll('#why-' + id + ' option')].map(o => o.textContent.trim())", a)
    check(options == ["Select", "Not my kind of role", "Too junior", "Too senior", "Location or remote setup", "Pay",
                      "Not interested in this company", "Already applied", "Other"], "...with the agreed reasons in order", f"{options}")
    check(counts() == start and page.locator(".fg-toast").count() == 0, "...and nothing is hidden until they answer", f"{counts()}")

    # a failing server hides nothing
    page.route("**/hide/*", lambda route: route.fulfill(status=500, body="no"))
    page.select_option(f"#why-{a}", "junior")
    page.wait_for_timeout(1200)
    check(row(a).is_visible() and counts() == start and page.locator(".fg-toast").count() == 0,
          "if the server fails, a reason hides nothing: the card stays and there is no toast")
    page.unroute("**/hide/*")

    # a reason hides it; the toast has Undo and no View
    page.select_option(f"#why-{a}", "senior")
    page.wait_for_selector(".fg-toast", timeout=8000)
    after = counts()
    toast = page.locator(".fg-toast")
    check(not row(a).is_visible() and int(after[0]) == int(start[0]) - 1 and int(after[1]) == int(start[1]),
          "a reason hides the card at once and Open roles goes down by one", f"{start} -> {after}")
    check(toast.inner_text().startswith("Job hidden.") and toast.locator("button", has_text="Undo").is_visible() and toast.locator("a").count() == 0,
          "...the toast says 'Job hidden.' with Undo and no View", toast.inner_text()[:80])
    check(link() == "Show 1 hidden job", "...and the footer now offers 'Show 1 hidden job'", f"{link()}")
    check(page.evaluate("!document.querySelector('.fg-toast').contains(document.activeElement)"), "...and the toast does not take focus")

    # Undo puts it back exactly where it was
    toast.locator("button", has_text="Undo").click()
    page.wait_for_function("(id) => { const r = document.getElementById('posting-' + id); return r && !r.hidden }", arg=a, timeout=8000)
    position = page.evaluate("(id) => [...document.querySelectorAll('li.fg-job')].findIndex(li => li.id === 'posting-' + id)", a)
    check(row(a).is_visible() and position == 0 and row(a).locator("[data-apply]").count() == 1 and row(a).locator("[data-hide]").count() == 1
          and counts() == start and link() is None,
          "Undo puts the card back where it was, with Apply and Hide, and the counts and link go back", f"{counts()} {link()} at {position}")
    page.reload(wait_until="load")
    check(row(a).count() == 1 and link() is None, "...and after a reload it is still in Open roles")

    # Skip hides with no reason
    row(b).locator("[data-hide] button").click()
    page.wait_for_selector(f"#why-{b}")
    row(b).locator("button", has_text="Skip").click()
    page.wait_for_selector(".fg-toast", timeout=8000)
    check(not row(b).is_visible() and link() == "Show 1 hidden job", "Skip hides it too", f"{link()}")
    page.wait_for_function("document.querySelectorAll('.fg-toast').length === 0", timeout=12000)

    # a reason too: Too junior on another job, then reload and open the hidden list
    row(c).locator("[data-hide] button").click()
    page.wait_for_selector(f"#why-{c}")
    page.select_option(f"#why-{c}", "junior")
    page.wait_for_selector(".fg-toast", timeout=8000)
    page.reload(wait_until="load")
    check(row(b).count() == 0 and row(c).count() == 0 and link() == "Show 2 hidden jobs", "hidden jobs stay hidden after a reload", f"{link()}")
    page.locator("[data-hidden-link]").click()
    page.wait_for_load_state("load")
    check(page.locator("h2#hidden-title").inner_text() == "Jobs you hid" and page.evaluate("document.activeElement.id") == "hidden-title",
          "the link opens 'Jobs you hid' and the heading has focus")
    check(page.locator(".toolbar .fg-filter-pill").count() == 0 and "Back to open roles" in page.locator(".hidden-head").inner_text(),
          "...with no filters and a way back")
    first = page.locator("li.fg-job").first
    check(f"posting-{c}" == first.get_attribute("id") and "Hidden" in first.locator("[data-state-top]").inner_text()
          and "Too junior" in first.locator("[data-state-when]").inner_text() and first.locator("button", has_text="Unhide").is_visible(),
          "newest first, with when and why and an Unhide button", first.inner_text()[:140])
    second = page.locator(f"#posting-{b}")
    check(second.locator("[data-state-when]").count() == 0, "...and nothing after the time when they skipped the question")
    check(page.locator("li.fg-job [data-apply], li.fg-job [data-hide]").count() == 0, "...and no Apply or Hide in this list")

    # Unhide: leaves the list, toast with Undo, and the job is in Open roles
    before = counts()
    page.locator(f"#posting-{c} button", has_text="Unhide").click()
    page.wait_for_selector(".fg-toast", timeout=8000)
    check(not page.locator(f"#posting-{c}").is_visible() and page.locator(".fg-toast").inner_text().startswith("Back in Open roles.")
          and int(counts()[0]) == int(before[0]) + 1, "Unhide sends it out of the list, says 'Back in Open roles.' and moves the count", f"{before} -> {counts()}")
    page.locator(".fg-toast button", has_text="Undo").click()
    page.wait_for_function("(id) => { const r = document.getElementById('posting-' + id); return r && !r.hidden }", arg=c, timeout=8000)
    check(page.locator(f"#posting-{c}").is_visible() and counts() == before, "...and Undo puts it back in the hidden list", f"{counts()}")
    page.locator(f"#posting-{c} button", has_text="Unhide").click()
    page.wait_for_selector(".fg-toast", timeout=8000)
    page.goto(base + "/", wait_until="load")
    check(row(c).count() == 1 and row(b).count() == 0 and link() == "Show 1 hidden job", "after Unhide the job is in Open roles and the link counts one fewer", f"{link()}")
    page.locator("[data-hidden-link]").click()
    page.wait_for_load_state("load")
    page.locator(f"#posting-{b} button", has_text="Unhide").click()
    page.wait_for_selector(".fg-toast", timeout=8000)
    page.goto(base + "/", wait_until="load")
    check(link() is None and row(b).count() == 1, "with nothing left hidden the link goes away")

    # Already applied is not hiding
    start = counts()
    row(d).locator("[data-hide] button").click()
    page.wait_for_selector(f"#why-{d}")
    page.select_option(f"#why-{d}", "applied")
    page.wait_for_selector(".fg-toast", timeout=8000)
    after = counts()
    check("applied" in page.locator(".fg-toast").inner_text() and page.locator(".fg-toast a", has_text="View").is_visible()
          and int(after[1]) == int(start[1]) + 1 and link() is None,
          "'Already applied' is not hiding: the job goes to Applications with the applied toast", f"{start} -> {after} {link()}")
    ctx.close()

    # a phone: the question on its own line, the group full width with equal buttons, the toast fits
    mctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    mctx.add_cookies([{"name": "scout_session", "value": token, "url": base}])
    mp = mctx.new_page()
    mp.goto(base + "/", wait_until="load")
    pid = mp.evaluate("document.querySelector('li.fg-job').id.replace('posting-', '')")
    mp.locator(f"#posting-{pid} [data-hide] button").click()
    mp.wait_for_selector(f"#why-{pid}")
    geo = mp.evaluate("""(id) => {
        const li = document.querySelector('#posting-' + id), c = li.getBoundingClientRect();
        const q = li.querySelector('.fg-job-question').getBoundingClientRect();
        const g = li.querySelector('.fg-job-group').getBoundingClientRect();
        const sel = li.querySelector('.fg-job-select').getBoundingClientRect(), skip = li.querySelector('[data-skip]').getBoundingClientRect();
        const text = li.querySelector('.fg-job-title').getBoundingClientRect();   // the group lines up with the text, not the monogram
        return {q: q.bottom <= g.top + 1, fill: Math.abs(g.left - text.left) <= 1 && g.right >= c.right - 14 && g.right <= c.right,
                equal: Math.abs(sel.width - skip.width) <= 2, sw: document.documentElement.scrollWidth, vw: window.innerWidth} }""", pid)
    check(geo["q"] and geo["fill"] and geo["equal"] and geo["sw"] <= geo["vw"],
          "on a phone the question has its own line and the menu and Skip fill the text column equally, with no sideways scroll", str(geo))
    mp.select_option(f"#why-{pid}", "pay")
    mp.wait_for_selector(".fg-toast", timeout=8000)
    t = mp.evaluate("""() => { const r = document.querySelector('.fg-toast').getBoundingClientRect();
        return {l: r.left, r: r.right, b: r.bottom, vw: window.innerWidth, vh: window.innerHeight} }""")
    check(t["l"] >= 0 and t["r"] <= t["vw"] and t["b"] <= t["vh"], "on a phone the 'Job hidden.' toast fits the screen", str(t))
    mctx.close()


def check_elevated(browser, base, token):
    """Open roles with Scout's character (SC-69): a line in Scout's voice above the board, jobs grouped by day with a
    heading, a count and a hairline, a monogram on every card centred on the company and title lines (the same tint for a
    company), small-caps company, an 18px title, tabular pay, a card that folds closed when it leaves (a fade with reduced
    motion), and on a phone the monogram alone on the left with the state and action lined up with the text."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_cookies([{"name": "scout_session", "value": token, "url": base}])
    page = ctx.new_page()
    page.goto(base + "/?range=all", wait_until="load")
    page.goto(base + "/", wait_until="load")
    voice = page.locator("[data-voice]")
    text = voice.inner_text() if voice.count() else ""
    check(re.fullmatch(r"(\d+ new since yesterday\.|Nothing new since yesterday\.)( (One looks|\w+ look) close to what you’re after\.)?", text) is not None
          and page.evaluate("document.querySelector('[data-voice]').getBoundingClientRect().bottom <= document.querySelector('.board').getBoundingClientRect().top + 1"),
          "Scout's line sits above the board and says something true", text)
    heads = page.evaluate("[...document.querySelectorAll('.fg-day')].map(s => ({label: s.querySelector('.fg-day-label').firstChild.textContent.trim(), count: parseInt(s.querySelector('.fg-day-count').textContent.replace(/\\D+/g, ''), 10), shown: s.querySelectorAll('li.fg-job').length, rule: !!s.querySelector('.fg-day-rule')}))")
    order = ["Today", "Yesterday", "Earlier this week", "Last week", "Earlier"]
    check(heads and [h["label"] for h in heads] == [l for l in order if l in [h["label"] for h in heads]]
          and all(h["rule"] and h["count"] >= h["shown"] > 0 for h in heads),
          "jobs are grouped by day, in order, each with a count and a hairline", str(heads[:4]))
    cards = page.evaluate("""() => [...document.querySelectorAll('li.fg-job')].slice(0, 12).map(li => {
        const m = li.querySelector('.fg-mono'), mr = m.getBoundingClientRect();
        const c = li.querySelector('.fg-job-company').getBoundingClientRect(), t = li.querySelector('.fg-job-title').getBoundingClientRect();
        const mid = (c.top + t.bottom) / 2, cs = getComputedStyle(li.querySelector('.fg-job-company'));
        const title = getComputedStyle(li.querySelector('.fg-job-title')), pay = getComputedStyle(li.querySelector('.fg-job-pay'));
        return {company: li.querySelector('.fg-job-company').textContent.trim(), tint: m.className, hidden: m.getAttribute('aria-hidden'), w: Math.round(mr.width), h: Math.round(mr.height),
                off: Math.abs((mr.top + mr.bottom) / 2 - mid), caps: cs.fontVariantCaps, title: title.fontSize, tab: pay.fontVariantNumeric, text: m.textContent.trim().length} })""")
    check(cards and all(c["hidden"] == "true" and c["w"] == 40 and c["h"] == 40 and 1 <= c["text"] <= 3 for c in cards),
          "every card has a decorative 40px monogram with its initials", str(cards[:1]))
    check(cards and all(c["off"] <= 4 for c in cards), "the monogram is centred on the company and title lines", f"worst {max(c['off'] for c in cards):.1f}px")
    seen = {}
    for c in cards:
        seen.setdefault(c["company"], set()).add(c["tint"])
    check(all(len(v) == 1 for v in seen.values()), "a company has the same tint on every card", str({k: v for k, v in seen.items() if len(v) > 1}))
    check(cards and all(c["caps"] == "all-small-caps" and c["title"] == "18px" and "tabular-nums" in c["tab"] for c in cards),
          "type: company in small caps, 18px title, tabular numbers for pay", str(cards[:1]))
    fits = page.evaluate("""() => [...document.querySelectorAll('.fg-job-fit')].map(f => { const li = f.closest('li.fg-job'), t = li.querySelector('.fg-job-title').getBoundingClientRect(), r = f.getBoundingClientRect();
        return {pips: f.querySelectorAll('.fg-pip').length, label: f.textContent.trim(), left: Math.abs(r.left - t.left) <= 1} })""")
    check(all(f["pips"] == 5 and re.fullmatch(r"(Light|Okay|Good|Great|Strong) fit", f["label"]) and f["left"] for f in fits),
          f"a fit line, where there is one, is five pips and a label on the text column ({len(fits)} cards have one)", str(fits[:2]))

    # a card that leaves folds closed; the day's count follows
    first = page.locator("li.fg-job").first
    pid = first.get_attribute("id").replace("posting-", "")
    before = page.evaluate("(id) => { const s = document.getElementById('posting-' + id).closest('.fg-day'); return parseInt(s.querySelector('.fg-day-count').textContent.replace(/\\D+/g, ''), 10) }", pid)
    page.evaluate("""(id) => { window.__leaving = []; const li = document.getElementById('posting-' + id);
        new MutationObserver(() => { if (li.classList.contains('is-leaving')) window.__leaving.push(getComputedStyle(li).animationName) }).observe(li, {attributes: true}) }""", pid)
    first.locator("[data-hide] button").click()
    page.wait_for_selector(f"#why-{pid}")
    first.locator("button", has_text="Skip").click()
    page.wait_for_selector(".fg-toast", timeout=8000)
    seen_anim = page.evaluate("window.__leaving")
    after = page.evaluate("(id) => { const s = document.getElementById('posting-' + id).closest('.fg-day'); return parseInt(s.querySelector('.fg-day-count').textContent.replace(/\\D+/g, ''), 10) }", pid)
    check("fg-fold" in seen_anim, "a card that leaves folds closed", f"{seen_anim}")
    check(after == before - 1, "...and its day's count goes down by one", f"{before} -> {after}")
    page.locator(".fg-toast button", has_text="Undo").click()
    page.wait_for_function("(id) => { const r = document.getElementById('posting-' + id); return r && !r.hidden }", arg=pid, timeout=8000)
    back = page.evaluate("(id) => parseInt(document.getElementById('posting-' + id).closest('.fg-day').querySelector('.fg-day-count').textContent.replace(/\\D+/g, ''), 10)", pid)
    check(back == before, "...and Undo brings the card and the count back", f"{back} vs {before}")
    ctx.close()

    # reduced motion: only a fade
    rctx = browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
    rctx.add_cookies([{"name": "scout_session", "value": token, "url": base}])
    rp = rctx.new_page()
    rp.goto(base + "/", wait_until="load")
    pid = rp.locator("li.fg-job").first.get_attribute("id").replace("posting-", "")
    rp.evaluate("""(id) => { window.__leaving = []; const li = document.getElementById('posting-' + id);
        new MutationObserver(() => { if (li.classList.contains('is-leaving')) window.__leaving.push(getComputedStyle(li).animationName) }).observe(li, {attributes: true}) }""", pid)
    rp.locator(f"#posting-{pid} [data-hide] button").click()
    rp.wait_for_selector(f"#why-{pid}")
    rp.locator(f"#posting-{pid} button", has_text="Skip").click()
    rp.wait_for_selector(".fg-toast", timeout=8000)
    check("fg-fade" in rp.evaluate("window.__leaving") and "fg-fold" not in rp.evaluate("window.__leaving"),
          "with reduced motion the card only fades", f"{rp.evaluate('window.__leaving')}")
    check(rp.evaluate("getComputedStyle(document.querySelector('.fg-toast')).animationName") == "none", "...and the toast does not rise")
    rp.locator(".fg-toast button", has_text="Undo").click()
    rctx.close()

    # a phone: the monogram alone on the left, the state and the action lined up with the text
    mctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    mctx.add_cookies([{"name": "scout_session", "value": token, "url": base}])
    mp = mctx.new_page()
    mp.goto(base + "/", wait_until="load")
    geo = mp.evaluate("""() => { const li = document.querySelector('li.fg-job'); const m = li.querySelector('.fg-mono').getBoundingClientRect();
        const t = li.querySelector('.fg-job-title').getBoundingClientRect(), st = li.querySelector('[data-state-top]').getBoundingClientRect();
        const ac = li.querySelector('[data-action] .fg-button').getBoundingClientRect(), c = li.getBoundingClientRect(), pay = li.querySelector('.fg-job-subline').getBoundingClientRect();
        return {mono: Math.round(m.width), monoLeft: m.left >= c.left, textAfterMono: t.left >= m.right, stateAligned: Math.abs(st.left - t.left) <= 1,
                actionAligned: Math.abs(ac.left - t.left) <= 1, payAligned: Math.abs(pay.left - t.left) <= 1, sw: document.documentElement.scrollWidth, vw: innerWidth} }""")
    check(geo["mono"] == 36 and geo["textAfterMono"] and geo["stateAligned"] and geo["actionAligned"] and geo["payAligned"] and geo["sw"] <= geo["vw"],
          "on a phone the monogram sits alone on the left and the state, action and pay line up with the text", str(geo))
    mctx.close()


def check_pay(browser, base, token):
    """The pay line on a job card (SC-71): a range like "$150,000 - $188,000" ("/ hour" or "/ month" when it is not a year),
    never a dash, in tabular numbers, on the card's text column; a card with no pay keeps the line as blank space, so
    every card in a list is the same height."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_cookies([{"name": "scout_session", "value": token, "url": base}])
    page = ctx.new_page()
    page.goto(base + "/?range=all", wait_until="load")
    page.goto(base + "/", wait_until="load")
    cards = page.evaluate("""() => [...document.querySelectorAll('li.fg-job')].map(li => {
        const p = li.querySelector('.fg-job-pay'), r = li.getBoundingClientRect(), t = li.querySelector('.fg-job-title').getBoundingClientRect(), pr = p.getBoundingClientRect();
        const sub = li.querySelector('.fg-job-subline').getBoundingClientRect();
        return {text: p.textContent.trim(), h: Math.round(r.height), extra: Math.round(t.height) + Math.round(sub.height), fit: !!li.querySelector('.fg-job-fit'), left: Math.abs(pr.left - t.left) <= 1, tab: getComputedStyle(p).fontVariantNumeric.includes('tabular-nums'), lines: Math.round(pr.height) } })""")
    with_pay = [c for c in cards if c["text"]]
    check(all(re.fullmatch(r"(Up to )?[^\s\d]*\s?[\d,.]+(?: - [^\s\d]*\s?[\d,.]+|\+)?(?: / (?:hour|month|week|day))?", c["text"]) for c in with_pay),
          f"every pay line reads as a range, never a dash or a stray word ({len(with_pay)} of {len(cards)} cards have one)", str([c["text"] for c in with_pay][:4]))
    check(all(c["left"] and c["tab"] for c in cards), "pay lines line up with the title and use tabular numbers")
    check(all(c["lines"] >= 14 for c in cards), "a card with no pay keeps the line as blank space (the line is never collapsed)")
    # cards with and without pay, in the same list, are the same height once the lines a long title or place wrapped onto
    # are taken out (the title and place lines are the only ones whose length depends on the job). A card with a fit
    # line is one line taller, so cards are compared with others that have (or lack) one.
    for with_fit in (False, True):
        heights = {c["h"] - c["extra"] for c in cards if c["lines"] and c["fit"] == with_fit}
        if heights:
            check(max(heights) - min(heights) <= 2, f"cards with and without pay are the same height (a wrapped title or place aside; {'with' if with_fit else 'without'} a fit line)", str(sorted(heights)))
    ctx.close()


def check_alerts_switch_no_js(browser, base, token):
    """Without JavaScript the alerts switch is still a working form button: it posts and the page
    reloads with the switch flipped and its date line updated (no notice banner)."""
    ctx = browser.new_context(java_script_enabled=False)
    ctx.add_cookies([{"name": "scout_session", "value": token, "url": base}])
    page = ctx.new_page()
    page.goto(base + "/profile", wait_until="load")
    before = page.get_attribute("#alerts", "aria-checked")
    with page.expect_navigation():
        page.click("#alerts")
    page.wait_for_load_state("load")
    after = page.get_attribute("#alerts", "aria-checked", timeout=5000) if page.locator("#alerts").count() else None
    detail = (page.text_content("#alerts-detail") or "") if page.locator("#alerts-detail").count() else ""
    check(after is not None and after != before and detail.startswith("Paused since" if before == "true" else "On since"),
          "alerts switch works without JavaScript (posts, flips, the line under it updates)", f"{before}->{after} '{detail}'")
    if after is not None and after != before:
        with page.expect_navigation():
            page.click("#alerts")  # put it back
    ctx.close()


HEADER_INFO_JS = """() => {
  const h = document.querySelector('header.fg-header');
  if (!h) return null;
  const pick = (el, keys) => { const cs = getComputedStyle(el); return Object.fromEntries(keys.map(k => [k, cs[k]])); };
  const wm = h.querySelector('.fg-wordmark');
  // what is shown: the two buttons, or on a phone the dashboard's one account button (the other is not drawn)
  const actions = [...h.querySelectorAll('.fg-header-actions .fg-button, .fg-header-actions > .fg-account > .fg-icon-button')].filter(b => b.getBoundingClientRect().width > 0).map(b => ({
    text: (b.textContent.trim() || b.getAttribute('aria-label')), cls: b.className, rect: b.getBoundingClientRect().toJSON(),
    style: pick(b, ['fontFamily', 'fontSize', 'fontWeight', 'color', 'paddingLeft', 'paddingRight', 'borderRadius', 'height']),
  }));
  return { wordmark: wm && pick(wm, ['fontFamily', 'fontSize', 'fontWeight', 'letterSpacing', 'color']),
           wordmarkTop: wm && wm.getBoundingClientRect().top, actions, vw: window.innerWidth,
           headline: (() => { const h1 = document.querySelector('h1.fg-display--page'); return h1 && getComputedStyle(h1).fontSize; })() };
}"""


def header_info(page, path, w):
    """The page header is one component everywhere (Ted, Oct 22): the 36px wordmark, then one or two
    actions at the end of the row, each a 44px Field Guide button inside the viewport."""
    info = page.evaluate(HEADER_INFO_JS)
    check(info is not None and info["wordmark"] is not None, f"page uses the Field Guide header  [{path} @{w}]")
    if not info or not info["wordmark"]:
        return None
    size = "24px" if w <= PHONE_MAX_WIDTH else "36px"   # a phone gives the header less room (SC-75)
    check(info["wordmark"]["fontSize"] == size, f"wordmark is {size}  [{path} @{w}]", info["wordmark"]["fontSize"])
    if info["headline"]:
        check(info["headline"] == "28px", f"page headline (h1) is 28px, under the wordmark  [{path} @{w}]", info["headline"])
    n = len(info["actions"])
    check(1 <= n <= 2, f"header has one or two actions  [{path} @{w}]", f"{n}: {[a['text'] for a in info['actions']]}")
    for a in info["actions"]:
        r = a["rect"]
        check(r["height"] >= 44 and r["left"] >= 0 and r["right"] <= info["vw"] + 0.5,
              f"header action '{a['text']}' is 44px and on screen  [{path} @{w}]", f"{r}")
    return info


def check_headers_match(infos):
    """Dashboard and settings headers share the wordmark style and the same 'go somewhere' button
    (Role preferences / Back to dashboard), at every width. They were two hand-built headers once."""
    for w in sorted({w for (_, w) in infos}):
        a, b = infos.get(("/", w)), infos.get(("/profile", w))
        if not a or not b or not a["actions"] or not b["actions"]:
            check(False, f"dashboard and settings headers both measured  [@{w}]")
            continue
        check(a["wordmark"] == b["wordmark"], f"dashboard and settings wordmarks match  [@{w}]",
              f"{a['wordmark']} vs {b['wordmark']}")
        check(abs(a["wordmarkTop"] - b["wordmarkTop"]) <= 1, f"the wordmark sits at the same height on both pages  [@{w}]",
              f"{a['wordmarkTop']:.1f} vs {b['wordmarkTop']:.1f}")
        if w <= PHONE_MAX_WIDTH:   # the dashboard has one account button instead of the two (SC-75); settings keeps its button
            continue
        na, nb = a["actions"][0], b["actions"][0]
        check(na["cls"] == nb["cls"] and na["style"] == nb["style"],
              f"'{na['text']}' and '{nb['text']}' are the same button  [@{w}]",
              f"{na['cls']} {na['style']} vs {nb['cls']} {nb['style']}")


def check_back_link(ctx, base):
    """Settings has a visible way back to the dashboard, and it works (SC-46: "how do I go to the dashboard?")."""
    page = ctx.new_page()
    page.goto(base + "/profile", wait_until="load")
    link = page.get_by_role("link", name="Back to dashboard")
    check(link.count() == 1 and link.is_visible(), "settings shows a 'Back to dashboard' link")
    if link.count() == 1:
        link.click()
        page.wait_for_load_state("load")
        check(page.url.rstrip("/") == base, "...and it goes to the dashboard", page.url)
    page.close()


# Every control a finger has to hit is at least this tall (Ted, SC-46). Filter
# pills follow the dashboard Figma (36px) and a chip's remove button sits inside
# its 44px chip, so they're not listed here.
CONTROL_SELECTORS = (".fg-button, .fg-input, .fg-select select, .fg-check, .fg-segment, "
                     ".fg-chip, .fg-chip-add, .fg-toggle, .fg-switch, .fg-icon-button, .fg-menu-item, button.fg-link")


# Every action that has an outline uses the one subtle action outline (Ted, Nov 1): secondary and
# suggested buttons and menu buttons (selects, filter pills). Text fields are not actions and keep
# their stronger border.
OUTLINED_ACTIONS = ".fg-button--secondary, .fg-button--suggested, .fg-select select, .fg-filter-pill"


def check_action_outlines(page, where):
    found = page.evaluate("""(sel) => {
      const probe = document.createElement('span');
      probe.style.color = 'var(--fg-color-action-outline)';
      document.body.appendChild(probe);
      const want = getComputedStyle(probe).color;
      probe.remove();
      const seen = {};
      for (const el of document.querySelectorAll(sel)) {
        if (el.disabled || el.closest(':disabled') || !el.getClientRects().length) continue;
        const cs = getComputedStyle(el);
        const kind = el.matches('.fg-filter-pill') ? 'filter pill' : el.tagName === 'SELECT' ? 'select'
          : el.matches('.fg-button--suggested') ? 'suggested button' : 'secondary button';
        if (cs.borderTopWidth === '0px' || cs.borderTopStyle === 'none') continue;
        (seen[kind] = seen[kind] || new Set()).add(cs.borderTopColor);
      }
      return { want, seen: Object.fromEntries(Object.entries(seen).map(([k, v]) => [k, [...v]])) };
    }""", OUTLINED_ACTIONS)
    for kind, colors in sorted(found["seen"].items()):
        check(colors == [found["want"]], f"{kind} outline is the subtle action outline  [{where}]",
              f"{colors} vs {found['want']}")
    return found["seen"]


def check_control_heights(page):
    """Controls in the gallery are all >= the touch target (44px)."""
    short = page.evaluate(
        """
        (sel) => [...document.querySelectorAll(sel)]
          .filter(e => e.getClientRects().length)
          .map(e => [e.className || e.tagName, Math.round(e.getBoundingClientRect().height)])
          .filter(([, h]) => h < 44)
          .map(([c, h]) => `${c} ${h}px`)
        """,
        CONTROL_SELECTORS,
    )
    check(not short, "every control is at least 44px tall", "; ".join(sorted(set(short))[:8]))


def check_filter_menus(page, w):
    """Open each filter menu in turn; it must land fully inside the viewport (a
    menu anchored to a pill near the right edge can hang off a phone screen).
    Measured against the requested width w, not innerWidth: on a mobile page an
    overflowing menu widens the layout viewport, so innerWidth grows to fit it
    and the menu would always look "inside"."""
    for fid in page.evaluate("[...document.querySelectorAll('details.fg-filter:not(.fg-account)')].map(d => d.id)"):
        page.click(f"#{fid} > summary")
        r = page.evaluate(
            f"(() => {{ const m = document.querySelector('#{fid} .fg-menu').getBoundingClientRect();"
            f" return {{l: m.left, r: m.right, t: m.top, b: m.bottom, W: innerWidth, open: document.querySelector('#{fid}').open}}; }})()"
        )
        check(r["open"], f"filter menu opens  [#{fid} @{w}]")
        inside = r["l"] >= -0.5 and r["r"] <= w + 0.5 and r["t"] >= -0.5
        check(inside, f"filter menu inside viewport  [#{fid} @{w}]",
              f"menu [{r['l']:.0f},{r['t']:.0f} -> {r['r']:.0f},{r['b']:.0f}] vs width {w}")
        page.keyboard.press("Escape")
        closed = page.evaluate(f"!document.querySelector('#{fid}').open")
        check(closed, f"Escape closes filter menu  [#{fid} @{w}]")


def check_phone(browser, base, token):
    """Open roles on a phone (SC-75): more jobs above the fold. Under 680px the whole page scrolls (not a box inside it),
    the tabs pin to the top, the header is one 24px wordmark and one account menu, Scout's line is 16px, the filters sit in
    one row that scrolls sideways (Viewed is a pill, the count is gone), the board runs edge to edge, and a card's place and
    pay, and its source, time and Viewed, are one line each. On a 390pt phone with Safari's toolbar showing (PHONE_FOLD_PX of
    content) the first card, with its buttons, and part of the second show without scrolling. Wider screens do not change."""
    w, h = 390, 844
    tag = f"@{w}"
    ctx = browser.new_context(viewport={"width": w, "height": h}, is_mobile=True, has_touch=True)
    ctx.add_cookies([{"name": "scout_session", "value": token, "url": base}])
    page = ctx.new_page()
    page.goto(base + "/", wait_until="load")
    rows = page.evaluate("document.querySelectorAll('li.fg-job').length")
    check(rows > 0, f"the dashboard has jobs to lay out on a phone  [{tag}]")
    m = page.evaluate(
        """
        (fold) => {
          const cs = (sel) => { const e = document.querySelector(sel); return e ? getComputedStyle(e) : null; };
          const rect = (e) => e.getBoundingClientRect();
          const out = {};
          out.wordmark = cs('.fg-wordmark').fontSize;
          out.band = rect(document.querySelector('.band')).height;
          out.headerPad = [cs('body > .fg-header').paddingTop, cs('body > .fg-header').paddingLeft];
          out.wide = [...document.querySelectorAll('.header-wide')].map(e => getComputedStyle(e).display);
          const acct = document.querySelector('#account-menu > summary');
          const ar = rect(acct);
          out.account = { w: ar.width, h: ar.height, label: acct.getAttribute('aria-label'), popup: acct.getAttribute('aria-haspopup') };
          const voice = document.querySelector('.voice-intro');
          out.voice = voice ? { size: getComputedStyle(voice).fontSize, margin: getComputedStyle(voice).marginTop } : null;
          const b = getComputedStyle(document.querySelector('.board')), br = rect(document.querySelector('.board'));
          out.board = { l: br.left, r: br.right, bw: [b.borderTopWidth, b.borderRightWidth, b.borderBottomWidth, b.borderLeftWidth], radius: b.borderTopLeftRadius, bottomRadius: b.borderBottomLeftRadius, overflow: b.overflowY };
          out.tabs = getComputedStyle(document.querySelector('.board > .fg-tabs')).position;
          out.list = getComputedStyle(document.querySelector('.board > [role=tabpanel]')).overflowY;
          out.bodyHeight = document.body.getBoundingClientRect().height;
          const tb = document.querySelector('.toolbar--filters'), tr = rect(tb), tcs = getComputedStyle(tb);
          out.toolbar = { h: tr.height, overflowX: tcs.overflowX, wrap: tcs.flexWrap, l: tr.left, r: tr.right };
          const pills = [...tb.querySelectorAll('.fg-filter-pill, .fg-filter-link')].filter(e => rect(e).width > 0);
          const shown = (e) => { const l = e.querySelector('.fg-filter-label'); return !l || l.getBoundingClientRect().width <= 1; };
          out.pills = pills.map(e => ({ text: (e.querySelector('.fg-filter-value') || e).innerText.trim(), labelHidden: shown(e), name: e.textContent.replace(/\s+/g, ' ').trim(), top: rect(e).top, radius: getComputedStyle(e).borderTopLeftRadius, border: getComputedStyle(e).borderTopWidth }));
          out.count = cs('[data-open-count]').display;
          const cards = [...document.querySelectorAll('li.fg-job')].slice(0, 2).map(rect);
          out.cards = cards.map(r => ({ top: r.top, bottom: r.bottom }));
          out.fold = fold;
          const c = document.querySelector('li.fg-job');
          const place = c.querySelector('.fg-job-subline'), pay = c.querySelector('.fg-job-pay');
          out.place = rect(place).top; out.pay = getComputedStyle(pay).display === 'none' ? null : rect(pay).top;
          const where = place.parentElement, gap = parseFloat(getComputedStyle(where).columnGap) || 0;
          out.fits = rect(place).width + rect(pay).width + gap <= where.clientWidth + 1;   // false: too long to share a line, so the pay drops by design
          const top = c.querySelector('[data-state-top]'), when = c.querySelector('[data-state-when]');
          out.state = { top: rect(top).top, when: when ? rect(when).top : null, text: top.innerText.trim(), pre: rect(c.querySelector('.fg-job-pre')).width,
                        full: top.textContent.trim() };
          out.scrolls = document.documentElement.scrollHeight - innerHeight;
          return out;
        }
        """,
        PHONE_FOLD_PX,
    )
    check(m["wordmark"] == "24px", f"the wordmark is 24px  [{tag}]", m["wordmark"])
    check(m["headerPad"] == ["8px", "16px"], f"the header pads 8px above and 16px at the sides  [{tag}]", str(m["headerPad"]))
    check(abs(m["band"] - 150) <= 1, f"the Sage band is about 150px  [{tag}]", f"{m['band']}px")
    check(m["wide"] and all(d == "none" for d in m["wide"]), f"the two header buttons give way to the account menu  [{tag}]", str(m["wide"]))
    check(m["account"]["w"] >= 44 and m["account"]["h"] >= 44, f"the account button is at least 44px square  [{tag}]", str(m["account"]))
    check(m["account"]["label"] == "Account: role preferences and sign out" and m["account"]["popup"] == "menu",
          f"the account button has its name and says it opens a menu  [{tag}]", str(m["account"]))
    if m["voice"]:
        check(m["voice"]["size"] == "16px" and m["voice"]["margin"] == "0px", f"Scout's line is 16px with no margin  [{tag}]", str(m["voice"]))
    check(abs(m["board"]["l"]) <= 0.5 and abs(m["board"]["r"] - w) <= 0.5, f"the board runs edge to edge  [{tag}]", str(m["board"]))
    check(m["board"]["bw"] == ["1px", "0px", "0px", "0px"], f"the board has a top border only  [{tag}]", str(m["board"]["bw"]))
    check(m["board"]["radius"] != "0px" and m["board"]["bottomRadius"] == "0px", f"the board's top corners are rounded and its bottom square  [{tag}]", str(m["board"]))
    check(m["board"]["overflow"] == "visible" and m["list"] == "visible", f"no scroll box on the board or its list  [{tag}]", f"{m['board']['overflow']} / {m['list']}")
    check(m["tabs"] == "sticky", f"the tabs are sticky  [{tag}]", m["tabs"])
    check(m["toolbar"]["wrap"] == "nowrap" and m["toolbar"]["overflowX"] == "auto", f"the filters are one row that scrolls sideways  [{tag}]", str(m["toolbar"]))
    check(abs(m["toolbar"]["l"]) <= 0.5 and abs(m["toolbar"]["r"] - w) <= 0.5, f"the filter row bleeds to the board's edges  [{tag}]", str(m["toolbar"]))
    names = [p["name"] for p in m["pills"]]
    check(len(m["pills"]) == 3 and len({round(p["top"]) for p in m["pills"]}) == 1, f"Work type, Posted and Viewed sit in one row  [{tag}]", str(m["pills"]))
    check(names[0].startswith("Work type:") and names[1].startswith("Posted:"), f"the pills keep their labels for a screen reader  [{tag}]", str(names))
    check(all(p["labelHidden"] for p in m["pills"]), f"...and show only the value  [{tag}]", str([(p["text"], p["labelHidden"]) for p in m["pills"]]))
    check(m["pills"][1]["text"] in ("Last 24 hours", "This week", "This month", "Any time"), f"Posted says the short wording ('This week')  [{tag}]", m["pills"][1]["text"])
    check(m["pills"][2]["text"] == "Viewed" and m["pills"][2]["radius"] != "0px" and m["pills"][2]["border"] == "1px", f"Viewed is a pill  [{tag}]", str(m["pills"][2]))
    check(m["count"] == "none", f"the open-roles count line is gone  [{tag}]", m["count"])
    check(m["place"] is not None, f"the card shows its place  [{tag}]")
    if m["pay"] is not None and m["fits"]:   # the pay drops to its own line only when the two don't fit; that depends on the first card's wording
        check(abs(m["place"] - m["pay"]) <= 2, f"place and pay are one line  [{tag}]", f"place at {m['place']:.0f}, pay at {m['pay']:.0f}")
    check(m["state"]["when"] is None or abs(m["state"]["top"] - m["state"]["when"]) <= 2, f"source and time are one line  [{tag}]", str(m["state"]))
    check(m["state"]["pre"] <= 1 and m["state"]["full"].startswith("Discovered on"),
          f"'Discovered on' stays for a screen reader and the source is what shows  [{tag}]", str(m["state"]))
    if len(m["cards"]) >= 2:
        check(m["cards"][0]["bottom"] <= m["fold"], f"the first card, with its buttons, is above the fold  [{tag}]", f"bottom {m['cards'][0]['bottom']:.0f} vs {m['fold']}")
        check(m["cards"][1]["top"] < m["fold"] - 24, f"part of the second card shows too  [{tag}]", f"top {m['cards'][1]['top']:.0f} vs {m['fold']}")
    over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    check(over <= 0, f"no horizontal overflow  [{tag}]", f"{over}px")
    # the page scrolls and the tabs stay pinned
    if m["scrolls"] > 300:
        page.evaluate("window.scrollTo(0, 300)")
        page.wait_for_timeout(200)
        pinned = page.evaluate("document.querySelector('.board > .fg-tabs').getBoundingClientRect().top")
        check(abs(pinned) <= 1, f"the page scrolls and the tabs stay pinned at the top  [{tag}]", f"tabs at {pinned}px")
        page.evaluate("window.scrollTo(0, 0)")
    # the account menu: a real menu button
    page.click("#account-menu > summary")
    page.wait_for_function("document.querySelector('#account-menu').open")
    page.wait_for_function("document.activeElement && document.activeElement.getAttribute('role') === 'menuitem'")
    first_text = page.evaluate("document.activeElement.textContent.trim()")
    check(first_text == "Role preferences", f"opening the account menu puts focus on its first row  [{tag}]", first_text)
    page.keyboard.press("ArrowDown")
    check(page.evaluate("document.activeElement.textContent.trim()") == "Sign out", f"the arrow key moves to the next row  [{tag}]")
    page.keyboard.press("ArrowDown")
    check(page.evaluate("document.activeElement.textContent.trim()") == "Role preferences", f"...and wraps around  [{tag}]")
    box = page.evaluate("(() => { const m = document.querySelector('#account-menu .fg-menu').getBoundingClientRect(); const x = (m.left + m.right) / 2, y = m.top + 20; return { l: m.left, r: m.right, t: m.top, b: m.bottom, hit: !!document.elementFromPoint(x, y).closest('#account-menu .fg-menu') }; })()")
    check(box["l"] >= -0.5 and box["r"] <= w + 0.5 and box["t"] >= -0.5, f"the account menu is inside the screen  [{tag}]", str(box))
    check(box["hit"], f"the account menu is above the board, not under it  [{tag}]")
    page.keyboard.press("Escape")
    check(not page.evaluate("document.querySelector('#account-menu').open"), f"Escape closes the account menu  [{tag}]")
    check(page.evaluate("document.activeElement === document.querySelector('#account-menu > summary')"), f"...and returns focus to its button  [{tag}]")
    # a filter menu opens as a sheet at the foot of the screen, so the scrolling row cannot clip it
    page.click("#filter-posted > summary")
    sheet = page.evaluate("(() => { const m = document.querySelector('#filter-posted .fg-menu'), r = m.getBoundingClientRect(); return { pos: getComputedStyle(m).position, l: r.left, r: r.right, b: r.bottom, t: r.top }; })()")
    check(sheet["pos"] == "fixed" and sheet["l"] >= 0 and sheet["r"] <= w and sheet["b"] <= h and sheet["t"] > 0,
          f"a filter menu opens as a sheet at the foot of the screen  [{tag}]", str(sheet))
    page.keyboard.press("Escape")
    ctx.close()

    # wider screens do not change
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_cookies([{"name": "scout_session", "value": token, "url": base}])
    page = ctx.new_page()
    page.goto(base + "/", wait_until="load")
    d = page.evaluate(
        """
        () => {
          const cs = (sel) => getComputedStyle(document.querySelector(sel));
          return { wide: [...document.querySelectorAll('.header-wide')].map(e => getComputedStyle(e).display),
                   account: cs('.fg-header-actions > .fg-account').display, wordmark: cs('.fg-wordmark').fontSize,
                   voice: document.querySelector('.voice-intro') ? cs('.voice-intro').fontSize : null,
                   label: cs('#filter-work .fg-filter-label').position, value: document.querySelector('#filter-posted .fg-filter-value').innerText.trim(),
                   count: cs('[data-open-count]').display, viewed: cs('[data-viewed-link]').borderTopWidth, viewedText: document.querySelector('[data-viewed-link]').innerText.trim(),
                   board: [cs('.board').borderTopWidth, cs('.board').borderLeftWidth], boardOverflow: cs('.board').overflowY, body: cs('body').overflow,
                   // the card's text column: title, place and pay all start at the same x, on their own lines
                   col: (() => { const c = document.querySelector('li.fg-job'), l = (q) => Math.round(c.querySelector(q).getBoundingClientRect().left), t = (q) => Math.round(c.querySelector(q).getBoundingClientRect().top);
                                  return { x: [l('.fg-job-title'), l('.fg-job-subline'), l('.fg-job-pay')], y: [t('.fg-job-subline'), t('.fg-job-pay')] }; })() };
        }
        """
    )
    check(all(x != "none" for x in d["wide"]) and d["account"] == "none", "a wide screen keeps the two header buttons and has no account menu  [@1280]", str(d))
    check(d["wordmark"] == "36px" and d["voice"] in (None, "18px"), "a wide screen keeps the 36px wordmark and the 18px line  [@1280]", str(d))
    check(d["label"] != "absolute" and d["value"].startswith("within"), "a wide screen keeps the filter labels and values  [@1280]", str(d))
    check(d["count"] != "none" and d["viewed"] == "0px" and d["viewedText"] == "Viewed jobs", "a wide screen keeps the open-roles count and the Viewed jobs link  [@1280]", str(d))
    check(len(set(d["col"]["x"])) == 1 and d["col"]["y"][0] < d["col"]["y"][1], "a wide screen keeps title, place and pay in one column, place above pay  [@1280]", str(d["col"]))
    check(d["board"] == ["1px", "1px"] and d["boardOverflow"] == "hidden" and d["body"] == "hidden", "a wide screen keeps the bordered board and the two-pane scroll  [@1280]", str(d))
    ctx.close()


def check_panel(browser, base, token):
    """The panel beside the list (SC-72). The page itself never scrolls: the list and the panel scroll in their own panes,
    tops aligned. Clicking a card's title opens the panel instead of the posting (nothing else opens), moves focus to its
    heading and gives the card the Forest edge; the first scroll folds Scout's line and slims the header, and the full
    header returns at the top; Close (only Close) shuts it and returns focus to that card; on a phone the panel is a
    full screen sheet that does not overflow."""
    for (w, h) in ((1280, 900), (390, 844)):
        mobile = w < MOBILE_MAX_WIDTH
        tag = f"@{w}"
        ctx = browser.new_context(viewport={"width": w, "height": h}, is_mobile=mobile, has_touch=mobile)
        ctx.add_cookies([{"name": "scout_session", "value": token, "url": base}])
        page = ctx.new_page()
        page.goto(base + "/", wait_until="load")
        rows = page.evaluate("document.querySelectorAll('li.fg-job').length")
        check(rows > 0, f"the dashboard has jobs to open in a panel  [{tag}]")
        if not rows:
            ctx.close()
            continue
        scroll_h = page.evaluate("document.documentElement.scrollHeight - innerHeight")
        if not mobile:   # on a phone the page scrolls (SC-75); beside the panel on a wide screen only the panes do
            check(scroll_h <= 1, f"the page itself does not scroll  [{tag}]", f"{scroll_h}px taller than the window")
        first = page.locator("li.fg-job").first
        row_id = first.get_attribute("id")
        first.locator(".fg-job-link").click()
        page.wait_for_selector("#apply-panel:not([hidden]) [data-panel-title]")
        settle(page)   # the panel slides in: measure it where it ends up
        check(len(ctx.pages) == 1, f"a card's title opens the panel, not the posting in a new tab  [{tag}]", f"{len(ctx.pages)} tabs")
        check(page.evaluate("document.activeElement.id") == "panel-title", f"opening the panel moves focus to its heading  [{tag}]")
        check(page.evaluate("[...document.querySelectorAll('.fg-job.is-selected')].map(r => r.id)") == [row_id],
              f"exactly the open card is selected  [{tag}]")
        apply_text = page.evaluate("document.querySelector('#apply-panel a[href^=\"/open/\"]').textContent.trim()")
        check(apply_text.startswith("Apply") and "opens the posting in a new tab" in apply_text,
              f"the panel has Apply with a full accessible name  [{tag}]", apply_text)
        over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        check(over <= 0, f"no horizontal overflow with the panel open  [{tag}]", f"{over}px")
        rect = page.evaluate("(() => { const r = document.getElementById('apply-panel').getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })()")
        if mobile:
            check(abs(rect[0]) <= 1 and abs(rect[1]) <= 1 and abs(rect[2] - w) <= 1 and abs(rect[3] - h) <= 1,
                  f"on a phone the panel is a full screen sheet  [{tag}]", f"{rect} vs {w}x{h}")
            check(page.evaluate("getComputedStyle(document.body).overflow") == "hidden",
                  f"...and the page behind the sheet does not scroll  [{tag}]")
        else:
            tops = page.evaluate("[document.querySelector('.board').getBoundingClientRect().top, document.getElementById('apply-panel').getBoundingClientRect().top]")
            check(abs(tops[0] - tops[1]) <= 1, f"the list and the panel have their tops aligned  [{tag}]", f"{tops}")
            widths = page.evaluate("[document.querySelector('.board').getBoundingClientRect().width, document.getElementById('apply-panel').getBoundingClientRect().width]")
            check(abs(widths[0] - widths[1]) <= 1 or widths[1] >= widths[0], f"the list and the panel share the width 50/50 (the panel never below 480px)  [{tag}]", f"{widths}")
            edge = page.evaluate("[getComputedStyle(document.getElementById('%s')).borderTopColor, getComputedStyle(document.querySelector('#apply-panel .fg-button--primary')).backgroundColor]" % row_id)
            check(edge[0] == edge[1], f"the open card has the Forest edge  [{tag}]", f"{edge}")
            # the scroll fold: first scroll folds Scout's line and slims the header; the top brings it all back
            page.mouse.move(400, 500)
            page.mouse.wheel(0, 600)
            page.wait_for_timeout(500)
            folded = page.evaluate("document.body.classList.contains('is-collapsed')")
            check(folded, f"the first scroll folds Scout's line and slims the header  [{tag}]")
            voice = page.evaluate("document.querySelector('.voice-wrap') ? document.querySelector('.voice-wrap').getBoundingClientRect().height : 0")
            check(voice <= 1, f"...Scout's line is folded away  [{tag}]", f"{voice}px")
            # the list scrolls under its tabs, like the panel's body: the board itself never scrolls, so its scrollbar can't
            # run through the rounded corners (it did once, and looked off beside the panel's)
            geo = page.evaluate("(() => { const b = document.querySelector('.board'), t = b.querySelector(':scope > .fg-tabs'), l = b.querySelector(':scope > [role=tabpanel]'); return [b.scrollTop, l.scrollTop, t.getBoundingClientRect().bottom, l.getBoundingClientRect().top]; })()")
            check(geo[0] == 0 and geo[1] > 0, f"the list scrolls under its tabs, not the whole board  [{tag}]", f"{geo}")
            check(geo[3] >= geo[2] - 1, f"...so its scrollbar starts below the tabs  [{tag}]", f"{geo}")
            tops = page.evaluate("[document.querySelector('.board').getBoundingClientRect().top, document.getElementById('apply-panel').getBoundingClientRect().top]")
            check(abs(tops[0] - tops[1]) <= 1, f"...and the tops are still aligned  [{tag}]", f"{tops}")
            check(page.evaluate("document.documentElement.scrollHeight - innerHeight") <= 1, f"...and the page still does not scroll  [{tag}]")
            page.mouse.wheel(0, -2000)
            page.wait_for_timeout(500)
            check(not page.evaluate("document.body.classList.contains('is-collapsed')"), f"back at the top the full header returns  [{tag}]")
        page.locator("#apply-panel [data-panel-close]").click()
        page.wait_for_selector("#apply-panel", state="hidden")
        check(page.evaluate("document.activeElement.closest('li.fg-job') && document.activeElement.closest('li.fg-job').id") == row_id,
              f"Close returns focus to the card  [{tag}]")
        check(page.evaluate("document.querySelectorAll('.fg-job.is-selected').length") == 0, f"nothing is selected once it is closed  [{tag}]")
        ctx.close()


# ---- tailoring (SC-72 slice 2) ----------------------------------------------

TAILOR_COMPANY = "Quality Check Studio"
TAILOR_STRUCTURE = {
    "header": ["Ada Lovelace", "ada@example.com"],
    "summary": "Product designer with 10 years of experience across B2B and consumer products.",
    "experience": [{"company": "Northwind Health", "title": "Design Lead", "location": "", "dates": "2022 to now",
                    "bullets": ["Ran research and shipped a redesign that cut support calls by 20%.",
                                "Owned the design system in Figma and hired two designers.",
                                "Worked with developers to keep designs and code in sync."]}],
    "skills": ["Figma", "Prototyping", "User research", "Accessibility"],
    "education": [], "other": [],
}


def provision_tailor_fixture(user_id):
    """One open design role at a made-up company with stored text and requirements, and a resume for the check's user,
    so the panel can be tailored for without a model call. Returns the posting's id. Removed by remove_tailor_fixture."""
    text = " ".join(TAILOR_STRUCTURE["header"] + [TAILOR_STRUCTURE["summary"]] + [b for r in TAILOR_STRUCTURE["experience"] for b in r["bullets"]] +
                    TAILOR_STRUCTURE["skills"])
    reqs = [{"label": "Design systems experience", "terms": ["design system"]},
            {"label": "Working knowledge of React", "terms": ["react"]}]
    conn = get_conn()
    with conn.cursor() as cur:
        remove_tailor_fixture(conn)
        cur.execute("INSERT INTO companies (name, ats_type, ats_identifier) VALUES (%s, 'ashby', 'quality-check-fixture') RETURNING id", (TAILOR_COMPANY,))
        company_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO postings (company_id, ats_posting_id, title, location, remote_type, url, is_design, description, requirements, requirements_at)
               VALUES (%s, 'qc-1', 'Staff Product Designer, Design Systems', 'Remote - United States', 'remote',
                       'https://example.invalid/qc', true, 'We want someone to lead our design system and know React.', %s, now()) RETURNING id""",
            (company_id, json.dumps(reqs)))
        posting_id = cur.fetchone()[0]
        cur.execute("INSERT INTO user_resumes (user_id, filename, content, size_bytes, text) VALUES (%s, 'qc.pdf', %s, 10, %s) "
                    "ON CONFLICT (user_id) DO UPDATE SET text = EXCLUDED.text, uploaded_at = now()", (user_id, b"%PDF-1.4 qc", text))
    conn.commit()
    conn.close()
    return posting_id


def remove_tailor_fixture(conn=None, user_id=None):
    own = conn is None
    conn = conn or get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM companies WHERE ats_identifier = 'quality-check-fixture'")   # cascades the posting
        if user_id:
            cur.execute("DELETE FROM user_resumes WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM resume_structures WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM tailorings WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM tailor_calls WHERE user_id = %s", (user_id,))
    if own:
        conn.commit()
        conn.close()


TAILOR_LETTER = (
    "Hello Quality Check Studio team,\n\n"
    "I have spent 10 years designing products people use every day. At Northwind Health I owned the design system in "
    "Figma and hired two designers, and I ran the research behind a redesign that cut support calls by 20%.\n\n"
    "The Staff Product Designer role at Quality Check Studio leads design systems, which is the work I enjoy most, and I like working "
    "closely with developers to keep designs and code in sync. [One sentence on why Quality Check Studio appeals.]\n\n"
    "I would love to talk about the role. [Link to portfolio]\n\nSincerely,\n[Name]")


def got_404(ctx, url):
    return ctx.request.get(url).status == 404


def check_tailor(browser, base, token, user_id):
    """Tailoring in the panel, end to end with the model calls faked (so nothing is spent) and the real database: the
    Prep stage offers Tailor with the month's count; clicking it moves focus to the Working stage's first line, whose
    Cancel returns to Prep; tailoring again ends in Review without any press (the page polls), with every change before
    and after, Undo that becomes Redo (focus stays on that change's button, the count follows), and the change kept
    after the panel is closed and opened again. Cancelled calls do not count toward the month's ten. Then the optional cover
    letter (asked for at Tailor time at 1280, from Review at 390: only its own section changes while it is written, edits
    save as you leave the box), Looks good and Ready (the PDF and Word downloads really are those files, the row says
    Tailored, Back to the changes takes the marker off), and Yes saving the files with the application (listed on the
    Applications tab, and let go again when the Yes is undone)."""
    gate = threading.Event()
    letter_gate = threading.Event()
    structure_reads = []

    def fake_read(pdf):
        structure_reads.append(1)
        return resume_module.ReadResult(TAILOR_STRUCTURE, 2800, 700)

    def fake_tailor(structure, posting_text, requirements, missing):
        gate.wait(40)   # hold the call so the Working stage can be looked at
        return resume_module.ReadResult({
            "rewrites": [{"id": "e0b1", "after": "Led the design system in Figma and hired two designers.", "why": "Leads with design systems."},
                         {"id": "e0b2", "after": "Partnered with engineers and React developers to keep designs and code in sync.", "why": "Not allowed: React."}],
            "bullet_order": [{"role": "e0", "order": ["e0b1", "e0b0", "e0b2"], "why": "Design systems first."}],
            "skills_order": {"skills": ["Accessibility", "Figma", "Prototyping", "User research"], "why": "Nothing added or removed."},
        }, 1800, 350, "req_qc", "claude-sonnet-5-5")

    def fake_letter(structure, posting_text, company, title, missing, note):
        letter_gate.wait(40)
        return resume_module.ReadResult({"letter": TAILOR_LETTER}, 2600, 380, "req_qc_letter", "claude-sonnet-5-5")

    saved = (tailor.READ_PROVIDER, tailor.PROVIDER, letter_module.PROVIDER)
    tailor.READ_PROVIDER, tailor.PROVIDER, letter_module.PROVIDER = fake_read, fake_tailor, fake_letter
    posting_id = provision_tailor_fixture(user_id)
    try:
        for (w, h) in ((1280, 900), (390, 844)):
            mobile = w < MOBILE_MAX_WIDTH
            tag = f"@{w}"
            gate.clear()
            letter_gate.clear()
            del structure_reads[:]
            conn = get_conn()
            with conn.cursor() as cur:   # each width starts from nothing tailored
                for table in ("tailorings", "tailor_calls", "resume_structures"):
                    cur.execute(f"DELETE FROM {table} WHERE user_id = %s", (user_id,))
            conn.commit()
            conn.close()
            ctx = browser.new_context(viewport={"width": w, "height": h}, is_mobile=mobile, has_touch=mobile)
            ctx.add_cookies([{"name": "scout_session", "value": token, "url": base}])
            page = ctx.new_page()
            page.goto(base + "/", wait_until="load")
            row = page.locator(f"li.fg-job:has-text('{TAILOR_COMPANY}')").first
            check(row.count() == 1, f"the tailoring fixture role is in the list  [{tag}]")
            if not row.count():
                ctx.close()
                continue
            held = []
            page.route("**/prep/*", lambda r: held.append(r))   # hold the server's answer: the panel must not wait for it
            row.locator(".fg-job-link").click()
            page.wait_for_selector("#apply-panel:not([hidden]) [aria-busy='true']", timeout=2000)
            opening = page.evaluate("[document.querySelector('#apply-panel .fg-panel-company').textContent, document.querySelector('#apply-panel .fg-panel-title').textContent, "
                                    "document.querySelectorAll('#apply-panel .fg-skeleton').length, document.querySelector('.fg-job.is-selected') !== null]")
            check(opening[0] == TAILOR_COMPANY and opening[1].startswith("Staff Product Designer") and opening[2] == 4 and opening[3],
                  f"clicking a card opens the panel at once with its own company and title, before the server answers  [{tag}]", str(opening))
            held[0].continue_()
            page.unroute("**/prep/*")
            page.wait_for_selector("#apply-panel:not([hidden]) [data-panel-title]")
            check(page.locator("#apply-panel [aria-busy]").count() == 0, f"the real panel replaces the opening state  [{tag}]")
            settle(page)
            steps = page.evaluate("[...document.querySelectorAll('#apply-panel .fg-step')].map(li => [li.textContent.trim(), li.getAttribute('aria-current')])")
            check(steps[0][1] == "step" and [x[0][1:] for x in steps][:3] == ["Prep", "Tailor", "Apply"],
                  f"the steps strip names Prep, Tailor, Apply with Prep current  [{tag}]", str(steps))
            check(page.evaluate("!!document.querySelector('#apply-panel .fg-panel-body > .fg-steps:first-child') && !document.querySelector('#apply-panel .fg-panel-head + .fg-steps')"),
                  f"the stepper is the first thing in the scrolling body, not a bar under the header  [{tag}]")
            tip = page.locator("#apply-panel .fg-panel-head [data-tip-toggle]")
            note = page.locator("#apply-panel .fg-panel-head .fg-tip")
            check(tip.count() == 1 and note.count() == 1 and tip.get_attribute("aria-describedby") == note.get_attribute("id"),
                  f"the fit sits in the header with its tooltip as its description  [{tag}]")
            check(note.is_hidden(), f"the fit's note is quiet until asked for  [{tag}]")
            tip.focus()
            check(note.is_visible() and note.inner_text().strip() == "Estimate from posting and your resume.",
                  f"keyboard focus on the fit shows its note  [{tag}]", note.inner_text())
            page.keyboard.press("Escape")
            check(page.locator("#apply-panel:not([hidden])").count() == 1 and note.is_hidden(), f"Escape puts the note away and leaves the panel open  [{tag}]")
            tip.click()
            check(note.is_visible(), f"a tap on the fit shows its note  [{tag}]")
            page.locator("#apply-panel .fg-panel-body").click(position={"x": 4, "y": 4})
            check(note.is_hidden(), f"the next tap puts it away  [{tag}]")
            steps_box = page.evaluate("(() => { const li = [...document.querySelectorAll('#apply-panel .fg-steps .fg-step')]; const r = li.map(x => x.getBoundingClientRect()); const o = document.querySelector('#apply-panel .fg-steps').getBoundingClientRect(); return [r.every(x => x.top >= o.top - 1 && x.bottom <= o.bottom + 1), o.right <= document.querySelector('#apply-panel').getBoundingClientRect().right]; })()")
            check(steps_box[0] and steps_box[1], f"the three steps and their connectors sit on one line inside the panel  [{tag}]", str(steps_box))
            lead_ok = page.evaluate("(() => { const c = document.querySelector('#apply-panel .fg-lead'); if (!c) return null; const cs = getComputedStyle(c); return [cs.backgroundColor, cs.borderTopColor, c.querySelectorAll('ul > li').length > 0]; })()")
            check(lead_ok is not None and lead_ok[2] and lead_ok[0] != "rgba(0, 0, 0, 0)", f"What I would lead with is a Goldenrod card with a real list  [{tag}]", str(lead_ok))
            check(page.locator("#apply-panel input[type=checkbox]").count() == 0, f"Prep has no checkbox beside Tailor  [{tag}]")
            note = page.evaluate("(() => { const n = document.querySelector('#apply-panel .fg-panel-foot .fg-panel-help'); return n ? n.textContent.trim() : ''; })()")
            check(note == "To tailor, I send your resume and this posting to Claude. I’ll only say this once.",
                  f"the first time, one line says what is sent to Claude  [{tag}]", note)
            check(page.evaluate("(() => { const n = document.querySelector('#apply-panel .fg-panel-foot .fg-panel-help'); const b = document.querySelector('#apply-panel .fg-panel-foot form button'); return !!n && b.getAttribute('aria-describedby') === n.id; })()"),
                  f"that line is tied to the Tailor button for screen readers  [{tag}]")

            # Tailor: the Working stage
            page.locator("#apply-panel button:has-text('Tailor my resume')").click()
            page.wait_for_selector("#apply-panel [data-poll]")
            if os.environ.get("QC_SHOTS"):
                page.locator("#apply-panel").screenshot(path=os.path.join(os.environ["QC_SHOTS"], f"tailor-working@{w}.png"))
            check(page.evaluate("document.activeElement.hasAttribute('data-panel-focus')"), f"Tailor moves focus to the Working stage's first line  [{tag}]")
            say = page.evaluate("document.activeElement.textContent")
            check(say.startswith("I’m tailoring your resume for Quality Check Studio"), f"the Working stage says what is happening  [{tag}]", say)
            check(page.locator("#apply-panel .fg-progress .fg-progress-text").inner_text().strip() != "", f"progress text is showing  [{tag}]")
            check(page.locator("#apply-panel .fg-step[aria-current='step']").inner_text().strip().endswith("Tailor"), f"the Tailor step is current  [{tag}]")
            over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            check(over <= 0, f"no horizontal overflow while it works  [{tag}]", f"{over}px")

            # Cancel returns to Prep and the cancelled call does not count
            page.locator("#apply-panel form[action$='/cancel'] button").click()
            page.wait_for_selector("#apply-panel button:has-text('Tailor my resume')")
            check(page.evaluate("document.activeElement.hasAttribute('data-panel-focus')"), f"Cancel puts focus on Prep's first line  [{tag}]")
            check(page.evaluate("(() => { const n = document.querySelector('#apply-panel .fg-panel-foot .fg-panel-help'); return n ? n.textContent : ''; })()").startswith("To tailor, I send"),
                  f"a cancelled tailoring does not count: it is still the first time  [{tag}]")

            # Tailor again: no press needed to get from Working to Review
            gate.set()
            page.locator("#apply-panel button:has-text('Tailor my resume')").click()
            page.wait_for_selector("#apply-panel .fg-changes", timeout=30000)
            check(page.evaluate("document.activeElement.hasAttribute('data-panel-focus')"), f"when it finishes, focus lands on Review's first line  [{tag}]")
            try:   # the page sets it a moment after drawing Review (so an unchanged sentence is announced again)
                page.wait_for_function("document.getElementById('panel-live').textContent.includes('Your changes are ready to review.')", timeout=3000)
                told = True
            except Exception:  # noqa: BLE001
                told = False
            check(told, f"a screen reader is told the changes are ready  [{tag}]")
            cards = page.evaluate("[...document.querySelectorAll('#apply-panel .fg-change')].map(c => c.querySelector('.fg-change-kind').textContent.trim())")
            check(cards == ["Bullet, reworded", "Bullets, reordered", "Skills, reordered"],
                  f"Review lists the changes that passed the accuracy check, in resume order  [{tag}]", str(cards))
            after_all = page.locator("#apply-panel .fg-changes").inner_text()
            check("React developers" not in after_all, f"a rewording that adds something the resume lacks never reaches Review  [{tag}]")
            check(page.locator("#apply-panel .fg-change-before").count() == 3 and page.locator("#apply-panel .fg-change-after").count() == 3,
                  f"every change shows its before and its after  [{tag}]")
            note = page.locator("#apply-panel .fg-panel-body .fg-message--attention").inner_text()
            check(note.startswith("Not added: Working knowledge of React."), f"what the resume lacks is listed as not added  [{tag}]", note)
            check(page.locator("#apply-panel .fg-panel-foot [data-kept-line]").inner_text() == "3 of 3 changes kept", f"the count starts at all kept  [{tag}]")
            apply_text = page.evaluate("document.querySelector('#apply-panel .fg-panel-foot a[href^=\"/open/\"]').textContent.trim()")
            check(apply_text.startswith("Apply"), f"Apply is still there in Review  [{tag}]", apply_text)
            over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            check(over <= 0, f"no horizontal overflow in Review  [{tag}]", f"{over}px")

            if os.environ.get("QC_SHOTS"):
                page.locator("#apply-panel").screenshot(path=os.path.join(os.environ["QC_SHOTS"], f"tailor-review@{w}.png"))
            # Undo, Redo
            second = page.locator("#apply-panel .fg-change").nth(1)
            second.locator("button").click()
            page.wait_for_selector("#apply-panel .fg-change.is-undone")
            if os.environ.get("QC_SHOTS"):
                page.locator("#apply-panel").screenshot(path=os.path.join(os.environ["QC_SHOTS"], f"tailor-undone@{w}.png"))
            check(page.locator("#apply-panel .fg-panel-foot [data-kept-line]").inner_text() == "2 of 3 changes kept", f"Undo lowers the count  [{tag}]")
            btn = page.locator("#apply-panel .fg-change.is-undone button")
            check(btn.inner_text().strip().startswith("Redo") and "Undone" in page.locator("#apply-panel .fg-change.is-undone .fg-change-kind").inner_text(),
                  f"an undone change says Undone and offers Redo  [{tag}]")
            check(page.evaluate("document.activeElement === document.querySelector('#apply-panel .fg-change.is-undone button')"), f"focus stays on that change's button  [{tag}]")
            names = page.evaluate("[...document.querySelectorAll('#apply-panel .fg-change button')].map(b => b.textContent.replace(/\\s+/g, ' ').trim())")
            check(len(set(names)) == 3, f"each Undo/Redo button has its own name  [{tag}]", str(names))

            # Closed and opened again: the change is still undone
            page.locator("#apply-panel [data-panel-close]").click()
            page.wait_for_selector("#apply-panel", state="hidden")
            row.locator(".fg-job-link").click()
            page.wait_for_selector("#apply-panel .fg-changes")
            check(page.locator("#apply-panel .fg-change.is-undone").count() == 1 and
                  page.locator("#apply-panel .fg-panel-foot [data-kept-line]").inner_text() == "2 of 3 changes kept",
                  f"opening the job again shows the same Review, with the undone change  [{tag}]")
            page.locator("#apply-panel .fg-change.is-undone button").click()
            page.wait_for_selector("#apply-panel .fg-change.is-undone", state="detached")
            check(page.locator("#apply-panel .fg-panel-foot [data-kept-line]").inner_text() == "3 of 3 changes kept", f"Redo brings it back  [{tag}]")

            # The cover letter: only its own section changes while it is written
            page.evaluate("window.__changes = document.querySelector('#apply-panel .fg-changes')")
            section = page.locator("#apply-panel [data-letter]")
            text = section.inner_text()
            check("Most applications don’t need one." in text and "doesn’t count toward your 10 this month" in text,
                  f"Review offers a cover letter and says it does not use one of the ten  [{tag}]", text)
            section.locator("button:has-text('Write a cover letter')").click()
            page.wait_for_selector("#apply-panel [data-letter] .fg-progress")
            check(page.evaluate("document.activeElement.hasAttribute('data-letter-focus')"), f"asking for the letter moves focus to its section's first line  [{tag}]")
            check(page.evaluate("window.__changes.isConnected"), f"while the letter is written the changes above it stay where they were  [{tag}]")
            check(page.locator("#apply-panel .fg-panel-foot button:has-text('Looks good')").count() == 1, f"Review has Looks good beside Apply  [{tag}]")
            letter_gate.set()
            page.wait_for_selector("#apply-panel [data-letter] textarea", timeout=30000)
            try:
                page.wait_for_function("document.getElementById('panel-live').textContent.includes('Your cover letter is ready.')", timeout=3000)
                told = True
            except Exception:  # noqa: BLE001
                told = False
            check(told, f"a screen reader is told the letter is ready  [{tag}]")
            check(page.evaluate("window.__changes.isConnected"), f"the finished letter swapped in without redrawing the changes  [{tag}]")
            box = page.locator("#apply-panel [data-letter] textarea")
            check(box.input_value().startswith("Hello Quality Check Studio team,") and "[Name]" in box.input_value(),
                  f"the letter is a draft with bracketed parts to fill in  [{tag}]")
            check(page.evaluate("document.querySelector('#apply-panel [data-letter] textarea').labels.length === 0 && "
                                "document.getElementById(document.querySelector('#apply-panel [data-letter] textarea').getAttribute('aria-labelledby')).textContent.trim()") == "Cover letter",
                  f"the letter box is named by its heading  [{tag}]")
            sizes = page.evaluate("(() => { const b = document.querySelector('#apply-panel [data-letter] textarea'); return [b.scrollHeight, b.clientHeight, b.style.height, b.className, b.rows]; })()")
            check(sizes[0] <= sizes[1] + 1, f"the letter box shows all its text: no scroll inside the panel's scroll  [{tag}]", repr(sizes))
            box.fill(box.input_value() + "\n\nP.S. Edited by me. " + "More words so the box has to grow. " * 40)
            check(page.evaluate("(() => { const b = document.querySelector('#apply-panel [data-letter] textarea'); return b.scrollHeight <= b.clientHeight + 1; })()"),
                  f"the letter box grows as the person types  [{tag}]")
            box.fill(box.input_value().split("\n\nP.S.")[0] + "\n\nP.S. Edited by me.")
            box.press("Tab")
            page.wait_for_function("document.querySelector('#apply-panel [data-letter-saved]').textContent.includes('saved')", timeout=5000)
            conn = get_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT letter, letter_state FROM tailorings WHERE user_id = %s AND status = 'ready' ORDER BY id DESC LIMIT 1", (user_id,))
                saved_letter, state = cur.fetchone()
            conn.close()
            check(state == "ready" and saved_letter.endswith("P.S. Edited by me."), f"an edit is saved as the person leaves the box  [{tag}]", repr(saved_letter[-40:]))
            check(page.locator("#apply-panel [data-letter] button:has-text('Write a cover letter')").count() == 0, f"a letter is written once: no second button  [{tag}]")
            copy = page.locator("#apply-panel [data-copy-letter]")
            check(page.evaluate("document.querySelector('#apply-panel [data-copy-letter]').textContent.replace(/\\s+/g, ' ').trim()") == "Copy cover letter",
                  f"the copy button is named 'Copy cover letter'  [{tag}]")
            if os.environ.get("QC_SHOTS"):
                page.locator("#apply-panel").screenshot(path=os.path.join(os.environ["QC_SHOTS"], f"tailor-letter@{w}.png"))

            # Looks good: Ready, with the two files
            page.locator("#apply-panel .fg-panel-foot button:has-text('Looks good')").click()
            page.wait_for_selector("#apply-panel [data-tailored]")
            check(page.evaluate("document.activeElement.hasAttribute('data-panel-focus')"), f"Looks good moves focus to Ready's first line  [{tag}]")
            check(page.evaluate("document.activeElement.textContent") == "You’re ready. I saved these with the job.", f"Ready says it is ready  [{tag}]")
            check(page.locator("#apply-panel .fg-step[aria-current='step']").inner_text().strip().endswith("Apply"), f"the Apply step is current  [{tag}]")
            names = page.evaluate("[...document.querySelectorAll('#apply-panel .fg-files a[download]')].map(a => [a.textContent.replace(/\\s+/g, ' ').trim(), a.getAttribute('href')])")
            check([n[0] for n in names] == ["Download the PDF of your resume tailored for Quality Check Studio",
                                            "Download the Word file of your resume tailored for Quality Check Studio"],
                  f"each download says which file and for which company  [{tag}]", str(names))
            for (label, href), magic, kind in zip(names, (b"%PDF", b"PK"), ("pdf", "docx")):
                got = ctx.request.get(base + href)
                body = got.body()
                check(got.status == 200 and body[:len(magic)] == magic and got.headers.get("content-disposition", "").endswith(f".{kind}\""),
                      f"the {kind.upper()} download is a real {kind} file  [{tag}]", f"{got.status} {got.headers.get('content-disposition')}")
                if kind == "pdf":
                    import io, pypdf
                    pdf_text = "\n".join(pg.extract_text() for pg in pypdf.PdfReader(io.BytesIO(body)).pages)
                    check("Ada Lovelace" in pdf_text and "Partnered with engineers" not in pdf_text and "Led the design system in Figma" in pdf_text,
                          f"the PDF is the resume with the kept changes in it  [{tag}]")
            check(page.locator("#apply-panel [data-letter] textarea").input_value().endswith("P.S. Edited by me."), f"Ready shows the letter with the person's edit  [{tag}]")
            check(page.locator("#apply-panel [data-copy-letter]").count() == 1, f"Ready has Copy cover letter  [{tag}]")
            check("When you’re back, I’ll ask if you applied." in page.locator("#apply-panel .fg-panel-foot").inner_text(),
                  f"Ready says what happens after Apply  [{tag}]")
            check(page.locator("#apply-panel .fg-panel-foot a[href^='/open/']").count() == 1, f"Apply is still there in Ready  [{tag}]")
            check(row.locator("[data-tailored-mark]").count() == 1, f"the job's row now says Tailored  [{tag}]")
            over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            check(over <= 0, f"no horizontal overflow in Ready  [{tag}]", f"{over}px")
            if os.environ.get("QC_SHOTS"):
                page.locator("#apply-panel").screenshot(path=os.path.join(os.environ["QC_SHOTS"], f"tailor-ready@{w}.png"))

            # Back to the changes takes the marker off; Looks good puts it back
            page.locator("#apply-panel button:has-text('Back to the changes')").click()
            page.wait_for_selector("#apply-panel .fg-changes")
            check(page.evaluate("document.activeElement.hasAttribute('data-panel-focus')"), f"Back to the changes moves focus to Review's first line  [{tag}]")
            check(row.locator("[data-tailored-mark]").count() == 0, f"Back to the changes takes Tailored off the row  [{tag}]")
            check(got_404(ctx, base + names[0][1]), f"the files are not offered while the person is back in the changes  [{tag}]")
            page.locator("#apply-panel .fg-panel-foot button:has-text('Looks good')").click()
            page.wait_for_selector("#apply-panel [data-tailored]")

            # Yes: the tailored files go with the application
            posted = ctx.request.post(f"{base}/set_applied/{posting_id}", form={"applied": "true"}, headers={"X-Requested-With": "fetch"})
            check(posted.status == 204, f"Yes is accepted  [{tag}]", str(posted.status))
            apps = ctx.new_page()
            apps.goto(base + "/?tab=applications", wait_until="load")
            app_row = apps.locator(f"li.fg-job:has-text('{TAILOR_COMPANY}')")
            group_of = f"[...document.querySelectorAll('li.fg-job')].find(li => li.textContent.includes('{TAILOR_COMPANY}')).closest('section.fg-day').getAttribute('aria-label')"
            check(app_row.locator("a[download]").count() == 0 and app_row.locator("[data-tailored-mark]").count() == 1,
                  f"the application's row says Tailored and carries no file links  [{tag}]")
            # SC-73: the card opens the application panel, which shows what was sent
            app_row.locator(".fg-job-link").click()
            apps.wait_for_selector("#apply-panel [data-application]")
            links = apps.evaluate("[...document.querySelectorAll('#apply-panel a[download]')].map(a => [a.textContent.replace(/\\s+/g, ' ').trim(), a.getAttribute('href')])")
            check([l[0] for l in links] == ["PDF of your resume tailored for Quality Check Studio", "Word file of your resume tailored for Quality Check Studio"],
                  f"the application panel offers the tailored resume as PDF and Word  [{tag}]", str(links))
            copy = apps.locator("#apply-panel [data-copy-text]")
            check(copy.count() == 1 and (copy.get_attribute("data-copy-text") or "").rstrip().endswith("P.S. Edited by me."),
                  f"the application panel has the letter to copy, with the person's edit  [{tag}]")
            if links:
                kept = [ctx.request.get(base + l[1]) for l in links]
                check([k.status for k in kept] == [200, 200] and kept[0].body()[:4] == b"%PDF",
                      f"those files still download after the job left Open roles  [{tag}]")
            check(apps.locator("#apply-panel [data-history] .fg-history-text").all_inner_texts() == ["Applied with a tailored resume and cover letter"],
                  f"History starts with the application and what went with it  [{tag}]")
            check(apps.locator("#apply-panel [data-stage-select]").input_value() == "waiting" and apps.evaluate(group_of) == "Waiting to hear",
                  f"a new application is waiting to hear  [{tag}]")
            # Where it stands: the row moves, a toast says so with Undo, History grows, focus stays on the select
            apps.select_option("#apply-panel [data-stage-select]", "interviewing")
            apps.wait_for_selector("#toast-region .fg-toast")
            apps.wait_for_function(f"{group_of} === 'Interviewing'")
            check(apps.locator("#toast-region .fg-toast-text").inner_text() == "Moved to Interviewing.",
                  f"changing Where it stands says what happened  [{tag}]", apps.locator("#toast-region").inner_text())
            check(apps.locator("#apply-panel [data-history] .fg-history-text").first.inner_text() == "Interviewing"
                  and apps.locator("#apply-panel [data-history] .fg-history-item").count() == 2,
                  f"History gets the new line first  [{tag}]")
            check(apps.evaluate("document.activeElement.matches('[data-stage-select]')"), f"focus stays on Where it stands  [{tag}]")
            check("interview going" in apps.locator("[data-voice]").inner_text() and app_row.locator("[data-state-top]").inner_text() == "Interviewing",
                  f"Scout's line and the row follow the change  [{tag}]", apps.locator("[data-voice]").inner_text())
            check(app_row.get_attribute("aria-current") == "true", f"the row stays selected while its panel is open  [{tag}]")
            apps.locator("#toast-region [data-toast-undo]").click()
            apps.wait_for_function(f"{group_of} === 'Waiting to hear'")
            apps.wait_for_function("document.querySelector('#apply-panel [data-stage-select]') && document.querySelector('#apply-panel [data-stage-select]').value === 'waiting'")   # the panel redraws after the list
            check(apps.locator("#apply-panel [data-stage-select]").input_value() == "waiting"
                  and apps.locator("#apply-panel [data-history] .fg-history-item").count() == 1,
                  f"Undo puts the row, the select and History back  [{tag}]")
            if os.environ.get("QC_SHOTS"):
                apps.screenshot(path=os.path.join(os.environ["QC_SHOTS"], f"application-panel@{w}.png"))

            # SC-73 slice 3: a week on, the row asks "Did you hear back?"; the answers move it along
            conn = get_conn()
            with conn.cursor() as cur:
                cur.execute("UPDATE user_events SET created_at = now() - interval '6 days' "
                            "WHERE user_id = %s AND posting_id = %s AND event_type = 'applied'", (user_id, posting_id))
            conn.commit()
            conn.close()
            apps.reload(wait_until="load")
            ask = lambda: app_row.locator(".fg-job-question").all_inner_texts()
            check(ask() == ["Did you hear back?"] and app_row.locator("[data-checkin]").count() == 2,
                  f"an application six days old asks Did you hear back?  [{tag}]", str(ask()))
            app_row.locator("[data-checkin='heard_no']").click()
            apps.wait_for_selector(f"li.fg-job:has-text('{TAILOR_COMPANY}') [data-checkin-note]")
            check(app_row.locator("[data-checkin-note]").inner_text() == "I’ll check back in a week."
                  and apps.evaluate("document.activeElement.hasAttribute('data-checkin-note')"),
                  f"Not yet says it will check back in a week, and says it aloud  [{tag}]")
            apps.reload(wait_until="load")
            check(ask() == [], f"after Not yet the row stays quiet  [{tag}]", str(ask()))
            conn = get_conn()
            with conn.cursor() as cur:
                cur.execute("UPDATE user_postings SET heard_not_yet_at = NULL WHERE user_id = %s AND posting_id = %s", (user_id, posting_id))
            conn.commit()
            conn.close()
            apps.reload(wait_until="load")
            app_row.locator("[data-checkin='heard_yes']").click()
            apps.wait_for_selector(f"li.fg-job:has-text('{TAILOR_COMPANY}') select[data-checkin-say]")
            check(ask() == ["What did they say?"] and apps.evaluate("document.activeElement.hasAttribute('data-checkin-say')"),
                  f"Yes asks What did they say? with focus on the menu  [{tag}]", str(ask()))
            over = apps.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            check(over <= 0, f"no horizontal overflow with the check-in menu showing  [{tag}]", f"{over}px")
            apps.reload(wait_until="load")
            check(ask() == ["What did they say?"], f"What did they say? is still asked after a reload  [{tag}]", str(ask()))
            app_row.locator("select[data-checkin-say]").select_option("interviewing")
            apps.wait_for_function(f"{group_of} === 'Interviewing'")
            check(apps.locator("#toast-region .fg-toast-text").inner_text() == "Moved to Interviewing."
                  and app_row.locator(".fg-job-question").count() == 0,
                  f"an answer moves the row with a toast, and the question is gone  [{tag}]")
            check(apps.evaluate("document.activeElement.classList.contains('fg-job-link')"), f"focus lands on the row's title  [{tag}]")
            app_row.locator(".fg-job-link").click()
            apps.wait_for_selector("#apply-panel [data-history] .fg-history-text")
            check(apps.locator("#apply-panel [data-history] .fg-history-text").first.inner_text() == "Heard back: interview",
                  f"History says Heard back: interview  [{tag}]")
            apps.locator("#toast-region [data-toast-undo]").click()
            apps.wait_for_function(f"{group_of} === 'Waiting to hear'")
            apps.wait_for_function("document.querySelector('#apply-panel [data-stage-select]') && document.querySelector('#apply-panel [data-stage-select]').value === 'waiting'")
            check(apps.locator("#apply-panel [data-history] .fg-history-item").count() == 1, f"Undo of an answer puts History back too  [{tag}]")
            over = apps.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            check(over <= 0, f"no horizontal overflow on Applications with files  [{tag}]", f"{over}px")
            if os.environ.get("QC_SHOTS"):
                apps.screenshot(path=os.path.join(os.environ["QC_SHOTS"], f"tailor-applications@{w}.png"))
            apps.close()
            undone = ctx.request.post(f"{base}/set_applied/{posting_id}", form={"applied": "false"}, headers={"X-Requested-With": "fetch"})
            conn = get_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM tailorings WHERE user_id = %s AND applied_at IS NOT NULL", (user_id,))
                still = cur.fetchone()[0]
            conn.close()
            check(undone.status == 204 and still == 0, f"undoing the Yes lets the files go again  [{tag}]", f"{undone.status}, {still} still kept")
            ctx.close()

            conn = get_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT kind, status FROM tailor_calls WHERE user_id = %s ORDER BY id", (user_id,))
                calls = cur.fetchall()
            conn.close()
            check(calls.count(("letter", "ok")) == 1, f"the cover letter call is logged as a letter, apart from the ten  [{tag}]", str(calls))
            check(calls.count(("tailor", "ok")) == 1 and ("tailor", "cancelled") in calls,
                  f"a finished tailoring is logged ok and the cancelled one is logged apart, so only one counts  [{tag}]", str(calls))
            check(len(structure_reads) == 1, f"the resume is read into structure once, not for each tailoring  [{tag}]", f"{len(structure_reads)} reads")
    finally:
        tailor.READ_PROVIDER, tailor.PROVIDER, letter_module.PROVIDER = saved
        gate.set()
        letter_gate.set()
        remove_tailor_fixture(None, user_id)



def snapshot_styles(page):
    return page.evaluate(
        """
        ([selectors, props]) => {
          const out = {};
          for (const s of selectors) {
            const e = document.querySelector(s); if (!e) { out[s] = null; continue; }
            const cs = getComputedStyle(e); out[s] = {};
            for (const p of props) out[s][p] = cs.getPropertyValue(p);
          }
          return out;
        }
        """,
        [STYLE_SELECTORS, STYLE_PROPS],
    )


# ---- main ------------------------------------------------------------------


def main():
    update_baseline = "--update-baseline" in sys.argv
    user_id, token = provision_user()
    _, setup_token = provision_user(TEST_EMAIL_SETUP, onboarded=False)
    server = make_server("127.0.0.1", 0, app)
    base = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    snapshots = {}
    try:
        with sync_playwright() as p:
            exe = find_chromium()
            browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
            ctx = browser.new_context()
            ctx.add_cookies([{"name": "scout_session", "value": token, "url": base}])
            check_filter_states(ctx.new_page(), base)
            check_leave_warning(ctx, base)
            check_back_link(ctx, base)
            check_skills_minimum(ctx, base)
            ctx.close()
            check_signin_states(browser, base)
            check_chip_scroll(browser, base, token)
            check_return_ask(browser, base, token)
            check_hide(browser, base, token)
            check_panel(browser, base, token)
            check_phone(browser, base, token)
            check_tailor(browser, base, token, user_id)
            check_elevated(browser, base, token)
            check_pay(browser, base, token)
            check_alerts_switch_no_js(browser, base, token)
            header_infos = {}
            for (w, h) in VIEWPORTS:
                mobile = w < MOBILE_MAX_WIDTH
                ctx = browser.new_context(viewport={"width": w, "height": h}, is_mobile=mobile, has_touch=mobile)
                ctx.add_cookies([{"name": "scout_session", "value": token, "url": base}])
                page = ctx.new_page()
                for path in ("/login", "/profile", "/", "/field-guide"):
                    # /login redirects nowhere for anonymous *and* signed-in users; the rest need the cookie.
                    page.goto(base + path, wait_until="load")
                    check_page_basics(page, path, w)
                    if mobile:
                        iw = page.evaluate("window.innerWidth")
                        check(iw == w, f"mobile page reports true width  [{path} @{w}]", f"innerWidth={iw}")
                    if w == VIEWPORTS[-1][0]:
                        check_typefaces(page, path)
                    if path == "/field-guide" and w == VIEWPORTS[-1][0]:
                        check_gallery_complete(page)
                        check_control_heights(page)
                        kinds = check_action_outlines(page, path)
                        check(set(kinds) == {"secondary button", "suggested button", "select", "filter pill"},
                              "gallery shows every kind of outlined action", sorted(kinds))
                    if path in ("/profile", "/") and w == VIEWPORTS[-1][0]:
                        check_action_outlines(page, path)
                    if path == "/profile" and w == VIEWPORTS[-1][0]:
                        check_control_heights(page)
                    if path in ("/", "/profile"):
                        header_infos[(path, w)] = header_info(page, path, w)
                    if path == "/login":
                        check_login_artwork(page, w, h)
                    if path == "/":
                        check_toolbar(page, w)
                        check_filter_menus(page, w)
                        check_ui_font(page, w)
                        if w <= 680:
                            check_row_action(page, w)
                        snapshots[str(w)] = snapshot_styles(page)
                    # QC_SHOTS=<dir>: save a screenshot of each page/width for a
                    # side-by-side with the Figma frame.
                    if os.environ.get("QC_SHOTS"):
                        name = (path.strip("/") or "dashboard").replace("/", "_")
                        page.screenshot(path=os.path.join(os.environ["QC_SHOTS"], f"{name}@{w}.png"))
                ctx.close()
                # The profile screen for someone who hasn't onboarded (setup view, other action bar).
                ctx = browser.new_context(viewport={"width": w, "height": h}, is_mobile=mobile, has_touch=mobile)
                ctx.add_cookies([{"name": "scout_session", "value": setup_token, "url": base}])
                page = ctx.new_page()
                for path in ("/start", "/profile"):
                    page.goto(base + path, wait_until="load")
                    check_page_basics(page, f"{path} (setup)", w)
                    if w == VIEWPORTS[-1][0]:
                        check_typefaces(page, f"{path} (setup)")
                        check_control_heights(page)
                ctx.close()
            check_headers_match(header_infos)
            browser.close()
    finally:
        server.shutdown()
        remove_tailor_fixture()
        remove_user()

    # style drift vs. baseline (informational)
    drift = []
    if update_baseline or not os.path.exists(BASELINE_PATH):
        json.dump(snapshots, open(BASELINE_PATH, "w"), indent=1, sort_keys=True)
        print(f"[baseline] saved computed-style baseline -> {os.path.relpath(BASELINE_PATH, ROOT)}\n")
    else:
        base_snap = json.load(open(BASELINE_PATH))
        for width, sels in snapshots.items():
            for sel, props in sels.items():
                old = (base_snap.get(width) or {}).get(sel)
                if old is None or props is None:
                    continue
                for prop, val in props.items():
                    if old.get(prop) != val:
                        drift.append(f"@{width} {sel} {prop}: {old.get(prop)!r} -> {val!r}")

    failed = [r for r in results if not r[0]]
    for ok, label, detail in results:
        if not ok:
            print(f"FAIL  {label}" + (f"\n        {detail}" if detail else ""))
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if drift:
        print(f"\nREVIEW  {len(drift)} computed-style change(s) vs. baseline (intended? then --update-baseline):")
        for d in drift[:25]:
            print("  ", d)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
