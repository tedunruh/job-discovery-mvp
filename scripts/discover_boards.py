"""Discover and validate job boards at scale (SC-48). READ-ONLY: never writes to Scout's database.

    python scripts/discover_boards.py harvest [--crawls 3]   # collect candidate board names from Common Crawl
    python scripts/discover_boards.py validate [--limit N]   # check each against its real ATS (resumable)
    python scripts/discover_boards.py enrich                 # re-fetch design-bearing boards, keep all design titles
    python scripts/discover_boards.py workable-search        # measure Workable's cross-company search
    python scripts/discover_boards.py report                 # summarize what was found

Why: design roles are rare across the companies Scout watches, so how many boards it covers is
the bottleneck. No ATS publishes a list of all its customers, but their board pages are public, so
the web index Common Crawl has seen many of them. A name found there is only a *candidate*; it
counts only if the real ATS says the board exists and has open roles.

Everything is cached under scripts/_discovery/ (git-ignored) so runs can stop and resume. Requests
identify themselves, use modest per-host concurrency, and back off when asked to slow down.
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from collectors.filters import is_design_role  # noqa: E402

WORKDIR = os.path.join(ROOT, "scripts", "_discovery")
TOKENS_PATH = os.path.join(WORKDIR, "candidates.json")
RESULTS_PATH = os.path.join(WORKDIR, "validated.jsonl")
WORKABLE_PATH = os.path.join(WORKDIR, "workable_search.json")

UA = "Scout/1.0 (+https://github.com/tedunruh/job-discovery-mvp; board discovery, polite)"
HEADERS = {"User-Agent": UA}

# ats -> (Common Crawl URL patterns, regex capturing the board name from a URL)
SOURCES = {
    "greenhouse": (
        ["boards.greenhouse.io/*", "job-boards.greenhouse.io/*", "job-boards.eu.greenhouse.io/*", "boards.eu.greenhouse.io/*"],
        re.compile(r"greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)", re.I),
    ),
    "lever": (["jobs.lever.co/*", "jobs.eu.lever.co/*"], re.compile(r"lever\.co/([A-Za-z0-9_-]+)", re.I)),
    "ashby": (["jobs.ashbyhq.com/*"], re.compile(r"ashbyhq\.com/([A-Za-z0-9._%-]+)", re.I)),
    "workable": (["apply.workable.com/*"], re.compile(r"workable\.com/([A-Za-z0-9_-]+)", re.I)),
}
# First path segments that are site furniture, not company boards
NOT_BOARDS = {
    "embed", "api", "static", "assets", "favicon.ico", "robots.txt", "sitemap.xml", "_next", "v1", "v2",
    "jobs", "j", "careers", "login", "signin", "sign-in", "signup", "privacy", "terms", "about", "help",
    "search", "company", "companies", "jobs.rss", "feed", "u", "new", "docs", "support", "blog", "health",
    "oauth", "auth", "images", "img", "js", "css", "fonts", "widget", "app", "apply", "board", "boards",
}
SLUG_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")

# Per-host concurrency while validating (kept modest on purpose)
WORKERS = {"greenhouse": 6, "lever": 4, "ashby": 5, "workable": 5}
TIMEOUT = 25
MAX_WAIT = 120  # seconds; a longer Retry-After means "go away", so we stop instead of waiting
# Titles that say "design" but mean hardware, civil, chip or other non-UX work
NON_UX_WORDS = re.compile(
    r"asic|rtl|fpga|dsp|analog|\brf\b|verification|mechanical|electrical|civil|roadway|structural|harness|piping|hvac|"
    r"architect|interior|instructional|silicon|layout|\bic design|semiconductor|filter|antenna|firmware|embedded|"
    r"manufactur|process design|plant|substation|electronics|pcb|circuit|hardware|aerospace|propulsion|avionics|"
    r"configuration design|tooling|mold|packaging design engineer|drafter|cad\b",
    re.I,
)
STRICT_CATS = ("product_design", "ux_research", "design_management", "brand_visual")  # UX Engineering excluded: too mixed
AGENCY_WORDS = re.compile(r"recruit|staffing|talent|search|agency|headhunt|manpower|placement|consult", re.I)


# --- small HTTP helper with polite backoff ---------------------------------------


def http_get(url, params=None, retries=4):
    """GET with backoff on 429/5xx/network errors. Returns (status, response|None)."""
    delay = 3
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            r = None
        if r is not None and r.status_code not in (429, 500, 502, 503, 504):
            return r.status_code, r
        retry_after = None
        if r is not None and r.headers.get("Retry-After", "").isdigit():
            retry_after = int(r.headers["Retry-After"])
            if retry_after > MAX_WAIT:
                # The server asked us to stay away for a long time (Workable's search once
                # said ~23 hours). Don't sit and wait: stop, and let the caller report it.
                print(f"  rate-limited (HTTP {r.status_code}), asked to wait {retry_after / 3600:.1f}h - not retrying {url.split('?')[0]}", flush=True)
                return r.status_code, None
        if attempt < retries - 1:
            time.sleep(retry_after or delay)
            delay *= 2
    return (r.status_code if r is not None else 0), None


# --- 1. harvest ------------------------------------------------------------------


def latest_crawls(n):
    status, r = http_get("https://index.commoncrawl.org/collinfo.json")
    if not r:
        sys.exit(f"Couldn't list Common Crawl crawls (HTTP {status}).")
    return [c["id"] for c in r.json()[:n]]


def harvest(n_crawls):
    os.makedirs(WORKDIR, exist_ok=True)
    crawls = latest_crawls(n_crawls)
    print(f"Harvesting from {len(crawls)} crawl(s): {', '.join(crawls)}")
    found = defaultdict(dict)  # ats -> {lowercase name: name as seen}
    for ats, (patterns, rx) in SOURCES.items():
        before = 0
        for crawl in crawls:
            for pattern in patterns:
                base = f"https://index.commoncrawl.org/{crawl}-index"
                status, r = http_get(base, {"url": pattern, "output": "json", "showNumPages": "true"})
                pages = r.json().get("pages") if r else None
                if not pages:
                    print(f"  {ats:10} {crawl} {pattern}: no index (HTTP {status})")
                    continue
                got_pages = 0
                for page in range(pages):
                    status, r = http_get(base, {"url": pattern, "output": "json", "fl": "url", "page": page})
                    if not r:
                        print(f"  {ats:10} {crawl} {pattern} page {page}/{pages}: failed (HTTP {status})")
                        continue
                    got_pages += 1
                    for line in r.text.splitlines():
                        try:
                            url = json.loads(line)["url"]
                        except (ValueError, KeyError):
                            continue
                        m = rx.search(url)
                        if not m:
                            continue
                        name = urllib.parse.unquote(m.group(1))
                        if name.lower() in NOT_BOARDS or not SLUG_OK.match(name):
                            continue
                        found[ats].setdefault(name.lower(), name)
                    time.sleep(1)  # be gentle with a free public service
                print(f"  {ats:10} {crawl} {pattern}: {got_pages}/{pages} pages -> {len(found[ats]):,} distinct so far")
        print(f"{ats}: {len(found[ats]):,} candidate boards\n")
    json.dump({a: sorted(v.values(), key=str.lower) for a, v in found.items()}, open(TOKENS_PATH, "w"))
    print(f"Saved candidates -> {os.path.relpath(TOKENS_PATH, ROOT)}")


# --- 2. validate -----------------------------------------------------------------


KEEP_TITLES = 5  # design titles kept per board; `enrich` raises this to keep them all


def _titles_result(ats, name, titles, nbytes, board_name=None):
    design = [t for t in titles if is_design_role(t or "")]
    return {
        "ats": ats, "token": name, "ok": len(titles) > 0, "total": len(titles), "design": len(design),
        "design_titles": design[:KEEP_TITLES], "name": board_name, "bytes": nbytes,
    }


def validate_one(ats, name):
    """Ask the real ATS about one board. Never raises; returns a result dict."""
    try:
        if ats == "greenhouse":
            status, r = http_get(f"https://boards-api.greenhouse.io/v1/boards/{name}/jobs")  # no content=true: titles only
            if status == 404 or not r:
                return {"ats": ats, "token": name, "ok": False, "status": status}
            titles = [j.get("title") for j in r.json().get("jobs", [])]
            res = _titles_result(ats, name, titles, len(r.content))
            if res["ok"]:
                s2, r2 = http_get(f"https://boards-api.greenhouse.io/v1/boards/{name}")
                if r2:
                    res["name"] = r2.json().get("name")
            return res
        if ats == "lever":
            status, r = http_get(f"https://api.lever.co/v0/postings/{name}", {"mode": "json"})
            if status == 404 or not r:
                return {"ats": ats, "token": name, "ok": False, "status": status}
            data = r.json()
            titles = [j.get("text") for j in data] if isinstance(data, list) else []
            return _titles_result(ats, name, titles, len(r.content))
        if ats == "ashby":
            status, r = http_get(f"https://api.ashbyhq.com/posting-api/job-board/{name}")
            if status == 404 or not r:
                return {"ats": ats, "token": name, "ok": False, "status": status}
            titles = [j.get("title") for j in r.json().get("jobs", [])]
            return _titles_result(ats, name, titles, len(r.content))
        if ats == "workable":
            status, r = http_get(f"https://apply.workable.com/api/v1/widget/accounts/{name}")
            if status == 404 or not r:
                return {"ats": ats, "token": name, "ok": False, "status": status}
            data = r.json()
            titles = [j.get("title") for j in data.get("jobs", [])]
            return _titles_result(ats, name, titles, len(r.content), data.get("name"))
    except Exception as e:  # a malformed response is just a failed probe
        return {"ats": ats, "token": name, "ok": False, "status": f"error:{type(e).__name__}"}
    return {"ats": ats, "token": name, "ok": False, "status": "unsupported"}


def load_results():
    done = {}
    if os.path.exists(RESULTS_PATH):
        for line in open(RESULTS_PATH):
            try:
                r = json.loads(line)
                done[(r["ats"], r["token"].lower())] = r
            except ValueError:
                pass
    return done


def validate(limit):
    if not os.path.exists(TOKENS_PATH):
        sys.exit("Run `harvest` first.")
    candidates = json.load(open(TOKENS_PATH))
    done = load_results()
    out = open(RESULTS_PATH, "a")
    lock = threading.Lock()
    t0 = time.time()
    for ats, names in candidates.items():
        todo = [n for n in names if (ats, n.lower()) not in done][: limit or None]
        print(f"{ats}: {len(names):,} candidates, {len(names) - len([n for n in names if (ats, n.lower()) not in done]):,} already checked, checking {len(todo):,}")
        counter = Counter()

        def work(name):
            res = validate_one(ats, name)
            with lock:
                out.write(json.dumps(res) + "\n")
                out.flush()
                counter["done"] += 1
                counter["ok"] += 1 if res.get("ok") else 0
                if counter["done"] % 250 == 0:
                    print(f"  {ats}: {counter['done']:,}/{len(todo):,} checked, {counter['ok']:,} valid  ({time.time() - t0:.0f}s)", flush=True)

        with ThreadPoolExecutor(max_workers=WORKERS[ats]) as pool:
            list(pool.map(work, todo))
        print(f"  {ats}: finished, {counter['ok']:,} valid of {len(todo):,}\n", flush=True)
    out.close()
    print(f"Done in {time.time() - t0:.0f}s -> {os.path.relpath(RESULTS_PATH, ROOT)}")


ENRICHED_PATH = os.path.join(WORKDIR, "enriched.jsonl")


def enrich():
    """Re-fetch only the boards that have design-title matches and keep ALL their design titles,
    so the report can apply the dashboard's real design categories (not just the loose title filter)."""
    global KEEP_TITLES
    KEEP_TITLES = 400
    done = {}
    if os.path.exists(ENRICHED_PATH):
        for line in open(ENRICHED_PATH):
            try:
                r = json.loads(line); done[(r["ats"], r["token"].lower())] = r
            except ValueError:
                pass
    todo = [r for r in load_results().values() if r.get("ok") and r["design"] > 0 and (r["ats"], r["token"].lower()) not in done]
    print(f"enriching {len(todo):,} boards with design-title matches ({len(done):,} already done)")
    out, lock, n = open(ENRICHED_PATH, "a"), threading.Lock(), [0]

    def work(r):
        res = validate_one(r["ats"], r["token"])
        with lock:
            out.write(json.dumps(res) + "\n"); out.flush(); n[0] += 1
            if n[0] % 200 == 0:
                print(f"  {n[0]:,}/{len(todo):,}", flush=True)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(work, todo))
    print("enrich done")


