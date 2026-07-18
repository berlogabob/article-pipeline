from unittest.mock import Mock, patch

import requests

from article_pipeline.net import expand_url, get_unique_path, safe_filename


# ---------------------------------------------------------------------------
# expand_url (ported from old tests/test_utils_expand_url.py)
# ---------------------------------------------------------------------------


def test_expand_url_head_redirect_returns_final_url():
    session = Mock()
    session.max_redirects = None
    session.head.return_value = Mock(url="https://example.com/final")

    with patch("article_pipeline.net.requests.Session", return_value=session):
        result = expand_url("https://short.example/abc", timeout=3, max_redirects=7)

    assert result == "https://example.com/final"
    session.head.assert_called_once_with(
        "https://short.example/abc", allow_redirects=True, timeout=3
    )
    assert session.max_redirects == 7
    session.get.assert_not_called()


def test_expand_url_head_failure_then_get_success():
    session = Mock()
    session.max_redirects = None
    session.head.side_effect = requests.RequestException("head failed")
    get_response = Mock(url="https://example.com/from-get")
    session.get.return_value = get_response

    with patch("article_pipeline.net.requests.Session", return_value=session):
        result = expand_url("https://short.example/fallback")

    assert result == "https://example.com/from-get"
    session.head.assert_called_once()
    session.get.assert_called_once_with(
        "https://short.example/fallback",
        allow_redirects=True,
        timeout=10,
        stream=True,
    )
    get_response.close.assert_called_once()


def test_expand_url_head_and_get_fail_returns_none():
    session = Mock()
    session.max_redirects = None
    session.head.side_effect = requests.RequestException("head failed")
    session.get.side_effect = requests.RequestException("get failed")

    with patch("article_pipeline.net.requests.Session", return_value=session):
        result = expand_url("https://short.example/fail")

    assert result is None


def test_expand_url_non_http_final_url_returns_none():
    session = Mock()
    session.max_redirects = None
    session.head.return_value = Mock(url="ftp://example.com/file")

    with patch("article_pipeline.net.requests.Session", return_value=session):
        result = expand_url("https://short.example/ftp")

    assert result is None


def test_expand_url_extracts_wrapped_query_url():
    session = Mock()
    session.max_redirects = None
    session.head.return_value = Mock(
        url="https://news.example/redirect?url=https%3A%2F%2Ftarget.example%2Farticle"
    )

    with patch("article_pipeline.net.requests.Session", return_value=session):
        result = expand_url("https://short.example/wrapped")

    assert result == "https://target.example/article"
    session.get.assert_not_called()


def test_expand_url_closes_get_response_on_wrapped_early_return():
    session = Mock()
    session.max_redirects = None
    session.head.side_effect = requests.RequestException("head failed")
    get_resp = Mock(
        url="https://wrapper.example/redirect?url=https%3A%2F%2Ftarget.example%2Ffinal"
    )
    session.get.return_value = get_resp

    with patch("article_pipeline.net.requests.Session", return_value=session):
        result = expand_url("https://short.example/wrapped-get")

    assert result == "https://target.example/final"
    get_resp.close.assert_called_once()


def test_expand_url_meta_fallback_from_wrapper_page():
    session = Mock()
    session.max_redirects = None
    session.head.return_value = Mock(url="https://news.example/redirect?id=1")
    html_resp = Mock(
        url="https://news.example/redirect?id=1",
        text="""
            <html><head>
              <meta property="og:url" content="https://target.example/from-og" />
            </head></html>
        """,
    )
    session.get.return_value = html_resp

    with patch("article_pipeline.net.requests.Session", return_value=session):
        result = expand_url("https://short.example/meta")

    assert result == "https://target.example/from-og"
    session.get.assert_called_once()
    html_resp.close.assert_called_once()


def test_expand_url_search_app_shortlink_resolves_to_canonical_target():
    session = Mock()
    session.max_redirects = None
    session.head.return_value = Mock(url="https://klausai.com/")

    with patch("article_pipeline.net.requests.Session", return_value=session):
        result = expand_url("https://search.app/2Xn5o")

    assert result == "https://klausai.com/"
    session.get.assert_not_called()


def test_expand_url_rejects_private_network_target():
    session = Mock()
    session.max_redirects = None
    session.head.return_value = Mock(url="http://127.0.0.1/admin")

    with patch("article_pipeline.net.requests.Session", return_value=session):
        result = expand_url("https://short.example/private")

    assert result is None


def test_expand_url_handles_non_request_exception_in_html_fallback():
    session = Mock()
    session.max_redirects = None
    session.head.return_value = Mock(url="https://news.example/redirect?id=1")
    html_resp = Mock(url="https://news.example/redirect?id=1")
    type(html_resp).text = property(
        lambda _self: (_ for _ in ()).throw(
            UnicodeDecodeError("utf-8", b"x", 0, 1, "boom")
        )
    )
    session.get.return_value = html_resp

    with patch("article_pipeline.net.requests.Session", return_value=session):
        result = expand_url("https://short.example/meta-bad")

    assert result == "https://news.example/redirect?id=1"
    html_resp.close.assert_called_once()


# ---------------------------------------------------------------------------
# safe_filename (new)
# ---------------------------------------------------------------------------


def test_safe_filename_strips_emoji():
    assert safe_filename("🚀 Launch 🔥") == "Launch"


def test_safe_filename_removes_forbidden_chars():
    result = safe_filename('Report: "Q1/Q2" <final> | v1?*')
    for ch in '<>:"/\\|?*':
        assert ch not in result


def test_safe_filename_truncates_long_russian_title_at_word_boundary():
    title = " ".join(["Заголовок"] * 15)  # well over 100 chars
    result = safe_filename(title, max_chars=60)
    assert len(result) <= 60
    # truncation should not cut a word in half: result must be a prefix of
    # the original title up to a space boundary (no trailing partial word).
    assert title.startswith(result)
    assert not title[len(result) : len(result) + 1].isalnum()


def test_safe_filename_preserves_cyrillic():
    result = safe_filename("Привет мир")
    assert result == "Привет мир"


def test_safe_filename_strips_trailing_dots_and_spaces():
    assert safe_filename("Draft Title...   ") == "Draft Title"


def test_safe_filename_empty_result_falls_back_to_article():
    assert safe_filename("🔥🔥🔥") == "Article"
    assert safe_filename("") == "Article"
    assert safe_filename("???") == "Article"


def test_safe_filename_default_max_chars_is_60():
    result = safe_filename("a" * 100)
    assert len(result) <= 60


# ---------------------------------------------------------------------------
# get_unique_path (new)
# ---------------------------------------------------------------------------


def test_get_unique_path_no_collision(tmp_path):
    path = get_unique_path("My Title", tmp_path)
    assert path == tmp_path / "My Title.md"


def test_get_unique_path_with_source_prefix(tmp_path):
    path = get_unique_path("My Title", tmp_path, source="YouTube")
    assert path == tmp_path / "YouTube - My Title.md"


def test_get_unique_path_collision_suffixing(tmp_path):
    (tmp_path / "My Title.md").write_text("x")
    (tmp_path / "My Title (1).md").write_text("x")

    path = get_unique_path("My Title", tmp_path)

    assert path == tmp_path / "My Title (2).md"


def test_get_unique_path_custom_extension(tmp_path):
    path = get_unique_path("My Title", tmp_path, ext=".typ")
    assert path == tmp_path / "My Title.typ"
