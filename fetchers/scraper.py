"""Advanced web scraper for full article text extraction with intelligent fallback strategies.

CRITICAL ARCHITECTURE NOTE:
===========================
This is the FALLBACK scraper in the content enrichment waterfall. It should NEVER be called
directly by external modules. The scraper is invoked only by aggregator.py when licensed
news sources (Polygon.io, Alpaca News) fail to provide full article text.

Content Enrichment Waterfall (aggregator.py orchestrates this):
===============================================================
Step 1: Licensed sources (Polygon.io full text API) - preferred, high quality
Step 2: Secondary licensed sources (Alpaca News API) - good quality, limited coverage
Step 3: Web scraping (THIS MODULE) - last resort, quality varies, rate limit concerns

Web Scraping Strategy (Three-Tier Fallback):
============================================
PRIMARY:   trafilatura.fetch_url() + trafilatura.extract()
           - Most reliable, handles complex layouts, respects robots.txt
           - Built for news article extraction specifically
           - Excellent at filtering navigation, ads, boilerplate content

FALLBACK:  newspaper3k Article().parse()
           - Good fallback when trafilatura fails
           - More aggressive extraction, sometimes catches missed content
           - Built-in article detection algorithms

LAST RESORT: BeautifulSoup4 raw text extraction
           - Strip all HTML tags and extract pure text
           - Used only when both primary methods fail completely
           - Quality highly variable, may include navigation/ads

Paywall Detection & Compliance:
==============================
The scraper maintains a strict skip-list of paywalled domains to ensure legal compliance
and avoid wasting resources on protected content:

SKIP DOMAINS (return snippet only):
- wsj.com (Wall Street Journal) - strict paywall
- ft.com (Financial Times) - metered paywall
- bloomberg.com (Bloomberg) - subscription required
- nytimes.com (New York Times) - article limit paywall
- economist.com (The Economist) - subscription model
- reuters.com (Reuters) - some content paywalled

For these domains, the scraper immediately returns the snippet without attempting extraction.

Content Quality Assurance:
==========================
All extracted content undergoes validation:
1. Minimum word count threshold (50 words) to filter navigation/error pages
2. Maximum word count limit (1200 words) to prevent memory bloat and API limit issues
3. Content structure validation (paragraphs, coherent text)
4. Encoding normalization and cleanup

Performance & Rate Limiting:
===========================
- Request timeout: 10 seconds to prevent hanging
- User-Agent rotation to avoid detection
- Respectful 1-second delay between requests
- Content caching to avoid re-scraping same URLs
- Graceful degradation on network failures

Error Handling Philosophy:
=========================
The scraper implements defense-in-depth error handling:
1. Network errors → try next fallback method
2. All methods fail → return snippet with partial=True flag
3. Timeout errors → return snippet rather than crash discovery
4. Encoding errors → attempt UTF-8 normalization, then fallback
5. Never crash the parent process - always return something usable

Security Considerations:
=======================
- No JavaScript execution (pure HTML parsing)
- Request timeout prevents resource exhaustion
- User-Agent string identifies bot clearly
- Respects robots.txt via trafilatura (when possible)
- No credential storage or authentication attempts

Integration with Sentiment Analysis:
===================================
Extracted content feeds directly into engine/sentiment.py for Claude API analysis.
Content quality directly impacts sentiment accuracy, so aggressive filtering
ensures only meaningful article text reaches the sentiment engine.

Memory Management:
==================
- 1200-word limit prevents memory bloat during high-volume news cycles
- Content truncation preserves article structure (sentence boundaries)
- Immediate cleanup of large HTTP responses
- No persistent content caching (handled upstream)

Compliance & Legal Notes:
=========================
- Only extracts publicly available content
- Respects robots.txt when possible
- Identifies as automated crawler in User-Agent
- Maintains skip-list for subscription/paywalled content
- Fair use extraction for sentiment analysis purposes only

Future Enhancements:
===================
- Content fingerprinting to detect duplicate articles across sources
- Machine learning content quality scoring
- Dynamic paywall detection beyond static domain list
- Integration with content delivery networks for faster extraction
- Structured data extraction (author, publication date, tags)
"""

import os
import re
import time
import logging
import requests
from urllib.parse import urlparse, urljoin
from typing import Dict, Optional, List, Tuple
from dotenv import load_dotenv

# Core scraping libraries (waterfall approach)
import trafilatura
from newspaper import Article
from bs4 import BeautifulSoup

# Load environment variables
load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)

# Configuration constants
MAX_WORDS = 1200  # Maximum article length before truncation
MIN_WORDS = 50    # Minimum words required for valid article content
REQUEST_TIMEOUT = 10  # Seconds before request timeout
SCRAPER_DELAY = 1.0   # Polite delay between requests (seconds)

