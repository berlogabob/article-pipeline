from enum import Enum
from urllib.parse import urlparse


class ProcessingError(Enum):
    INVALID_URL = "invalid_url"
    NETWORK_ERROR = "network_error"
    PARSE_ERROR = "parse_error"
    LLM_ERROR = "llm_error"
    EMPTY_CONTENT = "empty_content"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"
    NO_URL_FOUND = "no_url_found"


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
