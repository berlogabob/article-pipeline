from unittest.mock import Mock, patch

import pytest

from article_pipeline.errors import FetchError, ProcessingError
from article_pipeline.html_parser import fetch_and_extract_article, fetch_reddit_post_article

URL = "https://www.reddit.com/r/example/comments/abc/a_post/"


def response(data=None, status=200):
    result = Mock(status_code=status)
    result.json.return_value = data
    return result


def listing(**overrides):
    post = {"title": "A post", "selftext": "Post body", "author": "alice",
            "subreddit": "example", "permalink": "/r/example/comments/abc/a_post/",
            "score": 42, "url": URL, "is_self": True}
    post.update(overrides)
    comments = [
        {"kind": "t1", "data": {"author": "[deleted]", "body": "gone"}},
        {"kind": "t1", "data": {"author": "bob", "body": "[removed]"}},
        {"kind": "t1", "data": {"author": "AutoModerator", "body": "rules", "stickied": True}},
        {"kind": "more", "data": {}},
    ] + [{"kind": "t1", "data": {"author": f"user{i}", "body": "x" * 1000}} for i in range(20)]
    return [{"data": {"children": [{"data": post}]}}, {"data": {"children": comments}}]


def test_self_post_and_comments_are_bounded():
    with patch("article_pipeline.html_parser._session.get", return_value=response(listing())) as get:
        article = fetch_reddit_post_article(URL)
    assert article["title"] == "A post"
    assert article["canonical_url"] == URL
    assert "Subreddit: r/example" in article["text"]
    assert "Score: 42" in article["text"]
    assert "Post:\nPost body" in article["text"]
    comments = article["text"].split("Top comments:\n", 1)[1]
    assert len(comments) <= 12000
    assert "u/user0:" in comments and "u/user15:" not in comments
    assert "[deleted]" not in comments and "[removed]" not in comments and "AutoModerator" not in comments
    assert comments.endswith("…")
    assert get.call_args.args[0] == URL.rstrip("/") + ".json?raw_json=1&limit=50"
    assert get.call_args.kwargs["headers"]["Accept"] == "application/json"
    assert "Chrome/" in get.call_args.kwargs["headers"]["User-Agent"]


def test_comment_count_limit():
    data = listing()
    for child in data[1]["data"]["children"][4:]:
        child["data"]["body"] = "short"
    with patch("article_pipeline.html_parser._session.get", return_value=response(data)):
        text = fetch_reddit_post_article(URL)["text"]
    assert "u/user14:" in text and "u/user15:" not in text


def test_link_post_includes_outbound_url():
    data = listing(is_self=False, selftext="", url="https://example.com/story")
    with patch("article_pipeline.html_parser._session.get", return_value=response(data)):
        article = fetch_reddit_post_article(URL)
    assert "https://example.com/story" in article["text"]


@pytest.mark.parametrize("status", [403, 429])
def test_blocked_propagates_after_reader_fallback(status):
    with patch("article_pipeline.html_parser._session.get", return_value=response(status=status)), patch(
        "article_pipeline.html_parser.fetch_reader_article", return_value=None
    ) as reader, pytest.raises(FetchError) as exc:
        fetch_and_extract_article(URL)
    assert exc.value.error is ProcessingError.BLOCKED
    assert exc.value.http_status == status
    assert f"HTTP {status} (reddit blocked)" in str(exc.value)
    reader.assert_called_once()


def test_blocked_can_use_reader():
    article = {"text": "reader", "title": "title", "canonical_url": URL}
    with patch("article_pipeline.html_parser._session.get", return_value=response(status=403)), patch(
        "article_pipeline.html_parser.fetch_reader_article", return_value=article
    ):
        assert fetch_and_extract_article(URL) == article


def test_malformed_json_falls_back_to_old_reddit():
    bad = response()
    bad.json.side_effect = ValueError("invalid JSON")
    old = response()
    old.text = '<div class="thing" data-fullname="t3_abc"><a class="title">Old title</a><div class="md">Old body</div></div>'
    with patch("article_pipeline.html_parser._session.get", side_effect=[bad, old]) as get:
        article = fetch_reddit_post_article(URL)
    assert "Old body" in article["text"]
    assert get.call_args_list[1].args[0] == "https://old.reddit.com/r/example/comments/abc/a_post/"


def test_share_link_is_resolved_before_json():
    redirect = response()
    redirect.url = URL + "?share_id=123"
    with patch("article_pipeline.html_parser._session.get", side_effect=[redirect, response(listing())]) as get:
        article = fetch_reddit_post_article("https://www.reddit.com/r/example/s/abc")
    assert article["canonical_url"] == URL
    assert get.call_args_list[1].args[0] == URL.rstrip("/") + ".json?raw_json=1&limit=50"
