from enum import Enum
from urllib.parse import urlparse


class ProcessingError(Enum):
    INVALID_URL = "invalid_url"
    NETWORK_ERROR = "network_error"
    PARSE_ERROR = "parse_error"  # fetched fine, nothing extractable
    LLM_ERROR = "llm_error"
    EMPTY_CONTENT = "empty_content"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"
    NO_URL_FOUND = "no_url_found"
    BLOCKED = "blocked"  # 401/403, or 429 after the retry ladder
    NOT_FOUND = "not_found"  # 404/410


# Worth another attempt later: the network, the site or the model may behave
# differently. The rest are settled — a 404 will not become a 200, and a URL
# that is not a URL will not start being one. BLOCKED is retryable since the
# reader-proxy fallback fetches from other IPs, so a 403 against us can still
# succeed there.
RETRYABLE_ERRORS = frozenset(
    {
        ProcessingError.NETWORK_ERROR,
        ProcessingError.TIMEOUT,
        ProcessingError.PARSE_ERROR,
        ProcessingError.EMPTY_CONTENT,
        ProcessingError.LLM_ERROR,
        ProcessingError.BLOCKED,
    }
)


def is_retryable(error: ProcessingError) -> bool:
    return error in RETRYABLE_ERRORS


class FetchError(Exception):
    """A definitive HTTP answer, carrying the outcome it maps to.

    Without this every 403, 404 and read timeout arrived as the same bare
    `None` from fetch_html and was reported as parse_error, so nothing could
    tell "the site refused us" from "we got the page and it was empty".
    """

    def __init__(self, error: "ProcessingError", message: str, http_status: int | None = None):
        super().__init__(message)
        self.error = error
        self.http_status = http_status


def validate_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    if len(url) > 2048:
        return False
    try:
        result = urlparse(url)
        return all([result.scheme in ("http", "https"), result.netloc])
    except (ValueError, AttributeError):
        return False