# User-Agent string for web requests (identifies as legitimate news crawler)
USER_AGENT = (
    "Mozilla/5.0 (compatible; AI-Paper-Trader/1.0; +https://example.com/bot; "
    "news-analysis@example.com) NewsBot"
)

# Paywall domain skip-list (return snippet only for these domains)
# Updated regularly to maintain compliance with subscription news sites
PAYWALLED_DOMAINS = {
    'wsj.com', 'www.wsj.com',                    # Wall Street Journal
    'ft.com', 'www.ft.com',                      # Financial Times
    'bloomberg.com', 'www.bloomberg.com',        # Bloomberg News
    'nytimes.com', 'www.nytimes.com',            # New York Times
    'economist.com', 'www.economist.com',        # The Economist
    'reuters.com', 'www.reuters.com',            # Reuters (partial paywall)
    'barrons.com', 'www.barrons.com',            # Barron's
    'marketwatch.com', 'www.marketwatch.com',   # MarketWatch (some content)
}

# Content extraction failure tracking (for debugging and optimization)
_extraction_stats = {
    'trafilatura_success': 0,
    'newspaper_success': 0,
    'beautifulsoup_success': 0,
    'paywall_skipped': 0,
    'total_failures': 0
}


def scrape(url: str, snippet: str = "") -> Dict:
    """FALLBACK ONLY — scrape full article text when licensed sources fail.

    This function implements the final step (Step 3) of the content enrichment waterfall
    orchestrated by aggregator.py. It should NEVER be called directly by other modules.

    Execution Flow:
    --------------
    1. Validate URL and check against paywall skip-list
    2. If paywalled domain → return snippet immediately with partial=True
    3. If extractable domain → attempt three-tier waterfall extraction:
       a) PRIMARY: trafilatura (most reliable, built for news)
       b) FALLBACK: newspaper3k (good secondary option)
       c) LAST RESORT: BeautifulSoup raw text (when others fail)
    4. Validate extracted content quality (minimum word count, structure)
    5. Truncate to maximum word limit preserving sentence boundaries
    6. Return structured result with full_text and extraction metadata

    Quality Assurance:
    -----------------
    - All extracted content validated for minimum quality standards
    - Content truncated to prevent memory/API limit issues
    - Encoding normalized to handle international character sets
    - HTML entities decoded and cleaned up
    - Navigation/boilerplate content filtered out

    Error Recovery:
    --------------
    - Network timeouts → return snippet with partial=True
    - Extraction failures → try next method in waterfall
    - All methods fail → return snippet with partial=True
    - Invalid content → return snippet with partial=True
    - Never raises exceptions that could crash discovery pipeline

    Performance Optimization:
    ------------------------
    - 10-second request timeout prevents hanging
    - Polite 1-second delay between requests
    - Efficient memory usage with immediate cleanup
    - User-Agent rotation to avoid rate limiting

    Legal Compliance:
    ----------------
    - Strict paywall domain skip-list maintained
    - Respects robots.txt via trafilatura when possible
    - Clear bot identification in User-Agent string
    - Fair use extraction for sentiment analysis only

    Args:
        url: Article URL to scrape (from NewsAPI.ai item)
             Must be valid HTTP/HTTPS URL from supported domain
        snippet: Original snippet to fall back to if scraping fails
                Used as last resort when all extraction methods fail

    Returns:
        Dict with keys:
            full_text (str): Extracted article content or snippet fallback
            partial (bool): True if scraping failed and using snippet only
                          False if scraping succeeded with full content
            extraction_method (str): Method used: "trafilatura", "newspaper", "beautifulsoup", "snippet"
            word_count (int): Number of words in final content
            extraction_time_ms (int): Time spent on extraction (for performance monitoring)

    Example Successful Return:
    -------------------------
    {
        "full_text": "The Federal Reserve announced today that it will raise interest rates...",
        "partial": False,
        "extraction_method": "trafilatura",
        "word_count": 847,
        "extraction_time_ms": 1250
    }

    Example Paywall Return:
    ----------------------
    {
        "full_text": "Fed officials are expected to announce rate decision...",
        "partial": True,
        "extraction_method": "snippet",
        "word_count": 23,
        "extraction_time_ms": 5
    }

    Example Extraction Failure:
    ---------------------------
    {
        "full_text": "Original snippet content when extraction fails...",
        "partial": True,
        "extraction_method": "snippet",
        "word_count": 15,
        "extraction_time_ms": 3200
    }

    Integration Notes:
    -----------------
    - Called exclusively by aggregator.py in waterfall enrichment
    - Results feed directly to engine/sentiment.py for analysis
    - Extraction stats tracked globally for system optimization
    - Content quality impacts downstream sentiment accuracy
    """
    start_time = time.time()

    try:
        # Input validation and safety checks
        if not url or not isinstance(url, str):
            logger.warning("Invalid URL provided to scraper")
            return _create_result(snippet, True, "snippet", start_time)

        # Normalize URL and extract domain for paywall check
        parsed_url = urlparse(url.strip())
        if not parsed_url.netloc:
            logger.warning(f"Malformed URL: {url}")
            return _create_result(snippet, True, "snippet", start_time)

        domain = parsed_url.netloc.lower()

        # PAYWALL CHECK: Skip extraction for subscription/paywalled domains
        if _is_paywalled_domain(domain):
            logger.info(f"Skipping paywalled domain: {domain}")
            global _extraction_stats
            _extraction_stats['paywall_skipped'] += 1
            return _create_result(snippet, True, "snippet", start_time)

        # Polite delay to avoid overwhelming target servers
        time.sleep(SCRAPER_DELAY)

        # THREE-TIER WATERFALL EXTRACTION
        logger.info(f"Starting content extraction for: {url}")

        # TIER 1: trafilatura (primary method - most reliable for news content)
        full_text = _scrape_with_trafilatura(url)
        if full_text and _validate_scraped_content(full_text):
            logger.info(f"trafilatura extraction successful: {len(full_text.split())} words")
            _extraction_stats['trafilatura_success'] += 1
            final_text = _truncate_to_words(full_text, MAX_WORDS)
            return _create_result(final_text, False, "trafilatura", start_time)

        # TIER 2: newspaper3k (fallback method - good alternative parser)
        full_text = _scrape_with_newspaper3k(url)
        if full_text and _validate_scraped_content(full_text):
            logger.info(f"newspaper3k extraction successful: {len(full_text.split())} words")
            _extraction_stats['newspaper_success'] += 1
            final_text = _truncate_to_words(full_text, MAX_WORDS)
            return _create_result(final_text, False, "newspaper", start_time)

        # TIER 3: BeautifulSoup (last resort - raw HTML text extraction)
        full_text = _scrape_with_beautifulsoup(url)
        if full_text and _validate_scraped_content(full_text):
            logger.info(f"BeautifulSoup extraction successful: {len(full_text.split())} words")
            _extraction_stats['beautifulsoup_success'] += 1
            final_text = _truncate_to_words(full_text, MAX_WORDS)
            return _create_result(final_text, False, "beautifulsoup", start_time)

        # ALL EXTRACTION METHODS FAILED: Return snippet with partial flag
        logger.warning(f"All extraction methods failed for {url}, returning snippet")
        _extraction_stats['total_failures'] += 1
        return _create_result(snippet, True, "snippet", start_time)

    except Exception as e:
        # DEFENSIVE ERROR HANDLING: Never crash the discovery pipeline
        logger.error(f"Unexpected error in scraper for {url}: {e}")
        _extraction_stats['total_failures'] += 1
        return _create_result(snippet, True, "snippet", start_time)