# --- 3. Workable cross-company search ---------------------------------------------

WORKABLE_QUERIES = ["designer", "product designer", "ux designer", "ui designer", "ux researcher", "user research",
                    "visual designer", "brand designer", "graphic designer", "design lead", "design manager",
                    "design director", "interaction designer", "service designer", "content designer"]


def workable_search(sample_pages=15):
    """Size Workable's cross-company search without hammering it: one request per query for the
    reported total, then a modest sample of the broad query to see how many of its employers we
    already found through board discovery. Saved after every step."""
    os.makedirs(WORKDIR, exist_ok=True)
    out = {"totals": {}, "sample_jobs": 0, "sample_companies": 0}
    for q in WORKABLE_QUERIES:
        status, r = http_get("https://jobs.workable.com/api/v1/jobs", {"query": q, "limit": 1})
        out["totals"][q] = r.json().get("totalSize") if r else f"HTTP {status}"
        print(f"  '{q}': {out['totals'][q]} jobs reported", flush=True)
        json.dump(out, open(WORKABLE_PATH, "w"))
        time.sleep(1.5)
    jobs, token = {}, None
    for page in range(sample_pages):
        params = {"query": "designer", "limit": 20}
        if token:
            params["pageToken"] = token
        status, r = http_get("https://jobs.workable.com/api/v1/jobs", params)
        if not r:
            print(f"  sample stopped at page {page} (HTTP {status})")
            break
        d = r.json()
        for j in d.get("jobs", []):
            jobs[j.get("id") or j.get("url")] = j
        token = d.get("nextPageToken")
        if not token:
            break
        time.sleep(1.5)
    companies = Counter(((j.get("company") or {}).get("title") or "?") for j in jobs.values())
    out.update(sample_jobs=len(jobs), sample_companies=len(companies), top_companies=companies.most_common(15),
               sample_titles=[j.get("title") for j in list(jobs.values())[:40]])
    json.dump(out, open(WORKABLE_PATH, "w"))
    print(f"sample of 'designer': {len(jobs)} jobs from {len(companies)} companies")


