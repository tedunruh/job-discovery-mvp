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
            return { s: e.className, top: r.top, bottom: r.bottom, cy: r.top + r.height / 2, lines: inner / lh };
          });
          return { items, toolbarLeft: document.querySelector('.toolbar')?.getBoundingClientRect().left };
        }
        """,
        TOOLBAR_ITEMS,
    )
    items = info["items"]
    if len(items) < 3:
        check(False, f"toolbar elements found  [@{w}]", f"expected 2 filter pills + role count, found {len(items)}")
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
    check(info["toolbarLeft"] >= MIN_SIDE_PADDING_PX - 0.5, f"toolbar side padding >= {MIN_SIDE_PADDING_PX}px  [@{w}]",
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
                               arg=before, timeout=5000)
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
                           arg=before, timeout=5000)
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
    "Did you apply?" with Yes and Not yet, without a reload and without taking focus; the latest job opened is the one
    asked about and the others say Viewed; Not yet puts Apply back for good (until the job is opened again). Yes sends
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
        with ctx.expect_page() as popup:
            page.locator(f"#posting-{job} a.fg-job-link").click()
        popup.value.wait_for_load_state("load")
        popup.value.close()

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

    # a second job: only the latest asks, the first goes back to Apply and says Viewed
    open_in_new_tab(ids[1])
    page.wait_for_timeout(2200)       # the script waits a moment between asks
    come_back()
    page.wait_for_selector(f"#posting-{ids[1]} [data-ask-yes]", timeout=8000)
    first = page.locator(f"#posting-{ids[0]}")
    check(asking() == [ids[1]] and first.locator("[data-apply]").count() == 1 and "Viewed" in first.locator("[data-state]").inner_text(),
          "after a second job only the latest asks; the first has Apply back and says Viewed", f"{asking()} | {first.inner_text()[:100]}")

    # a reload shows it with no script: the server draws it
    page.reload(wait_until="load")
    check(asking() == [ids[1]], "after a reload the question is still on the latest job", f"{asking()}")

    # Not yet: Apply comes back now and after a reload, the row still says Viewed
    page.locator(f"#posting-{ids[1]} [data-ask-dismiss] button").click()
    page.wait_for_timeout(500)
    second = page.locator(f"#posting-{ids[1]}")
    check(asking() == [] and second.locator("[data-apply]").count() == 1 and "Viewed" in second.locator("[data-state]").inner_text(),
          "'Not yet' brings Apply back and the row still says Viewed", f"{asking()} | {second.inner_text()[:100]}")
    page.reload(wait_until="load")
    check(asking() == [], "...and it stays closed after a reload (until that job is opened again)", f"{asking()}")

    # opening it again asks again
    page.wait_for_timeout(2200)
    open_in_new_tab(ids[1])
    come_back()
    page.wait_for_selector(f"#posting-{ids[1]} [data-ask-yes]", timeout=8000)
    check(asking() == [ids[1]], "opening the job again asks again", f"{asking()}")

    # Yes while the server is failing: nothing is claimed. The row stays, still asking, and there is no toast.
    page.route("**/set_applied/*", lambda route: route.fulfill(status=500, body="no"))
    page.locator(f"#posting-{ids[1]} [data-ask-yes] button").click()
    page.wait_for_timeout(1200)
    check(page.locator(f"#posting-{ids[1]}").is_visible() and asking() == [ids[1]] and page.locator(".fg-toast").count() == 0,
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
        mp.locator(f"#posting-{other} a.fg-job-link").click()
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
    check(re.fullmatch(r"(\d+ new since yesterday\.|Nothing new since yesterday\.)", text) is not None
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
    check(page.locator(".fg-fit, .fg-job-fit").count() == 0, "no fit line on cards until the fit score exists")

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
        const ac = li.querySelector('[data-action] .fg-button').getBoundingClientRect(), c = li.getBoundingClientRect(), pay = li.querySelector('.fg-job-pay').getBoundingClientRect();
        return {mono: Math.round(m.width), monoLeft: m.left >= c.left, textAfterMono: t.left >= m.right, stateAligned: Math.abs(st.left - t.left) <= 1,
                actionAligned: Math.abs(ac.left - t.left) <= 1, payAligned: Math.abs(pay.left - t.left) <= 1, sw: document.documentElement.scrollWidth, vw: innerWidth} }""")
    check(geo["mono"] == 36 and geo["textAfterMono"] and geo["stateAligned"] and geo["actionAligned"] and geo["payAligned"] and geo["sw"] <= geo["vw"],
          "on a phone the monogram sits alone on the left and the state, action and pay line up with the text", str(geo))
    mctx.close()


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
  const actions = [...h.querySelectorAll('.fg-header-actions .fg-button')].map(b => ({
    text: b.textContent.trim(), cls: b.className, rect: b.getBoundingClientRect().toJSON(),
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
    check(info["wordmark"]["fontSize"] == "36px", f"wordmark is 36px  [{path} @{w}]", info["wordmark"]["fontSize"])
    if info["headline"]:
        check(info["headline"] == "28px", f"page headline (h1) is 28px, under the 36px wordmark  [{path} @{w}]", info["headline"])
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
    for fid in page.evaluate("[...document.querySelectorAll('details.fg-filter')].map(d => d.id)"):
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
            check_return_ask(browser, base, token)
            check_hide(browser, base, token)
            check_elevated(browser, base, token)
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