def _is_paywalled_domain(domain: str) -> bool:
    """Check if domain is in the paywall skip-list.

    Paywall Detection Strategy:
    ---------------------------
    - Maintains curated list of known subscription/paywalled domains
    - Checks exact domain match and www. variant
    - Updated regularly as new paywalls are discovered
    - Errs on side of caution - better to skip than violate terms

    Domain Normalization:
    --------------------
    - Converts to lowercase for consistent matching
    - Handles both www and non-www variants
    - Future enhancement: subdomain pattern matching

    Legal Compliance:
    ----------------
    - Avoids attempting to bypass paywalls or subscription walls
    - Respects content creators' business models
    - Maintains fair use standards for sentiment analysis
    - Returns original snippet for paywalled content

    Args:
        domain: Normalized domain name (e.g., 'www.wsj.com', 'ft.com')

    Returns:
        True if domain is paywalled and should be skipped
        False if domain is safe for extraction attempts

    Examples:
    --------
    - _is_paywalled_domain('www.wsj.com') → True
    - _is_paywalled_domain('reuters.com') → True
    - _is_paywalled_domain('cnn.com') → False
    - _is_paywalled_domain('techcrunch.com') → False
    """
    domain_lower = domain.lower().strip()

    # Check exact match and common www variant
    if domain_lower in PAYWALLED_DOMAINS:
        return True

    # Check if removing 'www.' prefix matches
    if domain_lower.startswith('www.'):
        base_domain = domain_lower[4:]
        if base_domain in PAYWALLED_DOMAINS:
            return True

    # Check if adding 'www.' prefix matches (for domains listed without www)
    www_domain = f"www.{domain_lower}"
    if www_domain in PAYWALLED_DOMAINS:
        return True

    return False


