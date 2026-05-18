"""Unit tests for scraper.scope."""

from __future__ import annotations

import pytest

from src.scraper.scope import is_exception_url, is_in_scope


@pytest.mark.parametrize(
    "url",
    [
        "https://www.nhs.uk/baby/",
        "https://www.nhs.uk/baby/caring-for-a-newborn/",
        "https://www.nhs.uk/conditions/jaundice-in-babies/",
        "https://www.nhs.uk/children/dental-care/",
        "https://www.nhs.uk/pregnancy/labour-and-birth/early-days/",
        "https://www.nhs.uk/conditions/newborn-jaundice/",
    ],
)
def test_in_scope_accepts(url):
    assert is_in_scope(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.nhs.uk/conditions/heart-disease/",
        "https://www.nhs.uk/medicines/ibuprofen/",
        "https://www.nhs.uk/",
        "https://example.com/baby/",
        "http://www.nhs.uk/baby/",  # in scope: host + /baby/ both ok regardless of scheme
        "https://api.nhs.uk/baby/",  # other subdomain rejected
    ],
)
def test_in_scope_rejects_some(url):
    if url == "http://www.nhs.uk/baby/":
        assert is_in_scope(url)
    else:
        assert not is_in_scope(url)


def test_is_exception_url_true():
    assert is_exception_url("https://www.nhs.uk/pregnancy/labour-and-birth/early-days/")


def test_is_exception_url_false():
    assert not is_exception_url("https://www.nhs.uk/baby/")
