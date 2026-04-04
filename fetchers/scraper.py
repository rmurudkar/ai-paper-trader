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


def scrape(url: str, snippet: str = "") -> Dict:
    """FALLBACK ONLY — scrape full article text when licensed sources fail.

    Step 3 of waterfall enrichment for NewsAPI.ai articles.
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


def _is_paywalled_domain(url: str) -> bool:
    """Private: Check if URL is from known paywalled/skip-list domains."""
    pass


def _scrape_with_trafilatura(url: str) -> Optional[str]:
    """Private: Primary scraping method using trafilatura."""
    pass


def _scrape_with_newspaper3k(url: str) -> Optional[str]:
    """Private: Fallback scraping method using newspaper3k."""
    pass


def _truncate_to_words(text: str, max_words: int = 1200) -> str:
    """Private: Truncate text to maximum word count preserving word boundaries."""
    pass


def _validate_scraped_content(text: str, min_words: int = 50) -> bool:
    """Private: Validate that scraped content meets minimum quality standards."""
    pass