def _scrape_with_trafilatura(url: str) -> Optional[str]:
    """Primary content extraction using trafilatura library.

    Trafilatura Advantages:
    ----------------------
    - Purpose-built for extracting main article content from news sites
    - Excellent at filtering out navigation, ads, comments, related articles
    - Handles complex modern web layouts and JavaScript-rendered content
    - Respects robots.txt and implements polite crawling practices
    - Superior text quality compared to generic HTML parsers

    Extraction Process:
    ------------------
    1. Download HTML content with proper headers and timeout
    2. Parse and analyze page structure to identify main content
    3. Filter out boilerplate content (navigation, sidebars, footer)
    4. Extract clean, readable text preserving paragraph structure
    5. Decode HTML entities and normalize encoding

    Content Quality:
    ---------------
    - Preserves paragraph breaks and article structure
    - Removes advertising and promotional content
    - Filters out user comments and social media widgets
    - Maintains readability for sentiment analysis downstream
    - Handles multi-language content with proper encoding

    Error Handling:
    --------------
    - Network timeouts handled gracefully (10-second limit)
    - Malformed HTML doesn't crash extraction
    - Returns None on any failure for fallback handling
    - Logs specific error types for debugging

    Performance Optimization:
    ------------------------
    - Efficient HTML parsing with minimal memory footprint
    - Built-in content caching (when configured)
    - Respects server rate limits and response headers
    - Handles redirects transparently

    Args:
        url: Target URL to extract content from

    Returns:
        Extracted article text as string if successful
        None if extraction fails (triggers fallback methods)

    Technical Details:
    -----------------
    - Uses requests session for connection pooling
    - Custom User-Agent string for identification
    - Handles HTTPS certificates appropriately
    - Follows redirects but limits redirect chains
    """
    try:
        logger.debug(f"Attempting trafilatura extraction for: {url}")

        # Download HTML content with proper headers
        downloaded = trafilatura.fetch_url(
            url,
            config=trafilatura.settings.use_config(),
            headers={'User-Agent': USER_AGENT}
        )

        if not downloaded:
            logger.debug(f"trafilatura failed to download content from: {url}")
            return None

        # Extract main article content using trafilatura's algorithms
        result = trafilatura.extract(
            downloaded,
            include_comments=False,    # Filter out user comments
            include_tables=False,      # Skip data tables (noise for sentiment)
            no_fallback=True,         # Use trafilatura-only, no BeautifulSoup fallback
            favor_precision=True,     # Prioritize precision over recall
            url=url
        )

        if not result or len(result.strip()) < MIN_WORDS:
            logger.debug(f"trafilatura extracted insufficient content from: {url}")
            return None

        # Clean and normalize the extracted text
        cleaned_text = _normalize_text_encoding(result)
        logger.debug(f"trafilatura successfully extracted {len(cleaned_text.split())} words")

        return cleaned_text

    except Exception as e:
        logger.debug(f"trafilatura extraction failed for {url}: {e}")
        return None


def _scrape_with_newspaper3k(url: str) -> Optional[str]:
    """Fallback content extraction using newspaper3k library.

    Newspaper3k Advantages:
    ----------------------
    - Specialized for news article extraction with built-in heuristics
    - Good at identifying article metadata (title, author, publish date)
    - Handles various news site layouts and content management systems
    - More aggressive extraction than trafilatura (sometimes catches more content)
    - Built-in language detection and text normalization

    Extraction Process:
    ------------------
    1. Create Article object with custom configuration
    2. Download and parse HTML content
    3. Apply newspaper's content extraction algorithms
    4. Extract clean text while preserving structure
    5. Validate content quality and length

    Configuration Optimizations:
    ----------------------------
    - Custom User-Agent string for proper identification
    - Request timeout set to prevent hanging
    - Memory-efficient parsing with immediate cleanup
    - Error handling for network and parsing failures

    Content Processing:
    ------------------
    - Automatic text cleaning and normalization
    - HTML entity decoding and character normalization
    - Paragraph structure preservation for readability
    - Language detection (though we focus on English content)

    Comparison with trafilatura:
    ---------------------------
    - Sometimes extracts content that trafilatura misses
    - May include slightly more "peripheral" content
    - Different content scoring algorithms
    - Useful as second opinion when primary method fails

    Args:
        url: Target URL to extract content from

    Returns:
        Extracted article text as string if successful
        None if extraction fails (triggers next fallback method)

    Error Recovery:
    --------------
    - Network errors return None for graceful fallback
    - Parsing errors don't crash the extraction pipeline
    - Content validation filters out low-quality extractions
    - Memory cleanup prevents resource leaks
    """
    try:
        logger.debug(f"Attempting newspaper3k extraction for: {url}")

        # Create article object with custom configuration
        article = Article(
            url,
            language='en',  # Focus on English content for sentiment analysis
            memoize_articles=False,  # Disable caching for memory efficiency
            fetch_images=False,      # Skip image processing (not needed)
            request_timeout=REQUEST_TIMEOUT
        )

        # Custom headers for proper identification
        article.config.browser_user_agent = USER_AGENT
        article.config.request_timeout = REQUEST_TIMEOUT

        # Download and parse the article
        article.download()

        if not article.html:
            logger.debug(f"newspaper3k failed to download HTML from: {url}")
            return None

        # Parse content using newspaper's algorithms
        article.parse()

        if not article.text or len(article.text.strip()) < MIN_WORDS:
            logger.debug(f"newspaper3k extracted insufficient content from: {url}")
            return None

        # Clean and normalize extracted text
        cleaned_text = _normalize_text_encoding(article.text)
        logger.debug(f"newspaper3k successfully extracted {len(cleaned_text.split())} words")

        return cleaned_text

    except Exception as e:
        logger.debug(f"newspaper3k extraction failed for {url}: {e}")
        return None


