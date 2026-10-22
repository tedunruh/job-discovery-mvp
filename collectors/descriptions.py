"""Job descriptions as plain text (SC-51).

Matching a resume needs the description, not just the title. Two platforms already put it in the list
response Scout downloads anyway (Ashby, Lever), so nothing extra is fetched. For the rest (Greenhouse,
Workable, Rippling, Workday) it takes one request per role, so those go through a FetchBudget: a cap per run
and a stop after a few rate-limit answers, because Workable once refused nearly every request after a burst
(SC-61). Nothing here talks to the database; db.set_posting_description stores what this returns.

    text = fetch_description("greenhouse", "acme", posting, budget)   # None when skipped or not available
"""
import re
import threading
from html import unescape
from html.parser import HTMLParser

import requests

from collectors.http import request_with_retries

MAX_CHARS = 20000
TIMEOUT = 20
ATTEMPTS = 2  # fewer than the board scans: a description can wait for the next run

GREENHOUSE_JOB = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{id}"
WORKABLE_JOB = "https://apply.workable.com/api/v2/accounts/{account}/jobs/{shortcode}"
RIPPLING_JOB = "https://ats.rippling.com/api/v2/board/{slug}/jobs/{id}"
WORKDAY_JOB = "https://{tenant}.{instance}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{path}"

PER_JOB_PLATFORMS = ("greenhouse", "workable", "rippling", "workday")
IN_LIST_PLATFORMS = ("ashby", "lever")


class Skipped(Exception):
    """No request was made: this platform is rate-limited for the rest of the run, or the run's budget is spent."""


# ---------------------------------------------------------------- HTML to plain text

_BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}
_SKIP = {"script", "style", "head"}


class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag == "li":
            pass  # the next item's own "- " starts the next line; a newline here would add a blank one
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html, limit=MAX_CHARS):
    """Readable plain text from a job description's HTML: tags gone, list items as "- " lines, blank lines
    collapsed, entities decoded, at most `limit` characters. Greenhouse sends its HTML entity-escaped
    ("&lt;p&gt;"), which is decoded first."""
    if not html:
        return ""
    s = str(html)
    if "&lt;" in s and "<" not in s:  # escaped markup, not real markup
        s = unescape(s)
    parser = _Stripper()
    parser.feed(s)
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ")
    text = re.sub(r"(?m)^[ \t]*-[ \t]*\n+(?=[ \t]*\S)", "- ", text)  # <li><p>text</p></li>: keep the bullet with its text
    text = re.sub(r"(?m)^(- [^\n]*)\n[ \t]*\n+(?=[ \t]*- )", r"\1\n", text)  # no blank lines between bullets
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    out, blank = [], False
    for line in lines:
        if not line:
            blank = True
            continue
        if blank and out:
            out.append("")
        blank = False
        out.append(line)
    return "\n".join(out)[:limit].rstrip()


def tidy_text(text, limit=MAX_CHARS):
    """Plain text a platform already sent (Ashby, Lever): trailing spaces dropped, runs of blank lines
    collapsed to one, at most `limit` characters."""
    if not text:
        return ""
    s = str(text).replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    s = "\n".join(line.rstrip() for line in s.splitlines())
    return re.sub(r"\n{3,}", "\n\n", s).strip()[:limit].rstrip()


# ---------------------------------------------------------------- already in the list response

def from_list_payload(ats_type, raw):
    """The description out of a posting's list-response payload (Ashby, Lever), or None."""
    if not isinstance(raw, dict):
        return None
    if ats_type == "ashby":
        text = tidy_text(raw.get("descriptionPlain")) or html_to_text(raw.get("descriptionHtml"))
        return text or None
    if ats_type == "lever":
        parts = [tidy_text(raw.get("descriptionPlain")) or html_to_text(raw.get("description"))]
        for block in raw.get("lists") or []:
            if isinstance(block, dict):
                heading = (block.get("text") or "").strip()
                body = html_to_text(block.get("content"))
                parts.append("\n".join(p for p in (heading, body) if p))
        parts.append(tidy_text(raw.get("additionalPlain")) or html_to_text(raw.get("additional")))
        text = "\n\n".join(p.strip() for p in parts if p and p.strip())
        return text[:MAX_CHARS].rstrip() or None
    return None


# ---------------------------------------------------------------- the per-run budget

