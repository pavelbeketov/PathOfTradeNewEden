"""ESI client with bounded retries and a shared floating-window limiter."""
import re
import threading
import time
from collections import deque
from email.utils import parsedate_to_datetime
import requests
from trader_common import utcnow

ESI = "https://esi.evetech.net/latest"
USER_AGENT = "TalvoidTrader/2.0 (discord: facearcher)"


def parse_limit(value):
    match = re.fullmatch(r"(\d+)/(\d+)([mh])", value or "")
    if not match:
        return None
    total, n, unit = match.groups()
    return int(total), int(n) * (60 if unit == "m" else 3600)


class RateLimiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.groups = {}
        self.blocked_until = 0

    def acquire(self, group):
        while True:
            with self.lock:
                now = time.monotonic()
                delay = max(0, self.blocked_until - now)
                st = self.groups.get(group)
                if st:
                    while st["events"] and st["events"][0][0] <= now:
                        st["remaining"] = min(st["total"], st["remaining"] + st["events"].popleft()[1])
                    if st["remaining"] < 5:
                        delay = max(delay, st["events"][0][0] - now if st["events"] else st["window"])
                    if delay <= 0:
                        st["remaining"] -= 5
                        st["events"].append((now + st["window"], 5))
                if delay <= 0:
                    return
            time.sleep(min(max(delay, 0.01), 5))

    def update(self, response, group):
        now = time.monotonic()
        with self.lock:
            parsed = parse_limit(response.headers.get("X-Ratelimit-Limit"))
            remaining = response.headers.get("X-Ratelimit-Remaining", "")
            if parsed and remaining.isdigit():
                total, window = parsed
                if total < 5 or window <= 0:
                    raise ValueError("Unexpected ESI rate limit")
                if group not in self.groups:
                    rem = min(total, int(remaining))
                    self.groups[group] = {"total": total, "window": window, "remaining": rem,
                                          "events": deque([(now + window, total - rem)])}
                else:
                    st = self.groups[group]
                    server_rem = int(remaining)
                    if server_rem < st["remaining"]:
                        st["events"].append((now + st["window"], st["remaining"] - server_rem))
                        st["remaining"] = server_rem
            er = response.headers.get("X-ESI-Error-Limit-Remain", "")
            reset = response.headers.get("X-ESI-Error-Limit-Reset", "")
            if er.isdigit() and reset.isdigit() and int(er) <= 5:
                self.blocked_until = max(self.blocked_until, now + int(reset) + 1)

    def block(self, seconds):
        with self.lock:
            self.blocked_until = max(self.blocked_until, time.monotonic() + seconds)


class ESIClient:
    def __init__(self, settings):
        self.settings = settings
        self.local = threading.local()
        self.limiter = RateLimiter()

    def session(self):
        if not hasattr(self.local, "session"):
            self.local.session = requests.Session()
            self.local.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        return self.local.session

    def get(self, path, params=None, headers=None, group="market-order"):
        retries = self.settings["http_retries"]
        for attempt in range(retries):
            self.limiter.acquire(group)
            try:
                r = self.session().get(f"{ESI}/{path.lstrip('/')}", params=params,
                                       headers=headers, timeout=self.settings["http_timeout_seconds"])
            except requests.RequestException as exc:
                if attempt + 1 == retries:
                    raise RuntimeError(f"ESI: network failure after {retries} attempts") from exc
                time.sleep(min(2 ** (attempt + 1), 30))
                continue
            self.limiter.update(r, group)
            if r.status_code in (420, 429) or 500 <= r.status_code < 600:
                delay = min(2 ** (attempt + 1), 30)
                retry_after = r.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = max(0, float(retry_after))
                    except ValueError:
                        delay = max(0, (parsedate_to_datetime(retry_after) - utcnow()).total_seconds())
                elif r.status_code == 420:
                    delay = int(r.headers.get("X-ESI-Error-Limit-Reset", delay)) + 1
                if attempt + 1 == retries:
                    raise RuntimeError(f"ESI: HTTP {r.status_code} after {retries} attempts")
                if r.status_code in (420, 429):
                    self.limiter.block(delay)
                else:
                    time.sleep(delay)
                continue
            if r.status_code != 304:
                r.raise_for_status()
            return r
        raise RuntimeError("ESI request failed")