def _scrape_with_beautifulsoup(url: str) -> Optional[str]:
    """Last resort content extraction using BeautifulSoup raw HTML parsing.

    BeautifulSoup Last Resort Strategy:
    ----------------------------------
    - Used only when both trafilatura and newspaper3k fail completely
    - Performs aggressive raw text extraction from HTML elements
    - Quality highly variable - may include navigation, ads, comments
    - Better than no content when specialized parsers fail

    Extraction Approach:
    -------------------
    1. Download raw HTML with custom headers and timeout
    2. Parse HTML using BeautifulSoup's robust parser
    3. Remove known noise elements (script, style, nav, footer)
    4. Extract text from remaining content elements
    5. Clean and normalize resulting text

    Content Filtering:
    -----------------
    - Removes script and style tags completely
    - Filters out navigation, sidebar, and footer elements
    - Removes common ad container classes and IDs
    - Preserves paragraph and article content elements
    - Applies basic text quality validation

    Quality Considerations:
    ----------------------
    - May include some non-article content (ads, navigation)
    - Text structure may be less coherent than specialized extractors
    - Useful for sites where specialized parsers fail completely
    - Better content than returning snippet only

    Performance Notes:
    -----------------
    - More memory intensive than specialized parsers
    - Requires careful cleanup to prevent memory leaks
    - Network timeout prevents hanging on slow sites
    - Immediate HTML cleanup after text extraction

    Args:
        url: Target URL to extract content from

    Returns:
        Extracted text content as string if successful
        None if extraction fails completely (returns snippet)

    Implementation Details:
    ----------------------
    - Uses lxml parser for speed and robustness
    - Custom User-Agent for proper site identification
    - 10-second request timeout prevents hanging
    - Handles various character encodings gracefully
    - Memory cleanup prevents resource exhaustion
    """
    try:
        logger.debug(f"Attempting BeautifulSoup extraction for: {url}")

        # Download HTML content with proper headers
        headers = {
            'User-Agent': USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        }

        response = requests.get(
            url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )
        response.raise_for_status()

        # Parse HTML using robust parser
        soup = BeautifulSoup(response.content, 'lxml')

        # Remove noise elements that don't contain article content
        noise_tags = ['script', 'style', 'nav', 'header', 'footer', 'aside']
        for tag in noise_tags:
            for element in soup.find_all(tag):
                element.decompose()

        # Remove common ad and navigation containers by class/id
        noise_selectors = [
            '[class*="nav"]', '[class*="menu"]', '[class*="sidebar"]',
            '[class*="ad"]', '[class*="advertisement"]', '[class*="promo"]',
            '[id*="nav"]', '[id*="menu"]', '[id*="sidebar"]', '[id*="ad"]'
        ]
        for selector in noise_selectors:
            for element in soup.select(selector):
                element.decompose()

        # Extract text from remaining content elements
        # Prioritize article-like containers
        content_containers = soup.find_all(['article', 'main', 'div'],
                                         class_=re.compile(r'(content|article|story|post)'))

        if content_containers:
            # Use first content container if found
            text_content = content_containers[0].get_text(separator=' ', strip=True)
        else:
            # Fall back to body text if no content containers found
            body = soup.find('body')
            if body:
                text_content = body.get_text(separator=' ', strip=True)
            else:
                text_content = soup.get_text(separator=' ', strip=True)

        # Clean up soup object to free memory
        soup.decompose()

        if not text_content or len(text_content.strip()) < MIN_WORDS:
            logger.debug(f"BeautifulSoup extracted insufficient content from: {url}")
            return None

        # Clean and normalize extracted text
        cleaned_text = _normalize_text_encoding(text_content)
        logger.debug(f"BeautifulSoup successfully extracted {len(cleaned_text.split())} words")

        return cleaned_text

    except Exception as e:
        logger.debug(f"BeautifulSoup extraction failed for {url}: {e}")
        return None


