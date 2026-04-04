"""Buy/sell signal generation logic."""


def generate_signal(sentiment: dict, price_data) -> dict:
    """Generate a trade signal from sentiment and price data.

    Args:
        sentiment: Sentiment result dict from engine.sentiment.analyze_sentiment.
        price_data: pandas DataFrame of historical prices from fetchers.market.

    Returns:
        Dict with keys:
            action (str: 'buy' | 'sell' | 'hold'),
            confidence (float, 0.0 to 1.0),
            reason (str).
    """
    pass


def apply_risk_filters(signal: dict, portfolio: dict) -> dict:
    """Apply risk management rules to a raw signal.

    Args:
        signal: Raw signal dict from generate_signal.
        portfolio: Current portfolio state dict with positions and cash.

    Returns:
        Filtered signal dict, possibly with action overridden to 'hold'.
    """
    pass


def rank_signals(signals: dict[str, dict]) -> list[tuple[str, dict]]:
    """Rank trade signals across multiple tickers by confidence.

    Args:
        signals: Mapping of ticker to signal dict.

    Returns:
        List of (ticker, signal) tuples sorted by confidence descending.
    """
    pass
