from unittest.mock import Mock, patch

import requests

from article_pipeline.html_parser import fetch_html


def _rate_limiter():
    limiter = Mock()
    limiter.wait.return_value = False
    return limiter


def test_fetch_html_retries_429_with_exponential_backoff_then_succeeds():
    r1 = Mock(status_code=429)
    r2 = Mock(status_code=429)
    r3 = Mock(status_code=200, text="ok")
    r1.raise_for_status.side_effect = requests.HTTPError("429")
    r2.raise_for_status.side_effect = requests.HTTPError("429")
    r3.raise_for_status.return_value = None

    with patch(
        "article_pipeline.html_parser.validate_url", return_value=True
    ), patch(
        "article_pipeline.html_parser.trafilatura.fetch_url", return_value=None
    ), patch(
        "article_pipeline.html_parser._session.get", side_effect=[r1, r2, r3]
    ) as mock_get, patch(
        "article_pipeline.html_parser.time.sleep"
    ) as mock_sleep:
        result = fetch_html(
            "https://example.com/a",
            retry_429_count=2,
            retry_429_backoff_seconds=1.5,
            rate_limiter=_rate_limiter(),
        )

    assert result == "ok"
    assert mock_get.call_count == 3
    mock_sleep.assert_any_call(1.5)
    mock_sleep.assert_any_call(3.0)


def test_fetch_html_stops_after_configured_429_retries():
    r1 = Mock(status_code=429)
    r2 = Mock(status_code=429)
    r3 = Mock(status_code=429)
    err = requests.HTTPError("429 too many requests")
    r1.raise_for_status.side_effect = err
    r2.raise_for_status.side_effect = err
    r3.raise_for_status.side_effect = err

    with patch(
        "article_pipeline.html_parser.validate_url", return_value=True
    ), patch(
        "article_pipeline.html_parser.trafilatura.fetch_url", return_value=None
    ), patch(
        "article_pipeline.html_parser._session.get", side_effect=[r1, r2, r3]
    ) as mock_get, patch(
        "article_pipeline.html_parser.time.sleep"
    ) as mock_sleep:
        result = fetch_html(
            "https://example.com/b",
            retry_429_count=2,
            retry_429_backoff_seconds=0.5,
            rate_limiter=_rate_limiter(),
        )

    assert result is None
    assert mock_get.call_count == 3
    assert mock_sleep.call_count == 2
