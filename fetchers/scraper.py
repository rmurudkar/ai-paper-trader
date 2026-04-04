"""Fallback web scraper for NewsAPI.ai articles (step 3 of waterfall enrichment).

FALLBACK ONLY — never called directly. Only used by aggregator.py when:
- Step 1 (Polygon.io full text lookup) fails AND
- Step 2 (Alpaca News full text lookup) fails

Skip list (return snippet only): wsj.com, ft.com, bloomberg.com, nytimes.com

Primary: trafilatura.fetch_url() + trafilatura.extract()
Fallback: newspaper3k Article().parse()
If both fail: return snippet, set partial:true
"""

from typing import Dict, Optional


def scrape_article_fallback(url: str, snippet: str) -> Dict:
    """FALLBACK ONLY — scrape full article text when licensed sources fail.

    Never called directly from anywhere except aggregator.py waterfall enrichment.

    Skip list (return snippet only): wsj.com, ft.com, bloomberg.com, nytimes.com
    Primary: trafilatura.fetch_url() + trafilatura.extract()
    Fallback: newspaper3k Article().parse()
    If both fail: return snippet, set partial:true
    Truncate to 1200 words max.

    Args:
        url: Article URL to scrape (from NewsAPI.ai item).
        snippet: Original snippet to fall back to if scraping fails.

    Returns:
        Dict with keys: full_text, partial:bool
        partial=True if scraping failed and using snippet only
        partial=False if scraping succeeded
    """
    pass


def is_paywalled_domain(url: str) -> bool:
    """Check if URL is from known paywalled/skip-list domains.

    Skip list: wsj.com, ft.com, bloomberg.com, nytimes.com

    Args:
        url: Article URL to check.

    Returns:
        True if domain should be skipped (use snippet only).
    """
    pass


def scrape_with_trafilatura(url: str) -> Optional[str]:
    """Primary scraping method using trafilatura.

    Args:
        url: Article URL to scrape.

    Returns:
        Extracted article text or None if extraction failed.
    """
    pass


def scrape_with_newspaper3k(url: str) -> Optional[str]:
    """Fallback scraping method using newspaper3k.

    Args:
        url: Article URL to scrape.

    Returns:
        Extracted article text or None if extraction failed.
    """
    pass


def truncate_to_words(text: str, max_words: int = 1200) -> str:
    """Truncate text to maximum word count preserving word boundaries.

    Args:
        text: Text to truncate.
        max_words: Maximum number of words (default 1200).

    Returns:
        Truncated text.
    """
    pass


def validate_scraped_content(text: str, min_words: int = 50) -> bool:
    """Validate that scraped content meets minimum quality standards.

    Args:
        text: Scraped article text.
        min_words: Minimum word count for valid article.

    Returns:
        True if content is valid, False otherwise.
    """
    pass