"""Unit tests for scraper.classifier."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.scraper.classifier import classify

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("baby_index.html", "index"),
        ("caring_for_newborn_index.html", "index"),
        ("helping_baby_sleep_article.html", "article"),
    ],
)
def test_classify_real_fixtures(fixture, expected):
    html = (FIXTURES / fixture).read_text(encoding="utf-8")
    assert classify(html) == expected


def test_classify_empty_main_defaults_to_article():
    html = "<html><body><main id='maincontent'></main></body></html>"
    assert classify(html) == "article"


def test_classify_long_prose_is_article():
    para = " ".join(["word"] * 50)
    html = f"""
    <html><body><main id='maincontent'>
        <h2>Title</h2>
        <p>{para}</p>
        <p>{para}</p>
        <p>{para}</p>
    </main></body></html>
    """
    assert classify(html) == "article"


def test_classify_mostly_link_lists_is_index():
    items = "".join(f"<li><a href='/baby/{i}'>topic {i}</a></li>" for i in range(20))
    html = f"""
    <html><body><main id='maincontent'>
        <ul>{items}</ul>
    </main></body></html>
    """
    assert classify(html) == "index"


def test_classify_short_paragraph_does_not_save_a_list_dominated_page():
    items = "".join(f"<li><a href='/baby/{i}'>topic {i}</a></li>" for i in range(20))
    short_p = "<p>one word</p>"
    html = f"""
    <html><body><main id='maincontent'>
        <h2>Title</h2>
        {short_p}{short_p}
        <ul>{items}</ul>
    </main></body></html>
    """
    assert classify(html) == "index"


def test_classify_heading_with_substantive_prose_is_article():
    para = " ".join(["word"] * 25)
    items = "".join(f"<li><a href='/baby/{i}'>topic {i}</a></li>" for i in range(20))
    html = f"""
    <html><body><main id='maincontent'>
        <h2>Real section</h2>
        <p>{para}</p>
        <ul>{items}</ul>
    </main></body></html>
    """
    assert classify(html) == "article"
