"""Automated UI standing checks (SC-32) - the mechanical ones from QUALITY_LOG.md.

    python scripts/quality_check.py                  # run all checks
    python scripts/quality_check.py --update-baseline  # accept current computed styles

Run before calling any UI change done. Exits non-zero on any failure.

What it checks, at phone / tablet / desktop sizes, on /login, /onboarding and /:
  1. viewport meta present, and a mobile-emulated page reports its true width
  2. no horizontal page overflow
  3. login route artwork (pins, dots) stays inside the viewport once animations end
  4. dashboard toolbar items are vertically aligned (centerY) and padded >= 16px
  5. computed styles of the toolbar vs. a saved baseline - flags *changes* for
     review (sibling-regression catcher; not a failure, since edits are often intended)

It boots the real Flask app against the DB in .env.local with a throwaway user
(deleted on exit), and drives it with headless Chromium via Playwright
(`pip install playwright`; uses an already-installed Chromium if the pinned
build isn't present).
"""
import glob
import json
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
VIEWPORTS = [(320, 640), (390, 844), (768, 1024), (800, 600), (1280, 800), (1440, 900)]
MOBILE_MAX_WIDTH = 768  # below this, emulate a touch phone (viewport meta must work)
ALIGN_TOLERANCE_PX = 1.0
MIN_SIDE_PADDING_PX = 16
STYLE_PROPS = ["font-size", "line-height", "font-weight", "color", "display", "padding", "margin"]
STYLE_SELECTORS = [".toolbar", ".toolbar-left .fg-check", ".toolbar-left .fg-link", ".role-count"]

logging.getLogger("werkzeug").setLevel(logging.ERROR)  # quiet the per-request access log

results = []  # (ok, label, detail)


def check(ok, label, detail=""):
    results.append((bool(ok), label, detail))


# ---- setup -----------------------------------------------------------------


def provision_user():
    """Throwaway onboarded user tracking a company that has open postings."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE email = %s", (TEST_EMAIL,))
    cur.execute(
        "INSERT INTO users (email, ntfy_topic, onboarded_at) VALUES (%s, 'quality-check', now()) RETURNING id",
        (TEST_EMAIL,),
    )
    user_id = cur.fetchone()[0]
    cur.execute(
        """
        SELECT company_id FROM postings WHERE status = 'open'
        GROUP BY company_id ORDER BY count(*) DESC LIMIT 1
        """
    )
    row = cur.fetchone()
    if row:
        cur.execute("INSERT INTO user_companies (user_id, company_id) VALUES (%s, %s)", (user_id, row[0]))
    token = create_session(conn, user_id)
    conn.commit()
    conn.close()
    return user_id, token


def remove_user():
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE email = %s", (TEST_EMAIL,))  # cascades sessions/events
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
    info = page.evaluate(
        """
        () => {
          const sel = ['.toolbar-left .fg-check', '.toolbar-left .fg-link', '.role-count'];
          const rects = sel.map(s => { const e = document.querySelector(s); if (!e) return null;
            const r = e.getBoundingClientRect(); return { s, cy: r.top + r.height / 2, left: r.left }; });
          return { rects, toolbarLeft: document.querySelector('.toolbar-left')?.getBoundingClientRect().left };
        }
        """
    )
    if any(r is None for r in info["rects"]):
        check(False, f"toolbar elements found  [@{w}]", "missing toolbar item")
        return
    ys = [r["cy"] for r in info["rects"]]
    spread = max(ys) - min(ys)
    detail = ", ".join(f"{r['s'].split()[-1]}={r['cy']:.1f}" for r in info["rects"])
    check(spread <= ALIGN_TOLERANCE_PX, f"toolbar items share a centerY  [@{w}]", f"spread {spread:.2f}px ({detail})")
    check(info["toolbarLeft"] >= MIN_SIDE_PADDING_PX - 0.5, f"toolbar side padding >= {MIN_SIDE_PADDING_PX}px  [@{w}]",
          f"left edge at {info['toolbarLeft']:.1f}px")


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
    server = make_server("127.0.0.1", 0, app)
    base = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    snapshots = {}
    try:
        with sync_playwright() as p:
            exe = find_chromium()
            browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
            for (w, h) in VIEWPORTS:
                mobile = w < MOBILE_MAX_WIDTH
                ctx = browser.new_context(viewport={"width": w, "height": h}, is_mobile=mobile, has_touch=mobile)
                ctx.add_cookies([{"name": "scout_session", "value": token, "url": base}])
                page = ctx.new_page()
                for path in ("/login", "/onboarding", "/"):
                    # /login redirects nowhere for anonymous *and* signed-in users; the rest need the cookie.
                    page.goto(base + path, wait_until="load")
                    check_page_basics(page, path, w)
                    if mobile:
                        iw = page.evaluate("window.innerWidth")
                        check(iw == w, f"mobile page reports true width  [{path} @{w}]", f"innerWidth={iw}")
                    if path == "/login":
                        check_login_artwork(page, w, h)
                    if path == "/":
                        check_toolbar(page, w)
                        snapshots[str(w)] = snapshot_styles(page)
                ctx.close()
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