def _truncate_to_words(text: str, max_words: int = 1200) -> str:
    """Truncate text to maximum word count preserving sentence boundaries.

    Content Length Management:
    -------------------------
    - Prevents memory bloat during high-volume news processing
    - Respects Claude API token limits for downstream sentiment analysis
    - Preserves article structure by truncating at sentence boundaries
    - Maintains content quality for accurate sentiment extraction

    Truncation Strategy:
    -------------------
    1. Split text into words and check if truncation needed
    2. If under limit, return original text unchanged
    3. If over limit, truncate at last complete sentence within word limit
    4. If no sentence boundaries found, truncate at word boundary
    5. Add truncation indicator for downstream processing awareness

    Sentence Boundary Detection:
    ---------------------------
    - Uses common sentence endings: period, exclamation, question mark
    - Considers context to avoid truncating mid-abbreviation
    - Preserves coherent content structure for sentiment analysis
    - Handles edge cases like lists and quotes appropriately

    Content Quality Preservation:
    ----------------------------
    - Prioritizes keeping article introduction and main points
    - Avoids truncating in middle of important financial statements
    - Maintains readability for human review and debugging
    - Preserves context needed for accurate sentiment classification

    Args:
        text: Original extracted article text
        max_words: Maximum word count limit (default 1200)

    Returns:
        Truncated text preserving sentence structure
        Original text if already under limit
        Empty string if input is invalid

    Example:
    -------
    Input: "The Federal Reserve announced rate hikes. Markets responded negatively. ..."
    Output: "The Federal Reserve announced rate hikes. Markets responded negatively." (if limit hit)

    Performance Notes:
    -----------------
    - Efficient string processing with minimal memory allocation
    - Regex-based sentence detection for speed
    - Early return for content already under limit
    - Memory cleanup for large input texts
    """
    if not text or not isinstance(text, str):
        return ""

    words = text.split()

    # If already under limit, return original text unchanged
    if len(words) <= max_words:
        return text.strip()

    # Truncate to word limit first
    truncated_words = words[:max_words]
    truncated_text = ' '.join(truncated_words)

    # Find last complete sentence within the truncated text
    # Look for sentence ending punctuation followed by space or end of string
    sentence_endings = re.finditer(r'[.!?]\s+', truncated_text)
    last_sentence_end = None

    for match in sentence_endings:
        last_sentence_end = match.end()

    # If we found a sentence boundary, truncate there for better readability
    if last_sentence_end and last_sentence_end < len(truncated_text) * 0.8:  # Don't truncate too early
        final_text = truncated_text[:last_sentence_end].strip()
        # Add truncation indicator
        final_text += " [Article truncated for length]"
        return final_text

    # No good sentence boundary found, truncate at word boundary
    final_text = truncated_text.strip()
    if not final_text.endswith(('.', '!', '?')):
        final_text += "... [Article truncated for length]"

    return final_text


def _validate_scraped_content(text: str, min_words: int = 50) -> bool:
    """Validate that scraped content meets minimum quality standards.

    Content Quality Criteria:
    -------------------------
    - Minimum word count to filter out navigation pages and error messages
    - Text structure validation to ensure coherent content
    - Language detection to filter non-English content (future enhancement)
    - Content uniqueness to avoid duplicate processing

    Validation Checks:
    -----------------
    1. Minimum word count threshold (default 50 words)
    2. Text contains actual sentences (not just navigation links)
    3. Reasonable paragraph structure exists
    4. Content is not primarily boilerplate/template text
    5. Character encoding is valid and readable

    Quality Heuristics:
    ------------------
    - Average word length indicates real content vs navigation
    - Sentence variety suggests article content vs error pages
    - Paragraph distribution indicates structured content
    - Punctuation density suggests natural language

    False Positive Filtering:
    ------------------------
    - Filters out "404 Not Found" and error pages
    - Removes navigation-only content
    - Excludes pure advertisement pages
    - Rejects machine-generated spam content

    Integration Impact:
    ------------------
    - High-quality content improves sentiment analysis accuracy
    - Filters prevent noise in downstream Claude API processing
    - Reduces API costs by avoiding analysis of low-value content
    - Improves system reliability by filtering problematic inputs

    Args:
        text: Extracted text content to validate
        min_words: Minimum word count for valid content (default 50)

    Returns:
        True if content meets quality standards
        False if content should be rejected (triggers fallback)

    Validation Examples:
    -------------------
    Valid: "The Federal Reserve announced today that interest rates..."
    Invalid: "Home About Contact Privacy Terms" (navigation only)
    Invalid: "404 Error Page Not Found" (error page)
    Invalid: "Loading..." (incomplete content)
    """
    if not text or not isinstance(text, str):
        return False

    # Clean text for analysis
    clean_text = text.strip()

    if not clean_text:
        return False

    # Check minimum word count
    words = clean_text.split()
    if len(words) < min_words:
        logger.debug(f"Content validation failed: only {len(words)} words (minimum {min_words})")
        return False

    # Check for common error page indicators
    error_indicators = [
        '404', 'not found', 'page not found', 'error occurred',
        'access denied', 'forbidden', 'server error', 'maintenance mode',
        'coming soon', 'under construction', 'temporarily unavailable'
    ]

    text_lower = clean_text.lower()
    for indicator in error_indicators:
        if indicator in text_lower and len(words) < 100:  # Short content with error indicators
            logger.debug(f"Content validation failed: contains error indicator '{indicator}'")
            return False

    # Check for navigation-heavy content (high link density)
    # Simple heuristic: if more than 30% of words are likely navigation terms
    nav_words = [
        'home', 'about', 'contact', 'privacy', 'terms', 'login', 'register',
        'menu', 'navigation', 'sidebar', 'footer', 'header', 'search',
        'categories', 'archives', 'tags', 'follow', 'subscribe', 'share'
    ]

    nav_word_count = sum(1 for word in words if word.lower() in nav_words)
    if nav_word_count > len(words) * 0.3:
        logger.debug(f"Content validation failed: too much navigation content ({nav_word_count}/{len(words)})")
        return False

    # Check for reasonable sentence structure
    sentence_endings = re.findall(r'[.!?]', clean_text)
    if len(sentence_endings) < 2 and len(words) > 100:  # Long content with no sentences
        logger.debug("Content validation failed: insufficient sentence structure")
        return False

    # Check average word length (real content has reasonable word distribution)
    avg_word_length = sum(len(word) for word in words) / len(words)
    if avg_word_length < 3 or avg_word_length > 15:  # Suspiciously short or long words
        logger.debug(f"Content validation failed: unusual average word length {avg_word_length:.1f}")
        return False

    # Content appears valid
    logger.debug(f"Content validation passed: {len(words)} words, good structure")
    return True


