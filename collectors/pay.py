"""Pay ranges for postings (SC-71). Pure functions, so every rule can be tested without a network or a database.

A pay range is {"min", "max", "currency", "period", "source"}: min and max are numbers (either may be None: "from $X" or
"up to $X"), currency is an ISO code ("USD"), period is year, month, week, day or hour, and source says where it came
from: "board" (the job board gave it as data) or "text" (it was read from the posting's description).

Boards that give pay as data: Ashby (compensation, with ?includeCompensation=true), Greenhouse (pay_input_ranges, with
?pay_transparency=true) and Lever (salaryRange). Everywhere else, and as a fallback, pay is read from the description
text, conservatively: a range must sit next to words about pay (salary, compensation, pay range, base pay, wage...) and
away from words about money that is not pay (funding, raised, valuation, revenue, a bonus). It is better to show no pay
than the wrong pay: a posting with no pay found shows a blank line.
"""
import re

PERIODS = ("year", "month", "week", "day", "hour")
# What a believable pay figure looks like, per period (any currency: a Japanese salary is a big number, so the bounds are wide).
BOUNDS = {"year": (1_000, 100_000_000), "month": (100, 2_000_000), "week": (50, 200_000), "day": (10, 20_000), "hour": (3, 5_000)}
_ASHBY_PERIOD = {"1 YEAR": "year", "1 MONTH": "month", "1 WEEK": "week", "1 DAY": "day", "1 HOUR": "hour"}
_LEVER_PERIOD = {"per-year-salary": "year", "per-month-salary": "month", "per-week-salary": "week", "per-day-wage": "day", "per-hour-wage": "hour"}


