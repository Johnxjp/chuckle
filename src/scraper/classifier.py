"""Page classification (article vs index) for NHS baby pages.

Classification operates on the <main id="maincontent"> region (fallback <body>).
Strict index criteria — bias toward `article` so we never discard real prose.
"""

from __future__ import annotations

from typing import Literal

from bs4 import BeautifulSoup, Tag

Classification = Literal["article", "index"]

MIN_SUBSTANTIVE_WORDS = 20
MIN_SUBSTANTIVE_PARAGRAPHS = 3
MIN_LINK_LIS_FOR_INDEX = 5


def classify(html: str) -> Classification:
    """Return 'index' iff ALL strict index criteria hold; otherwise 'article'."""
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main", id="maincontent") or soup.find("main") or soup.body
    if main is None:
        return "article"

    paragraphs = main.find_all("p")
    substantive_paragraphs = [
        p for p in paragraphs if _word_count(p.get_text(" ", strip=True)) >= MIN_SUBSTANTIVE_WORDS
    ]
    substantive_p_count = len(substantive_paragraphs)

    link_lis = [li for li in main.find_all("li") if li.find("a", href=True)]
    link_li_count = len(link_lis)

    dominated_by_lists = (
        link_li_count >= MIN_LINK_LIS_FOR_INDEX and link_li_count > substantive_p_count
    )

    few_substantive_paragraphs = substantive_p_count < MIN_SUBSTANTIVE_PARAGRAPHS

    if not _heading_has_no_substantive_prose(main):
        return "article"

    if dominated_by_lists and few_substantive_paragraphs:
        return "index"

    return "article"


def _word_count(text: str) -> int:
    return len(text.split())


def _heading_has_no_substantive_prose(root: Tag) -> bool:
    """True iff no h2/h3 inside `root` is followed by a substantive <p>.

    A "substantive" paragraph has at least MIN_SUBSTANTIVE_WORDS words and
    appears later in document order than the heading we are testing, before
    the next h2/h3.
    """
    headings = root.find_all(["h2", "h3"])
    if not headings:
        return True

    for heading in headings:
        next_paragraph = _next_paragraph_before_next_heading(heading)
        if next_paragraph is None:
            continue
        if _word_count(next_paragraph.get_text(" ", strip=True)) >= MIN_SUBSTANTIVE_WORDS:
            return False
    return True


def _next_paragraph_before_next_heading(heading: Tag) -> Tag | None:
    """Walk the document after `heading` until we find a <p> or hit the next h2/h3."""
    for sibling in heading.find_all_next():
        if not isinstance(sibling, Tag):
            continue
        if sibling.name in {"h2", "h3"}:
            return None
        if sibling.name == "p":
            return sibling
    return None