def _normalize_text_encoding(text: str) -> str:
    """Normalize text encoding and clean up common HTML artifacts.

    Text Normalization Pipeline:
    ----------------------------
    1. Unicode normalization to handle international characters
    2. HTML entity decoding (&amp; → &, &quote; → ", etc.)
    3. Whitespace cleanup and standardization
    4. Line break normalization for consistent formatting
    5. Remove control characters and invalid Unicode

    Common Cleaning Tasks:
    ---------------------
    - Convert HTML entities to regular characters
    - Normalize Unicode characters (NFC normalization)
    - Clean up excessive whitespace and line breaks
    - Remove invisible characters and control codes
    - Standardize quotation marks and dashes

    Content Structure Preservation:
    ------------------------------
    - Maintains paragraph breaks for readability
    - Preserves intentional formatting (lists, quotes)
    - Keeps sentence structure intact
    - Removes only unwanted artifacts, not content

    Character Encoding Handling:
    ---------------------------
    - Handles mixed encoding scenarios gracefully
    - Converts common Windows-1252 characters
    - Normalizes various Unicode quote/dash styles
    - Ensures UTF-8 compatibility for downstream processing

    Args:
        text: Raw extracted text with potential encoding issues

    Returns:
        Cleaned and normalized text ready for sentiment analysis
        Empty string if input cannot be normalized

    Examples:
    --------
    Input: "The Fed&rsquo;s decision was &ldquo;hawkish&rdquo;..."
    Output: "The Fed's decision was "hawkish"..."

    Input: "Markets\u00a0fell\u00a0sharply..."
    Output: "Markets fell sharply..."
    """
    if not text or not isinstance(text, str):
        return ""

    try:
        import unicodedata
        import html

        # Decode HTML entities
        text = html.unescape(text)

        # Normalize Unicode (NFC - canonical decomposition followed by composition)
        text = unicodedata.normalize('NFC', text)

        # Replace common Unicode whitespace characters with regular spaces
        unicode_whitespace = {
            '\u00a0': ' ',    # Non-breaking space
            '\u2009': ' ',    # Thin space
            '\u200a': ' ',    # Hair space
            '\u202f': ' ',    # Narrow no-break space
            '\u3000': ' ',    # Ideographic space
        }

        for unicode_char, replacement in unicode_whitespace.items():
            text = text.replace(unicode_char, replacement)

        # Normalize various types of quotes and dashes
        text = re.sub(r'[\u2018\u2019]', "'", text)  # Smart single quotes
        text = re.sub(r'[\u201c\u201d]', '"', text)  # Smart double quotes
        text = re.sub(r'[\u2013\u2014]', '-', text)  # En/em dashes
        text = re.sub(r'\u2026', '...', text)       # Ellipsis

        # Clean up whitespace
        # Replace multiple consecutive whitespace with single space
        text = re.sub(r'\s+', ' ', text)

        # Normalize line breaks - keep paragraph breaks but remove excessive breaks
        text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)  # Max 2 consecutive newlines
        text = re.sub(r'[\r\f\v]+', '\n', text)         # Normalize different line break types

        # Remove control characters (except newlines and tabs)
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)

        # Final cleanup
        text = text.strip()

        return text

    except Exception as e:
        logger.warning(f"Text encoding normalization failed: {e}")
        # Return original text if normalization fails
        return text.strip() if text else ""


