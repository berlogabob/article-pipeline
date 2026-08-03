"""A page that renders no article text but declares where it really lives.

This is the share.google / link-wrapper shape: the interstitial carries
og:url or rel=canonical for the target while extracting to nothing itself.
Before this retry existed it was the single largest failure class in the
logs, always reported as a bare parse_error.
"""

from unittest.mock import patch

from article_pipeline.html_parser import fetch_and_extract_article

INTERSTITIAL = """
<html><head>
  <meta property="og:url" content="https://real.example.com/the-article">
</head><body><div id="app"></div></body></html>
"""

TARGET = """
<html><head><title>The Article</title>
  <link rel="canonical" href="https://real.example.com/the-article">
</head><body><article>
  <h1>The Article</h1>
  <p>Body text long enough that trafilatura will treat it as an article and
  return something useful rather than deciding the page is boilerplate.</p>
  <p>A second paragraph, for good measure, so extraction has real content.</p>
</article></body></html>
"""

SELF_CANONICAL = """
<html><head>
  <link rel="canonical" href="https://example.com/empty">
</head><body><div id="app"></div></body></html>
"""


def test_an_interstitial_is_retried_against_its_canonical_url():
    with patch(
        "article_pipeline.html_parser.fetch_html",
        side_effect=[INTERSTITIAL, TARGET],
    ) as fetch:
        article = fetch_and_extract_article("https://share.example.com/abc123")

    assert article is not None, "the canonical target had extractable text"
    assert "second paragraph" in article["text"].lower()
    # Filed under the URL we actually read, not the redirector.
    assert article["canonical_url"] == "https://real.example.com/the-article"
    assert fetch.call_count == 2
    assert fetch.call_args_list[1].args[0] == "https://real.example.com/the-article"


def test_a_self_canonical_page_is_not_refetched():
    """The loop guard. Without it every empty page fetches itself forever."""
    with patch(
        "article_pipeline.html_parser.fetch_html", return_value=SELF_CANONICAL
    ) as fetch:
        article = fetch_and_extract_article("https://example.com/empty")

    assert article is None
    assert fetch.call_count == 1, "canonical == url must not trigger a retry"


def test_the_canonical_url_comparison_ignores_www_and_trailing_slash():
    page = SELF_CANONICAL.replace(
        "https://example.com/empty", "https://www.example.com/empty/"
    )
    with patch("article_pipeline.html_parser.fetch_html", return_value=page) as fetch:
        assert fetch_and_extract_article("https://example.com/empty") is None
    assert fetch.call_count == 1


def test_the_retry_does_not_chain_beyond_one_hop():
    """Two redirectors in a row stop after the first hop, not N."""
    second = INTERSTITIAL.replace(
        "https://real.example.com/the-article", "https://third.example.com/x"
    )
    with patch(
        "article_pipeline.html_parser.fetch_html",
        side_effect=[INTERSTITIAL, second, TARGET],
    ) as fetch:
        article = fetch_and_extract_article("https://share.example.com/abc123")

    assert article is None
    assert fetch.call_count == 2, "one hop only"


def test_a_page_with_no_canonical_still_fails_cleanly():
    with patch(
        "article_pipeline.html_parser.fetch_html",
        return_value="<html><body><div></div></body></html>",
    ) as fetch:
        assert fetch_and_extract_article("https://example.com/x") is None
    assert fetch.call_count == 1


def test_extraction_that_succeeds_never_triggers_a_retry():
    with patch("article_pipeline.html_parser.fetch_html", return_value=TARGET) as fetch:
        article = fetch_and_extract_article("https://real.example.com/the-article")
    assert article is not None
    assert fetch.call_count == 1


# --- Open Graph fallback -----------------------------------------------------
# X, Threads and most SPA shells serve no server-side article text at all, so
# there is nothing for trafilatura to find and no canonical to follow. Their
# og: tags are the only content that exists.


def _og_page(title="A Post", description="x" * 60, site=None, url=None):
    tags = [f'<meta property="og:title" content="{title}">']
    if description is not None:
        tags.append(f'<meta property="og:description" content="{description}">')
    if site:
        tags.append(f'<meta property="og:site_name" content="{site}">')
    if url:
        tags.append(f'<meta property="og:url" content="{url}">')
    return f"<html><head>{''.join(tags)}</head><body><div id=root></div></body></html>"


def test_a_js_only_page_is_captured_from_open_graph_tags():
    page = _og_page(
        title="Github Projects (@githubprojects) on Threads",
        description="Hydra Launcher is an open source game launcher with its own embedded bittorrent client.",
        site="Threads",
        url="https://www.threads.com/@githubprojects/post/DaNH5",
    )
    # Social hosts consult the reader proxy first now; og: tags are the
    # fallback when it comes up empty.
    with patch("article_pipeline.html_parser.fetch_html", return_value=page), patch(
        "article_pipeline.html_parser.fetch_reader_article", return_value=None
    ):
        article = fetch_and_extract_article("https://www.threads.com/@x/post/DaNH5")

    assert article is not None
    assert article["metadata_only"] is True, "the note must admit it is metadata"
    assert article["title"] == "Github Projects (@githubprojects) on Threads"
    assert "embedded bittorrent client" in article["text"]
    assert "Source: Threads" in article["text"]
    assert article["canonical_url"] == "https://www.threads.com/@githubprojects/post/DaNH5"


def test_a_uselessly_short_description_is_not_worth_a_note():
    """Kaggle serves og:description="Kaggle" — a note built from that says nothing."""
    with patch(
        "article_pipeline.html_parser.fetch_html",
        return_value=_og_page(title="Kaggle", description="Kaggle"),
    ):
        assert fetch_and_extract_article("https://www.kaggle.com/competitions/x") is None


def test_a_page_with_no_open_graph_tags_is_still_a_failure():
    with patch(
        "article_pipeline.html_parser.fetch_html",
        return_value="<html><head><title>t</title></head><body><div></div></body></html>",
    ):
        assert fetch_and_extract_article("https://www.reddit.com/r/x/comments/y") is None


def test_a_real_article_is_never_downgraded_to_metadata():
    """The og path must not shadow a page trafilatura can actually read."""
    page = TARGET.replace(
        "<title>The Article</title>",
        '<title>The Article</title><meta property="og:description" content="'
        + "a short teaser that is definitely longer than the floor" + '">',
    )
    with patch("article_pipeline.html_parser.fetch_html", return_value=page):
        article = fetch_and_extract_article("https://real.example.com/the-article")

    assert article is not None
    assert not article.get("metadata_only")
    assert "second paragraph" in article["text"].lower()
