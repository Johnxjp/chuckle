"""Unit tests for scraper.normaliser."""

from __future__ import annotations

import pytest

from src.scraper.normaliser import normalise

BASE = "https://www.nhs.uk/baby/caring-for-a-newborn/"


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        # absolute, already canonical
        ("https://www.nhs.uk/baby/", "https://www.nhs.uk/baby/"),
        # fragment stripped
        (
            "https://www.nhs.uk/baby/#main",
            "https://www.nhs.uk/baby/",
        ),
        # query stripped
        (
            "https://www.nhs.uk/baby/?ref=top",
            "https://www.nhs.uk/baby/",
        ),
        # case lowering on scheme + host
        ("HTTPS://WWW.NHS.UK/Baby/", "https://www.nhs.uk/Baby/"),
        # trailing slash added for extension-less paths
        ("https://www.nhs.uk/baby", "https://www.nhs.uk/baby/"),
        # file extension preserved (no trailing slash)
        ("/baby/leaflet.pdf", "https://www.nhs.uk/baby/leaflet.pdf"),
        # relative href resolved against base
        (
            "helping-your-baby-to-sleep/",
            "https://www.nhs.uk/baby/caring-for-a-newborn/helping-your-baby-to-sleep/",
        ),
        # protocol-relative resolved
        ("//www.nhs.uk/baby/", "https://www.nhs.uk/baby/"),
        # path-relative with no trailing slash
        ("../newborn/", "https://www.nhs.uk/baby/newborn/"),
    ],
)
def test_normalise_rules(href, expected):
    assert normalise(href, BASE) == expected


@pytest.mark.parametrize(
    "href",
    [
        "mailto:foo@bar.com",
        "tel:+441234567890",
        "javascript:void(0)",
        "ftp://files.nhs.uk/leaflet.pdf",
        "",
        "   ",
        "#section-only",
    ],
)
def test_normalise_rejects_invalid(href):
    assert normalise(href, BASE) is None