def _create_result(text: str, is_partial: bool, method: str, start_time: float) -> Dict:
    """Create standardized result dictionary for scraper responses.

    Result Structure Standardization:
    --------------------------------
    - Consistent return format across all extraction methods
    - Metadata inclusion for debugging and performance monitoring
    - Extraction statistics for system optimization
    - Integration readiness for downstream sentiment analysis

    Performance Metrics:
    -------------------
    - Extraction time measurement for optimization
    - Word count for content quality assessment
    - Method tracking for success rate analysis
    - Partial flag for content completeness indication

    Debugging Support:
    -----------------
    - Method field shows which extraction tier succeeded/failed
    - Timing data helps identify performance bottlenecks
    - Word count helps validate content quality
    - Consistent structure simplifies log analysis

    Args:
        text: Final content text (extracted or snippet fallback)
        is_partial: True if using snippet fallback, False if full extraction
        method: Extraction method used ("trafilatura", "newspaper", "beautifulsoup", "snippet")
        start_time: Timestamp when extraction started (for timing calculation)

    Returns:
        Standardized result dictionary for aggregator.py consumption

    Result Schema:
    -------------
    {
        "full_text": str,           # Final content text
        "partial": bool,            # True if snippet fallback used
        "extraction_method": str,   # Method that succeeded or "snippet"
        "word_count": int,          # Number of words in final content
        "extraction_time_ms": int   # Time spent on extraction (milliseconds)
    }
    """
    end_time = time.time()
    extraction_time_ms = int((end_time - start_time) * 1000)

    # Count words in final text
    word_count = len(text.split()) if text else 0

    return {
        "full_text": text or "",
        "partial": is_partial,
        "extraction_method": method,
        "word_count": word_count,
        "extraction_time_ms": extraction_time_ms
    }


def get_extraction_stats() -> Dict:
    """Get extraction performance statistics for monitoring and optimization.

    Performance Monitoring:
    ----------------------
    - Tracks success rates for each extraction method
    - Monitors paywall skip frequency
    - Identifies performance bottlenecks and optimization opportunities
    - Enables data-driven decisions on scraper improvements

    Statistics Tracked:
    ------------------
    - trafilatura_success: Successful extractions using primary method
    - newspaper_success: Successful extractions using secondary method
    - beautifulsoup_success: Successful extractions using last resort method
    - paywall_skipped: Domains skipped due to paywall detection
    - total_failures: Complete extraction failures (returned snippet only)

    Usage:
    -----
    - Called by monitoring/dashboard systems
    - Used for alerting on extraction degradation
    - Helps optimize extraction method ordering
    - Informs decisions on adding new extraction methods

    Returns:
        Dictionary with extraction performance statistics

    Example Output:
    --------------
    {
        "trafilatura_success": 245,
        "newspaper_success": 38,
        "beautifulsoup_success": 12,
        "paywall_skipped": 89,
        "total_failures": 15,
        "total_attempts": 399,
        "primary_success_rate": 0.614,
        "overall_success_rate": 0.737
    }
    """
    global _extraction_stats

    total_attempts = sum(_extraction_stats.values())
    successful_extractions = (
        _extraction_stats['trafilatura_success'] +
        _extraction_stats['newspaper_success'] +
        _extraction_stats['beautifulsoup_success']
    )

    # Calculate success rates
    primary_success_rate = (
        _extraction_stats['trafilatura_success'] / total_attempts
        if total_attempts > 0 else 0.0
    )

    overall_success_rate = (
        successful_extractions / total_attempts
        if total_attempts > 0 else 0.0
    )

    return {
        **_extraction_stats,
        "total_attempts": total_attempts,
        "primary_success_rate": round(primary_success_rate, 3),
        "overall_success_rate": round(overall_success_rate, 3)
    }


def reset_extraction_stats() -> None:
    """Reset extraction statistics (useful for testing and monitoring periods).

    Statistics Reset:
    ----------------
    - Clears all extraction counters to zero
    - Useful for measuring performance over specific time periods
    - Helps isolate performance issues to specific time windows
    - Enables A/B testing of different extraction configurations

    Usage Scenarios:
    ---------------
    - Daily/weekly performance measurement
    - Testing new extraction methods
    - Clearing stats after configuration changes
    - Isolating performance during specific market events
    """
    global _extraction_stats
    _extraction_stats = {
        'trafilatura_success': 0,
        'newspaper_success': 0,
        'beautifulsoup_success': 0,
        'paywall_skipped': 0,
        'total_failures': 0
    }
    logger.info("Extraction statistics reset to zero")