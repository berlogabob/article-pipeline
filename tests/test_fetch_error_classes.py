"""A 403 and an empty page are not the same failure.

Every failure in the logs was reported as parse_error, so a retry pass could
not tell "the site refused us outright" from "we got the page and it was
empty". These are the boundaries where that distinction is made.
"""

from unittest.mock import Mock, patch

import pytest
import requests

from article_pipeline.errors import ProcessingError, is_retryable
from article_pipeline.html_parser import FetchError, fetch_html
from article_pipeline.stage1_ingest import ingest_article


def _rate_limiter():
    limiter = Mock()
    limiter.wait.return_value = False
    return limiter


def _fetch(status):
    response = Mock(status_code=status, text="")
    response.raise_for_status.return_value = None
    with patch(
        "article_pipeline.html_parser.validate_url", return_value=True
    ), patch(
        "article_pipeline.html_parser.trafilatura.fetch_url", return_value=None
    ), patch("article_pipeline.html_parser._session.get", return_value=response):
        return fetch_html("https://example.com/a", rate_limiter=_rate_limiter())


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, ProcessingError.BLOCKED),
        (403, ProcessingError.BLOCKED),
        (404, ProcessingError.NOT_FOUND),
        (410, ProcessingError.NOT_FOUND),
    ],
)
def test_a_definitive_status_names_its_own_outcome(status, expected):
    with pytest.raises(FetchError) as excinfo:
        _fetch(status)
    assert excinfo.value.error is expected


# The exhausted-429 case lives in test_html_parser_backoff.py, next to the
# ladder assertions it belongs with.


def test_a_read_timeout_is_a_timeout_not_a_parse_error():
    with patch(
        "article_pipeline.html_parser.validate_url", return_value=True
    ), patch(
        "article_pipeline.html_parser.trafilatura.fetch_url", return_value=None
    ), patch(
        "article_pipeline.html_parser._session.get",
        side_effect=requests.Timeout("read timed out"),
    ), pytest.raises(FetchError) as excinfo:
        fetch_html("https://example.com/a", rate_limiter=_rate_limiter())
    assert excinfo.value.error is ProcessingError.TIMEOUT


def test_a_200_that_extracts_to_nothing_stays_parse_error():
    """The distinction only works if the empty case keeps its own name."""
    with patch(
        "article_pipeline.stage1_ingest.expand_url", side_effect=lambda u: u
    ), patch(
        "article_pipeline.stage1_ingest.fetch_and_extract_article", return_value=None
    ):
        ingest, err = ingest_article("https://example.com/a", "t")
    assert ingest is None
    assert err is ProcessingError.PARSE_ERROR


def test_a_refused_fetch_reaches_the_caller_with_its_own_error():
    with patch(
        "article_pipeline.stage1_ingest.expand_url", side_effect=lambda u: u
    ), patch(
        "article_pipeline.stage1_ingest.fetch_and_extract_article",
        side_effect=FetchError(ProcessingError.BLOCKED, "HTTP 403"),
    ):
        ingest, err = ingest_article("https://example.com/a", "t")
    assert ingest is None
    assert err is ProcessingError.BLOCKED, "must not be flattened to network_error"


def test_fetch_error_details_reach_optional_ingest_out_parameter():
    details = {}
    with patch(
        "article_pipeline.stage1_ingest.expand_url", side_effect=lambda u: u
    ), patch(
        "article_pipeline.stage1_ingest.fetch_and_extract_article",
        side_effect=FetchError(ProcessingError.BLOCKED, "HTTP 403 denied", http_status=403),
    ):
        ingest, err = ingest_article("https://example.com/a", "t", fetch_details=details)
    assert ingest is None and err is ProcessingError.BLOCKED
    assert details == {"http_status": 403, "message": "HTTP 403 denied"}


def test_retryable_split_is_what_a_retry_pass_relies_on():
    assert is_retryable(ProcessingError.TIMEOUT)
    assert is_retryable(ProcessingError.PARSE_ERROR)
    assert is_retryable(ProcessingError.LLM_ERROR)
    # A 403 against us can still succeed via the reader-proxy fallback.
    assert is_retryable(ProcessingError.BLOCKED)
    # Hammering these again would only annoy the server.
    assert not is_retryable(ProcessingError.NOT_FOUND)
    assert not is_retryable(ProcessingError.INVALID_URL)