def make(low, high, currency, period, source):
    """A pay range, or None when it is not believable (nothing there, backwards, an unknown period, an absurd size)."""
    if period not in PERIODS:
        return None
    nums = [float(n) for n in (low, high) if n is not None]
    if not nums or any(n <= 0 for n in nums):
        return None
    if low is not None and high is not None and float(low) > float(high):
        return None
    floor, ceiling = BOUNDS[period]
    if any(n < floor or n > ceiling for n in nums):
        return None
    cur = (currency or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", cur):
        return None
    clean = lambda n: None if n is None else (int(n) if float(n) == int(float(n)) else round(float(n), 2))
    return {"min": clean(low), "max": clean(high), "currency": cur, "period": period, "source": source}


# ---- boards that give pay as data ---------------------------------------------------------------------------------

def from_ashby(job):
    """Ashby's compensation.summaryComponents: the one whose type is Salary (bonus, commission and equity are not pay)."""
    for part in ((job.get("compensation") or {}).get("summaryComponents") or []):
        if part.get("compensationType") == "Salary":
            found = make(part.get("minValue"), part.get("maxValue"), part.get("currencyCode"), _ASHBY_PERIOD.get(part.get("interval")), "board")
            if found:
                return found
    return None


def from_greenhouse(job):
    """Greenhouse's pay_input_ranges (amounts in cents). A title like "Hourly rate" says hourly; otherwise annual."""
    for rng in (job.get("pay_input_ranges") or []):
        low, high = rng.get("min_cents"), rng.get("max_cents")
        low = None if low is None else low / 100
        high = None if high is None else high / 100
        title = (rng.get("title") or "").lower()
        period = "hour" if re.search(r"\bhour", title) else "month" if re.search(r"\bmonth", title) else "year"
        found = make(low, high, rng.get("currency_type"), period, "board")
        if found:
            return found
    return None


def from_lever(posting):
    """Lever's salaryRange: {min, max, currency, interval}."""
    rng = posting.get("salaryRange") or {}
    return make(rng.get("min"), rng.get("max"), rng.get("currency"), _LEVER_PERIOD.get(rng.get("interval")), "board")


BOARD_PARSERS = {"ashby": from_ashby, "greenhouse": from_greenhouse, "lever": from_lever}


def from_board(ats_type, raw):
    """The pay a board gave as data for one raw posting, or None (also for boards that give none)."""
    parser = BOARD_PARSERS.get(ats_type)
    return parser(raw) if parser and isinstance(raw, dict) else None


def attach(ats_type, postings):
    """Read the pay each fetched posting carries as data and put it on the posting as p["pay"] (None when there is none);
    then slim the raw payload. Runs right after a board is fetched, while the raw payload is still there."""
    for p in postings:
        raw = p.get("raw_json")
        p["pay"] = from_board(ats_type, raw)
        p["raw_json"] = slim(ats_type, raw)


def slim(ats_type, raw):
    """The raw posting without the bulky parts that only carry pay (Greenhouse's per-range HTML blurb), once pay has been
    read from it, so storing it does not grow the database."""
    if ats_type == "greenhouse" and isinstance(raw, dict) and raw.get("pay_input_ranges"):
        raw = dict(raw)
        raw["pay_input_ranges"] = [{k: v for k, v in r.items() if k != "blurb"} for r in raw["pay_input_ranges"]]
    return raw


# ---- pay read from the description text -----------------------------------------------------------------------------

_CODES = ("USD", "CAD", "GBP", "EUR", "AUD", "NZD", "CHF", "SEK", "NOK", "DKK", "SGD", "INR", "PLN", "JPY", "BRL", "MXN")
_MONEY_PREFIX = r"(?:[$£€]|(?:US|CA|C|A|NZ)\$)"
_RANGE = re.compile(
    rf"(?P<a>{_MONEY_PREFIX}\s?\d[\d,]*(?:\.\d{{1,2}})?\s?[kK]?)"
    r"(?:\s+(?:USD|CAD|GBP|EUR|AUD|NZD))?"                  # "$117,000 CAD to $143,000 CAD"
    r"\s*(?:-|–|—|to|and)\s*"
    rf"(?P<b>{_MONEY_PREFIX}?\s?\d[\d,]*(?:\.\d{{1,2}})?\s?[kK]?)",
    re.IGNORECASE)
_PAY_WORDS = re.compile(r"\b(salary|salaries|compensation|pay|paid|wage|wages|base|ote|on[- ]target|earnings|rate|remuneration)\b", re.IGNORECASE)
_NOT_PAY = re.compile(r"\b(funding|raised|raise|valuation|revenue|arr|investment|invested|investors?|contracts?|worth|series [a-f]|billion|trillion|equity grant|bonus of|signing bonus|stipend|reimbursement|credit|subsidy|allowance|401)\b", re.IGNORECASE)
# A label right before the figure that says it is some other kind of money ("Equity: $80,000 - $100,000", "bonus target is ...").
_OTHER_MONEY_LABEL = re.compile(r"(equity|stock|rsus?|options?|bonus|commission|incentive|signing)[^.\n]{0,30}$", re.IGNORECASE)
# On-target earnings is a different measure from the base range before it ("base X - Y, with OTE between P - Q").
_OTE_LABEL = re.compile(r"(\bote\b|on[- ]target|target earnings)[^.\n]{0,30}$", re.IGNORECASE)
# "total compensation ... (base salary + equity)": a figure that includes equity is not pay.
_TOTAL_WITH_EQUITY_BEFORE = re.compile(r"total\s+comp\w*[^.\n]{0,40}$", re.IGNORECASE)
_EQUITY_AFTER = re.compile(r"^[^.\n]{0,40}equity", re.IGNORECASE)
_PERIODS_TEXT = [
    (re.compile(r"(/|\bper\b|\ban?\b|\bevery\b)\s*(hour|hr)\b|\bhourly\b|/\s*hr\b", re.IGNORECASE), "hour"),
    (re.compile(r"(/|\bper\b|\ban?\b)\s*(year|yr|annum)\b|\bannual(?:ly)?\b|\byearly\b|/\s*yr\b|\bp\.?a\.?\b", re.IGNORECASE), "year"),
    (re.compile(r"(/|\bper\b|\ban?\b)\s*month\b|\bmonthly\b", re.IGNORECASE), "month"),
    (re.compile(r"(/|\bper\b|\ban?\b)\s*week\b|\bweekly\b", re.IGNORECASE), "week"),
    (re.compile(r"(/|\bper\b|\ban?\b)\s*day\b|\bdaily\b", re.IGNORECASE), "day"),
]
_PERIOD_LABEL_BEFORE = re.compile(r"\b(hourly|monthly|weekly|daily|annual|yearly)\b[^\n.]{0,28}$", re.IGNORECASE)
_LABEL_PERIOD = {"hourly": "hour", "monthly": "month", "weekly": "week", "daily": "day", "annual": "year", "yearly": "year"}


def _number(token):
    digits = re.sub(r"[^\d.]", "", token.lower().replace("k", ""))
    if not digits or digits == ".":
        return None
    value = float(digits)
    return value * 1000 if "k" in token.lower() else value


def _explicit(text):
    for code in _CODES:
        if re.search(rf"\b{code}\b", text):
            return code
    return None


def _currency(a_token, around):
    m = re.search(r"(US|CA|C|A|NZ)\$", a_token, re.IGNORECASE)
    if m:
        return {"US": "USD", "CA": "CAD", "C": "CAD", "A": "AUD", "NZ": "NZD"}[m.group(1).upper()]
    if "\u00a3" in a_token:
        return "GBP"                       # a pound or euro sign is unambiguous: "paid in USD" later in the text is not the pay's currency
    if "\u20ac" in a_token:
        return "EUR"
    if "$" in a_token:
        return _explicit(around) or "USD"  # a dollar sign could be any dollar: a code beside it ("CAD") says which
    return _explicit(around)


def _period(before, after, low):
    """The period a range is paid per: a word right after it ("/ year", "per hour": no other figure in between), else a
    label right before it ("Monthly Salary Range"), else a year only if the figure is big enough to be a year's pay."""
    near = after[:28]
    for rx, period in _PERIODS_TEXT:
        m = rx.search(near)
        if m and not re.search(r"\d", near[:m.start()]):
            return period
    label = _PERIOD_LABEL_BEFORE.search(before[-60:])
    if label:
        return _LABEL_PERIOD[label.group(1).lower()]
    return "year" if low >= 20_000 else None


def from_text(text):
    """Pay read from description text, or None. Every range in the text that sits among words about pay (and not about
    funding, equity, a bonus or on-target earnings) counts; the next zone or city listed right after one counts too. When
    a posting lists several, the pay shown spans all of them, from the lowest low to the highest high, so it never
    claims a figure the posting does not offer."""
    if not text:
        return None
    found = []
    last_end = None
    for m in _RANGE.finditer(text):
        start, end = m.start(), m.end()
        before, after = text[max(0, start - 140):start], text[end:end + 90]
        near_pay_words = _PAY_WORDS.search(before + " " + text[start:end] + " " + after[:40])
        in_a_list = last_end is not None and start - last_end <= 320   # the next zone or city after a range already read
        if not (near_pay_words or in_a_list):
            continue
        if _NOT_PAY.search(text[max(0, start - 70):start]) or _OTHER_MONEY_LABEL.search(text[max(0, start - 45):start]) \
                or (_TOTAL_WITH_EQUITY_BEFORE.search(text[max(0, start - 60):start]) and _EQUITY_AFTER.search(after)) \
                or (found and _OTE_LABEL.search(text[max(0, start - 45):start])):   # a first OTE range is still the pay stated
            continue
        low, high = _number(m.group("a")), _number(m.group("b"))
        if low is None or high is None:
            continue
        if "k" in m.group("b").lower() and "k" not in m.group("a").lower() and low < 1000:
            low *= 1000                                   # "$150 - $180K" means 150K to 180K
        currency = _currency(m.group("a"), text[max(0, start - 25):end + 25])
        made = make(low, high, currency, _period(before, after, low), "text")
        if made:
            found.append(made)
            last_end = end
    if not found:
        return None
    first = found[0]
    same = [f for f in found if f["currency"] == first["currency"] and f["period"] == first["period"]]
    return make(min(f["min"] for f in same), max(f["max"] for f in same), first["currency"], first["period"], "text")