# --- 4. report -------------------------------------------------------------------


def report():
    results = load_results()
    if not results:
        sys.exit("Nothing validated yet.")
    candidates = json.load(open(TOKENS_PATH)) if os.path.exists(TOKENS_PATH) else {}
    known = set()
    try:  # compare to the directory (read-only)
        from dotenv import load_dotenv

        load_dotenv(os.path.join(ROOT, ".env.local"))
        from db.db import get_conn

        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT ats_type, lower(ats_identifier) FROM companies")
            known = {(a, i) for a, i in cur.fetchall()}
        conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"(couldn't read the directory to compare: {type(e).__name__})")

    print(f"{'ATS':10} {'candidates':>10} {'checked':>8} {'valid':>7} {'w/ design':>10} {'open design':>12} {'all open roles':>15} {'new vs 140':>11}")
    tot = Counter()
    by_ats = defaultdict(list)
    for (ats, token), r in results.items():
        by_ats[ats].append(r)
    for ats in ("greenhouse", "lever", "ashby", "workable"):
        rs = by_ats.get(ats, [])
        valid = [r for r in rs if r.get("ok")]
        wd = [r for r in valid if r["design"] > 0]
        if ats == "workable":  # staffing agencies list other employers' jobs; flag them
            agencies = [r for r in valid if r["total"] >= 1000 or AGENCY_WORDS.search(r.get("name") or "")]
        else:
            agencies = []
        new = [r for r in wd if (ats, r["token"].lower()) not in known]
        print(f"{ats:10} {len(candidates.get(ats, [])):>10,} {len(rs):>8,} {len(valid):>7,} {len(wd):>10,} "
              f"{sum(r['design'] for r in valid):>12,} {sum(r['total'] for r in valid):>15,} {len(new):>11,}"
              + (f"   ({len(agencies)} look like agencies)" if agencies else ""))
        tot["checked"] += len(rs); tot["valid"] += len(valid); tot["wd"] += len(wd)
        tot["design"] += sum(r["design"] for r in valid); tot["roles"] += sum(r["total"] for r in valid); tot["new"] += len(new)
    print(f"{'TOTAL':10} {sum(len(v) for v in candidates.values()):>10,} {tot['checked']:>8,} {tot['valid']:>7,} {tot['wd']:>10,} "
          f"{tot['design']:>12,} {tot['roles']:>15,} {tot['new']:>11,}")
    ok = [r for r in results.values() if r.get("ok")]
    print(f"\nWhy checks failed (of {len(results) - len(ok):,} not valid):", dict(Counter(str(r.get('status', 'no roles')) for r in results.values() if not r.get('ok')).most_common(6)))
    sizes = [r["bytes"] for r in ok]
    if sizes:
        print(f"Payload per valid board: median {sorted(sizes)[len(sizes) // 2] / 1024:.0f} KB, mean {sum(sizes) / len(sizes) / 1024:.0f} KB, "
              f"total {sum(sizes) / 1e6:.0f} MB per full pass")
    print("\nBoards with the most open design roles:")
    for r in sorted((r for r in ok), key=lambda r: -r["design"])[:12]:
        print(f"  {r['design']:4} design / {r['total']:5} roles  {r['ats']}:{r['token']}  {r.get('name') or ''}  e.g. {r['design_titles'][:1]}")
    core_report(ok, known)
    if os.path.exists(WORKABLE_PATH):
        w = json.load(open(WORKABLE_PATH))
        print("\nWorkable cross-company search (reported totals):", {q: t for q, t in w.get("totals", {}).items() if q in ("designer", "product designer", "ux designer", "ui designer", "ux researcher")})
        if w.get("sample_jobs"):
            print(f"  sample of 'designer': {w['sample_jobs']} jobs from {w['sample_companies']} companies; top: "
                  + ", ".join(f"{c} ({n})" for c, n in w.get("top_companies", [])[:6]))


