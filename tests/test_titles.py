import pytest
from article_pipeline.titles import (choose_title, final_title, is_generic_title,
    llm_title, normalise_title, site_names, strip_site_affixes, summary_title)
from article_pipeline.config import Config


@pytest.mark.parametrize('title', ['Хабр', 'GitHub', 'OpenAI', 'Qwen', 'youtube.com', 'dQw4w9WgXcQ', '16:45', '2026-11-13 16:45:02', 'OneBand', 'Name (@abc) on X'])
def test_generic_titles(title):
    assert is_generic_title(title, 'oneband.example', set())


@pytest.mark.parametrize(('title', 'expected'), [
    ('Как работает Python | Хабр', 'Как работает Python'),
    ('Хабр: Как работает Python', 'Как работает Python'),
    ('A useful article — Example', 'A useful article'),
    ('Example - A useful article', 'A useful article'),
    ('ABC | Example', 'ABC | Example'),
])
def test_affixes(title, expected):
    assert strip_site_affixes(title, {'Хабр', 'Example'}, 'example.com') == expected


def test_html_candidates():
    html = '<meta property="og:site_name" content="Example"><title>Example</title><meta property="og:title" content="Real Story | Example"><h1>Other Heading</h1>'
    assert choose_title(html, 'https://example.com') == 'Real Story'
    html = '<script type="application/ld+json">{"@graph":[{"@type":"NewsArticle","headline":"A JSON story","publisher":{"name":"Publisher"}}]}</script>'
    assert choose_title(html, 'https://example.com') == 'A JSON story'
    assert 'Publisher' in site_names(html, 'https://example.com')
    assert choose_title('<h1>Header</h1><main><h1>Article heading</h1></main>', '') == 'Article heading'


class Engine:
    def __init__(self, answer): self.answer = answer
    def chat_json(self, prompt, model, temperature):
        if isinstance(self.answer, Exception): raise self.answer
        return self.answer


@pytest.mark.parametrize(('answer', 'expected'), [('{"title":"Useful story"}', 'Useful story'), ('Here: {"title":"Useful story"} thanks', 'Useful story'), ('{"title":"GitHub"}', None), (ValueError(), None)])
def test_llm(answer, expected):
    assert llm_title(Engine(answer), 'text', 'model') == expected


def test_summary_and_final_order():
    assert normalise_title('  “A useful title”  ') == 'A useful title'
    summary = 'The article describes a detailed exploration of language models, including many practical examples and other ideas.'
    assert summary_title(summary) == 'A detailed exploration of language models'
    cfg = Config()
    engine = Engine('{"title":"Generated topic"}')
    args = ('https://example.com', summary, 'text', engine, cfg)
    assert final_title('Page title | Example', 'Passed title', *args) == 'Page title'
    assert final_title('Example', 'Passed title', *args) == 'Passed title'
    assert final_title('Example', '', *args) == 'Generated topic'
    assert final_title('Example', '', *args[:-2], Engine(ValueError()), cfg) == 'A detailed exploration of language models'
