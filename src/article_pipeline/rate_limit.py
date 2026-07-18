"""Per-domain + global request throttling.

Ported from the old logseq-processor project's src/common.py
(DomainRateLimiter), with the Config-singleton dependency removed: delays
are explicit constructor parameters with defaults matching the old config.
"""

import time
from threading import Lock
from typing import Optional
from urllib.parse import urlparse


class DomainRateLimiter:
    def __init__(self, delay_per_domain: float = 2.0, delay_global: float = 1.0):
        self.delay_domain = delay_per_domain
        self.delay_global = delay_global
        self.last_request_domain: dict[str, float] = {}
        self.last_request_global = 0.0
        self.lock = Lock()

    def wait(self, url: str) -> bool:
        domain = urlparse(url).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]

        with self.lock:
            now = time.time()

            time_since_global = now - self.last_request_global
            if time_since_global < self.delay_global:
                time.sleep(self.delay_global - time_since_global)
                now = time.time()

            time_since_domain = now - self.last_request_domain.get(domain, 0)
            if time_since_domain < self.delay_domain:
                sleep_time = self.delay_domain - time_since_domain
                time.sleep(sleep_time)

            self.last_request_global = time.time()
            self.last_request_domain[domain] = time.time()
            return time_since_domain < self.delay_domain


_default_rate_limiter: Optional[DomainRateLimiter] = None


def get_rate_limiter(
    delay_per_domain: float = 2.0, delay_global: float = 1.0
) -> DomainRateLimiter:
    """Return a process-wide default DomainRateLimiter, creating it on first use.

    Callers that need distinct throttling policies should construct their own
    DomainRateLimiter instance instead of relying on this shared default.
    """
    global _default_rate_limiter
    if _default_rate_limiter is None:
        _default_rate_limiter = DomainRateLimiter(
            delay_per_domain=delay_per_domain, delay_global=delay_global
        )
    return _default_rate_limiter
