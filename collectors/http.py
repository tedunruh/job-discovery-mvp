import time

import requests

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 1.0
RETRYABLE_STATUS_CODES = {429, 403, 500, 502, 503, 504}


def request_with_retries(method, url, max_attempts=MAX_ATTEMPTS, **kwargs):
    """requests.request() with exponential backoff for transient failures
    (429/403/5xx, timeouts, connection errors) within a single cycle.
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
                    time.sleep(BACKOFF_SECONDS * (2**attempt))
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
