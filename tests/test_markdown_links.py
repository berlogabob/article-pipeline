from article_pipeline.net import extract_links_from_markdown


def test_extract_links_from_markdown_single_bare_url():
    links = extract_links_from_markdown(
        "https://example.com/article", fallback_title="Inbox"
    )

    assert links == [{"url": "https://example.com/article", "title": "article"}]


def test_extract_links_from_markdown_multiple_numbered_urls_in_order():
    links = extract_links_from_markdown(
        "\n".join(
            [
                "1. https://example.com/one",
                "2. https://example.org/two",
            ]
        ),
        fallback_title="Batch",
    )

    assert [link["url"] for link in links] == [
        "https://example.com/one",
        "https://example.org/two",
    ]
    assert [link["title"] for link in links] == ["one", "two"]


def test_extract_links_from_markdown_uses_markdown_link_text_as_title():
    links = extract_links_from_markdown(
        "Read [Useful Article](https://example.com/useful).",
        fallback_title="Batch",
    )

    assert links == [
        {"url": "https://example.com/useful", "title": "Useful Article"}
    ]


def test_extract_links_from_markdown_preserves_mixed_link_order():
    links = extract_links_from_markdown(
        "https://example.com/first\n[Second](https://example.com/second)",
        fallback_title="Batch",
    )

    assert [link["url"] for link in links] == [
        "https://example.com/first",
        "https://example.com/second",
    ]


def test_extract_links_from_markdown_deduplicates_by_normalized_url():
    links = extract_links_from_markdown(
        "\n".join(
            [
                "[First](https://www.example.com/article/?utm_source=x)",
                "https://example.com/article",
            ]
        ),
        fallback_title="Batch",
    )

    assert links == [
        {
            "url": "https://www.example.com/article/?utm_source=x",
            "title": "First",
        }
    ]


def test_extract_links_from_markdown_returns_empty_list_without_urls():
    assert extract_links_from_markdown("No links here", fallback_title="Batch") == []