def core_report(ok, known):
    """The loose title filter ("design"/"designer") also catches engineering, architecture and
    instructional roles. Re-score with the five design categories the dashboard offers
    (role_categories.ROLE_CATEGORIES) using the full design-title lists from `enrich`."""
    if not os.path.exists(ENRICHED_PATH):
        return
    from role_categories import ROLE_CATEGORIES, matches_categories

    enriched = {}
    for line in open(ENRICHED_PATH):
        try:
            r = json.loads(line); enriched[(r["ats"], r["token"].lower())] = r
        except ValueError:
            pass
    cats = list(ROLE_CATEGORIES)
    rows = []
    for (ats, tok), r in enriched.items():
        if not r.get("ok"):
            continue
        core = [t for t in r["design_titles"] if matches_categories(t or "", set(cats))]
        by_cat = Counter()
        for t in core:
            for c in cats:
                if matches_categories(t, {c}):
                    by_cat[c] += 1
        agency = (ats == "workable" and (r["total"] >= 1000 or AGENCY_WORDS.search(r.get("name") or ""))) or bool(AGENCY_WORDS.search(r.get("name") or ""))
        rows.append({**r, "core": len(core), "by_cat": by_cat, "agency": agency, "known": (ats, tok) in known})
    def is_strict(t):
        return bool(t) and matches_categories(t, set(STRICT_CATS)) and not NON_UX_WORDS.search(t)

    for r in rows:
        r["strict"] = sum(1 for t in r["design_titles"] if is_strict(t))
    with_core = [r for r in rows if r["core"] > 0]
    print("\n=== Core design roles (the dashboard's 5 categories, applied to every title) ===")
    print(f"{'ATS':10} {'boards w/ core design':>22} {'open core design roles':>23} {'of those, new vs 140':>21} {'agency-like boards':>19}")
    t = Counter()
    for ats in ("greenhouse", "lever", "ashby", "workable"):
        rs = [r for r in with_core if r["ats"] == ats]
        new = [r for r in rs if not r["known"]]
        ag = [r for r in rs if r["agency"]]
        print(f"{ats:10} {len(rs):>22,} {sum(r['core'] for r in rs):>23,} {sum(r['core'] for r in new):>21,} {len(ag):>19,}")
        t["b"] += len(rs); t["r"] += sum(r["core"] for r in rs); t["n"] += sum(r["core"] for r in new); t["a"] += len(ag)
    print(f"{'TOTAL':10} {t['b']:>22,} {t['r']:>23,} {t['n']:>21,} {t['a']:>19,}")
    strict_rows = [r for r in rows if r["strict"] > 0]
    print("\n=== STRICT: product / UX / brand / design-management roles, hardware & civil 'design' titles removed ===")
    print(f"{'ATS':10} {'boards':>8} {'open roles':>11} {'new vs 140':>11} {'new, non-agency':>16}")
    ts = Counter()
    for ats in ("greenhouse", "lever", "ashby", "workable"):
        rs = [r for r in strict_rows if r["ats"] == ats]
        new = [r for r in rs if not r["known"]]
        na = [r for r in new if not r["agency"]]
        print(f"{ats:10} {len(rs):>8,} {sum(r['strict'] for r in rs):>11,} {sum(r['strict'] for r in new):>11,} {sum(r['strict'] for r in na):>16,}")
        ts["b"] += len(rs); ts["r"] += sum(r["strict"] for r in rs); ts["n"] += sum(r["strict"] for r in new); ts["na"] += sum(r["strict"] for r in na)
    print(f"{'TOTAL':10} {ts['b']:>8,} {ts['r']:>11,} {ts['n']:>11,} {ts['na']:>16,}")
    try:  # the same strict measure on the roles Scout already stores, for a like-for-like comparison
        from dotenv import load_dotenv

        load_dotenv(os.path.join(ROOT, ".env.local"))
        from db.db import get_conn

        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT title FROM postings WHERE status = 'open' AND is_design")
            ours = [r[0] for r in cur.fetchall()]
        conn.close()
        mine = sum(1 for t in ours if is_strict(t))
        print(f"Same strict measure on today's 140 companies: {mine:,} open roles  ->  discovery would add about {ts['na'] / max(mine, 1):.1f}x that")
    except Exception as e:  # noqa: BLE001
        print(f"(couldn't read today's roles: {type(e).__name__})")
    print("Strict examples (random-ish sample of new, non-agency):")
    shown = 0
    for r in sorted(strict_rows, key=lambda r: r["token"]):
        if r["known"] or r["agency"]:
            continue
        t = next((x for x in r["design_titles"] if is_strict(x)), None)
        if t and hash(r["token"]) % 97 == 0:
            print(f"   {r['ats']}:{r['token']:<24} {t[:70]}")
            shown += 1
        if shown >= 12:
            break
    noagency = sum(r["core"] for r in with_core if not r["agency"] and not r["known"])
    print(f"\nNew core design roles excluding agency-like boards: {noagency:,}")
    cat_tot = Counter()
    for r in with_core:
        cat_tot.update(r["by_cat"])
    print("By category (a title can match more than one):", {ROLE_CATEGORIES[c]["label"]: cat_tot[c] for c in cats})
    sizes = sorted(r["core"] for r in with_core)
    print(f"Core roles per board that has any: median {sizes[len(sizes) // 2]}, 90th pct {sizes[int(len(sizes) * 0.9)]}, max {sizes[-1]}")
    print("\nTop boards by core design roles:")
    for r in sorted(with_core, key=lambda r: -r["core"])[:12]:
        ex = next((x for x in r["design_titles"] if matches_categories(x or "", set(cats))), "")
        print(f"  {r['core']:3} {r['ats']}:{r['token']:<26} {'(agency-like) ' if r['agency'] else ''}{('NEW ' if not r['known'] else '')}e.g. {ex[:52]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["harvest", "validate", "enrich", "workable-search", "report"])
    ap.add_argument("--crawls", type=int, default=3, help="how many recent Common Crawl crawls to read")
    ap.add_argument("--limit", type=int, default=0, help="validate at most N boards per ATS (for a trial run)")
    args = ap.parse_args()
    {"harvest": lambda: harvest(args.crawls), "validate": lambda: validate(args.limit),
     "enrich": enrich, "workable-search": workable_search, "report": report}[args.step]()


if __name__ == "__main__":
    main()
