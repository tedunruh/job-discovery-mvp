import time

import requests

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 1.0
MAX_RETRY_AFTER_SECONDS = 30.0  # never wait longer than this for one retry, whatever the server asks
RETRYABLE_STATUS_CODES = {429, 403, 500, 502, 503, 504}


def retry_delay(resp, attempt):
    """How long to wait before the next try: the server's Retry-After (seconds), capped, when it sends
    one; otherwise exponential backoff."""
    header = resp.headers.get("Retry-After") if getattr(resp, "headers", None) else None
    if header:
        try:
            return max(0.0, min(float(header), MAX_RETRY_AFTER_SECONDS))
        except ValueError:
            pass  # a date form of Retry-After: fall back to backoff
    return BACKOFF_SECONDS * (2**attempt)


def request_with_retries(method, url, max_attempts=MAX_ATTEMPTS, **kwargs):
    """requests.request() with exponential backoff for transient failures
    (429/403/5xx, timeouts, connection errors) within a single cycle. Honors Retry-After (capped).
    Raises the last error once attempts are exhausted, for the caller's
    cross-cycle health tracking to pick up.
    """
    last_exc = None
    for attempt in range(max_attempts):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code in RETRYABLE_STATUS_CODES:
                last_exc = requests.HTTPError(f"{resp.status_code} from {url}")
                if attempt < max_attempts - 1:
                    time.sleep(retry_delay(resp, attempt))
                    continue
                resp.raise_for_status()
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            if attempt < max_attempts - 1:
                time.sleep(BACKOFF_SECONDS * (2**attempt))
                continue
    raise last_exc