class FetchBudget:
    """How many description requests a run may make, and when to stop asking a platform.

    max_requests caps the whole run. After `stop_after_rate_limits` rate-limit answers (429 or 403) from one
    platform it is not asked again this run; those roles are simply retried next run. Safe to share
    between worker threads."""

    def __init__(self, max_requests=300, stop_after_rate_limits=5):
        self.max_requests = max_requests
        self.stop_after = stop_after_rate_limits
        self._lock = threading.Lock()
        self.requests = 0
        self.rate_limited = {}
        self.failures = {}

    def reserve(self, ats_type):
        """Count one request against the budget, or raise Skipped."""
        with self._lock:
            if self.rate_limited.get(ats_type, 0) >= self.stop_after:
                raise Skipped(f"{ats_type} is rate-limiting us; not asking again this run")
            if self.requests >= self.max_requests:
                raise Skipped("this run's description budget is spent")
            self.requests += 1

    def record_rate_limit(self, ats_type):
        with self._lock:
            self.rate_limited[ats_type] = self.rate_limited.get(ats_type, 0) + 1

    def record_failure(self, ats_type):
        with self._lock:
            self.failures[ats_type] = self.failures.get(ats_type, 0) + 1

    def blocked(self):
        """Platforms that stopped being asked this run, for the health alert."""
        with self._lock:
            return sorted(a for a, n in self.rate_limited.items() if n >= self.stop_after)


# ---------------------------------------------------------------- one request per role

def _workday_url(identifier, path):
    host_prefix, site = identifier.split("/", 1)
    tenant, instance = host_prefix.split(".", 1)
    return WORKDAY_JOB.format(tenant=tenant, instance=instance, site=site, path=path)


def _parse(ats_type, data):
    if ats_type == "greenhouse":
        return html_to_text(data.get("content"))
    if ats_type == "workable":
        parts = [html_to_text(data.get(k)) for k in ("description", "requirements", "benefits")]
        return "\n\n".join(p for p in parts if p)
    if ats_type == "rippling":
        d = data.get("description")
        if isinstance(d, dict):  # the role first, then the company blurb
            return "\n\n".join(p for p in (html_to_text(d.get("role")), html_to_text(d.get("company"))) if p)
        return html_to_text(d)
    if ats_type == "workday":
        return html_to_text((data.get("jobPostingInfo") or {}).get("jobDescription"))
    return ""


def _url(ats_type, identifier, posting_id):
    if ats_type == "greenhouse":
        return GREENHOUSE_JOB.format(token=identifier, id=posting_id)
    if ats_type == "workable":
        return WORKABLE_JOB.format(account=identifier, shortcode=posting_id)
    if ats_type == "rippling":
        return RIPPLING_JOB.format(slug=identifier, id=posting_id)
    if ats_type == "workday":
        return _workday_url(identifier, posting_id)  # a Workday posting id is its externalPath
    raise ValueError(f"no per-job description endpoint for {ats_type}")


def fetch_description(ats_type, identifier, posting, budget=None):
    """The description of one posting as plain text, or None if it has none.

    Ashby and Lever come from the payload already downloaded (posting["raw_json"]); no request is made.
    Greenhouse, Workable, Rippling and Workday make one request, counted against `budget`.
    Raises Skipped when the budget says not to ask, and lets other request errors through after recording
    them: the caller leaves that role for the next run."""
    if ats_type in IN_LIST_PLATFORMS:
        return from_list_payload(ats_type, posting.get("raw_json"))
    if ats_type not in PER_JOB_PLATFORMS:
        return None
    budget = budget or FetchBudget()
    budget.reserve(ats_type)
    url = _url(ats_type, identifier, posting["ats_posting_id"])
    try:
        method = "GET"
        resp = request_with_retries(method, url, max_attempts=ATTEMPTS, timeout=TIMEOUT)
        data = resp.json()
    except requests.HTTPError as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (429, 403):
            budget.record_rate_limit(ats_type)
        elif status == 404:
            return None  # the role is gone; not an error worth counting
        else:
            budget.record_failure(ats_type)
        raise
    except (requests.RequestException, ValueError):
        budget.record_failure(ats_type)
        raise
    text = _parse(ats_type, data if isinstance(data, dict) else {})
    return text[:MAX_CHARS].strip() or None
