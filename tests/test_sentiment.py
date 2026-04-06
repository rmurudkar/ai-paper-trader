"""Tests for engine.sentiment module."""

import unittest
from unittest.mock import patch, MagicMock
import json
import sys
import os
from datetime import datetime

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.sentiment import (
    analyze_article_sentiment,
    analyze_newsapi_with_claude,
    batch_analyze_articles,
    get_ticker_sentiment_scores,
    _parse_claude_result,
    _fallback_parse,
    _VALID_URGENCY,
    _VALID_MATERIALITY,
    _VALID_TIME_HORIZON,
)


class TestSentimentAnalysis(unittest.TestCase):
    """Test sentiment analysis functions."""

    def setUp(self):
        """Set up test fixtures."""
        self.marketaux_article = {
            'source': 'marketaux',
            'title': 'Apple stock soars on earnings',
            'ticker': 'AAPL',
            'sentiment_score': 0.75,
            'published_at': '2026-04-05T14:30:00Z'
        }

        self.massive_article = {
            'source': 'massive',
            'title': 'Tesla and Amazon face headwinds',
            'tickers': ['TSLA', 'AMZN'],
            'sentiment_score': -0.4,
            'published_at': '2026-04-05T13:15:00Z'
        }

        self.newsapi_article = {
            'source': 'newsapi',
            'title': 'Fed raises interest rates',
            'full_text': 'The Federal Reserve announced a 0.25% interest rate hike today, citing inflation concerns. This could impact technology stocks like Apple (AAPL) and Microsoft (MSFT) negatively as higher rates typically reduce valuations for growth stocks.',
            'tickers': ['AAPL', 'MSFT'],
            'partial': False,
            'published_at': '2026-04-05T15:00:00Z'
        }

        self.partial_article = {
            'source': 'newsapi',
            'title': 'Short headline only',
            'full_text': 'Brief snippet with limited info.',
            'tickers': ['SPY'],
            'partial': True,
            'published_at': '2026-04-05T12:00:00Z'
        }

        self.missing_text_article = {
            'source': 'newsapi',
            'title': 'No content article',
            'tickers': ['NVDA'],
            'partial': False,
            'published_at': '2026-04-05T11:00:00Z'
        }

    def test_analyze_marketaux_article(self):
        """Test analysis of Marketaux article (pre-computed sentiment)."""
        result = analyze_article_sentiment(self.marketaux_article)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['ticker'], 'AAPL')
        self.assertEqual(result[0]['sentiment_score'], 0.75)
        self.assertEqual(result[0]['source'], 'marketaux')
        self.assertIn('Pre-computed sentiment from Marketaux', result[0]['reasoning'])
        # Pre-computed sources get default enrichment
        self.assertEqual(result[0]['urgency'], 'standard')
        self.assertEqual(result[0]['materiality'], 'unknown')
        self.assertEqual(result[0]['time_horizon'], 'medium_term')

    def test_analyze_massive_article(self):
        """Test analysis of Massive article (pre-computed sentiment)."""
        result = analyze_article_sentiment(self.massive_article)

        self.assertEqual(len(result), 2)

        # Check both tickers got the same sentiment
        tickers = [r['ticker'] for r in result]
        self.assertIn('TSLA', tickers)
        self.assertIn('AMZN', tickers)

        for res in result:
            self.assertEqual(res['sentiment_score'], -0.4)
            self.assertEqual(res['source'], 'massive')
            self.assertIn('Pre-computed sentiment from Massive', res['reasoning'])
            # Pre-computed sources get default enrichment
            self.assertEqual(res['urgency'], 'standard')
            self.assertEqual(res['materiality'], 'unknown')
            self.assertEqual(res['time_horizon'], 'medium_term')

    @patch('engine.sentiment.anthropic.Anthropic')
    def test_analyze_newsapi_article_success(self, mock_anthropic):
        """Test analysis of NewsAPI article with successful Claude call."""
        # Mock Claude response with enriched fields
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = json.dumps({
            "sentiment_score": -0.3,
            "urgency": "breaking",
            "materiality": "high",
            "time_horizon": "short_term",
            "reasoning": "Fed rate hike typically negative for growth stocks"
        })

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.return_value = mock_client

        result = analyze_article_sentiment(self.newsapi_article)

        self.assertEqual(len(result), 2)  # AAPL and MSFT

        for res in result:
            self.assertEqual(res['sentiment_score'], -0.3)
            self.assertEqual(res['source'], 'newsapi')
            self.assertEqual(res['reasoning'], 'Fed rate hike typically negative for growth stocks')
            self.assertEqual(res['urgency'], 'breaking')
            self.assertEqual(res['materiality'], 'high')
            self.assertEqual(res['time_horizon'], 'short_term')

    @patch('engine.sentiment.anthropic.Anthropic')
    def test_analyze_newsapi_article_json_parse_error(self, mock_anthropic):
        """Test NewsAPI article analysis with JSON parse error fallback."""
        # Mock Claude response with non-JSON text
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = "The sentiment is very negative due to market concerns."

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.return_value = mock_client

        result = analyze_article_sentiment(self.newsapi_article)

        self.assertEqual(len(result), 2)

        for res in result:
            self.assertEqual(res['sentiment_score'], -0.8)  # Should parse "very negative" to -0.8
            self.assertEqual(res['source'], 'newsapi')
            self.assertTrue(len(res['reasoning']) > 0)
            # Fallback parse returns default enrichment values
            self.assertEqual(res['urgency'], 'standard')
            self.assertEqual(res['materiality'], 'unknown')
            self.assertEqual(res['time_horizon'], 'medium_term')

    def test_analyze_article_missing_full_text(self):
        """Test analysis of article without full_text."""
        result = analyze_article_sentiment(self.missing_text_article)

        self.assertEqual(len(result), 0)  # Should return empty list

    def test_analyze_article_partial_content(self):
        """Test analysis of partial article (snippet-only)."""
        with patch('engine.sentiment.anthropic.Anthropic') as mock_anthropic:
            mock_response = MagicMock()
            mock_response.content = [MagicMock()]
            mock_response.content[0].text = json.dumps({
                "sentiment_score": 0.0,
                "urgency": "standard",
                "materiality": "low",
                "time_horizon": "medium_term",
                "reasoning": "Limited information available"
            })

            mock_client = MagicMock()
            mock_client.messages.create.return_value = mock_response
            mock_anthropic.return_value = mock_client

            result = analyze_article_sentiment(self.partial_article)

            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]['sentiment_score'], 0.0)
            self.assertEqual(result[0]['urgency'], 'standard')
            self.assertEqual(result[0]['materiality'], 'low')
            self.assertEqual(result[0]['time_horizon'], 'medium_term')

    def test_analyze_unknown_source(self):
        """Test analysis of article with unknown source."""
        unknown_article = {
            'source': 'unknown_source',
            'title': 'Some title',
            'ticker': 'AAPL'
        }

        result = analyze_article_sentiment(unknown_article)
        self.assertEqual(len(result), 0)

    @patch('engine.sentiment.anthropic.Anthropic')
    def test_analyze_newsapi_with_claude_success(self, mock_anthropic):
        """Test direct Claude analysis function."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = json.dumps({
            "sentiment_score": 0.6,
            "urgency": "breaking",
            "materiality": "high",
            "time_horizon": "short_term",
            "reasoning": "Strong quarterly earnings beat expectations"
        })

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.return_value = mock_client

        result = analyze_newsapi_with_claude("Apple beats earnings expectations", "AAPL")

        self.assertEqual(result['sentiment_score'], 0.6)
        self.assertEqual(result['reasoning'], "Strong quarterly earnings beat expectations")
        self.assertEqual(result['urgency'], 'breaking')
        self.assertEqual(result['materiality'], 'high')
        self.assertEqual(result['time_horizon'], 'short_term')

        # Verify Claude was called with correct parameters
        mock_client.messages.create.assert_called_once()
        call_args = mock_client.messages.create.call_args
        self.assertEqual(call_args[1]['model'], 'claude-3-haiku-20240307')
        self.assertEqual(call_args[1]['temperature'], 0.1)
        self.assertEqual(call_args[1]['max_tokens'], 400)

    @patch('engine.sentiment.anthropic.Anthropic')
    def test_analyze_newsapi_with_claude_api_error(self, mock_anthropic):
        """Test Claude analysis with API error."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API Error")
        mock_anthropic.return_value = mock_client

        result = analyze_newsapi_with_claude("Some text", "AAPL")

        self.assertEqual(result['sentiment_score'], 0.0)
        self.assertIn("Analysis failed", result['reasoning'])

    @patch('engine.sentiment.anthropic.Anthropic')
    def test_analyze_newsapi_text_truncation(self, mock_anthropic):
        """Test that long articles are truncated before sending to Claude."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = json.dumps({
            "sentiment_score": 0.0,
            "urgency": "standard",
            "materiality": "low",
            "time_horizon": "medium_term",
            "reasoning": "Analysis complete"
        })

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.return_value = mock_client

        # Create text with more than 1200 words
        long_text = ' '.join(['word'] * 1500)

        result = analyze_newsapi_with_claude(long_text, "AAPL")

        # Verify the API was called
        mock_client.messages.create.assert_called_once()
        call_args = mock_client.messages.create.call_args

        # Check that the text in the prompt was truncated
        prompt = call_args[1]['messages'][0]['content']
        words_in_prompt = len(prompt.split())
        self.assertLess(words_in_prompt, 1400)  # Should be around 1200 + prompt overhead

    def test_batch_analyze_articles(self):
        """Test batch analysis of multiple articles."""
        with patch('engine.sentiment.analyze_article_sentiment') as mock_analyze:
            # Mock return values
            mock_analyze.side_effect = [
                [{'ticker': 'AAPL', 'sentiment_score': 0.5, 'source': 'marketaux', 'reasoning': 'test'}],
                [{'ticker': 'TSLA', 'sentiment_score': -0.3, 'source': 'massive', 'reasoning': 'test'}],
                []  # Empty result for third article
            ]

            articles = [self.marketaux_article, self.massive_article, self.missing_text_article]
            result = batch_analyze_articles(articles)

            self.assertEqual(len(result), 2)  # Two successful analyses
            mock_analyze.assert_called()

    def test_batch_analyze_articles_with_error(self):
        """Test batch analysis with some articles failing."""
        with patch('engine.sentiment.analyze_article_sentiment') as mock_analyze:
            # First call succeeds, second fails, third succeeds
            mock_analyze.side_effect = [
                [{'ticker': 'AAPL', 'sentiment_score': 0.5, 'source': 'marketaux', 'reasoning': 'test'}],
                Exception("Analysis failed"),
                [{'ticker': 'MSFT', 'sentiment_score': 0.2, 'source': 'newsapi', 'reasoning': 'test'}]
            ]

            articles = [self.marketaux_article, self.massive_article, self.newsapi_article]
            result = batch_analyze_articles(articles)

            self.assertEqual(len(result), 2)  # Two successful analyses

    def test_get_ticker_sentiment_scores_single_ticker(self):
        """Test aggregating sentiment scores for a single ticker."""
        sentiment_results = [
            {'ticker': 'AAPL', 'sentiment_score': 0.7, 'source': 'marketaux'},
            {'ticker': 'AAPL', 'sentiment_score': 0.3, 'source': 'newsapi'},
            {'ticker': 'MSFT', 'sentiment_score': -0.2, 'source': 'massive'}
        ]

        result = get_ticker_sentiment_scores('AAPL', sentiment_results)

        self.assertEqual(result['ticker'], 'AAPL')
        self.assertEqual(result['sentiment_score'], 0.5)  # Average of 0.7 and 0.3
        self.assertEqual(result['article_count'], 2)
        self.assertEqual(result['source_breakdown']['marketaux'], 1)
        self.assertEqual(result['source_breakdown']['newsapi'], 1)
        self.assertGreater(result['confidence'], 0)

    def test_get_ticker_sentiment_scores_no_data(self):
        """Test aggregating sentiment for ticker with no data."""
        sentiment_results = [
            {'ticker': 'MSFT', 'sentiment_score': 0.2, 'source': 'newsapi'}
        ]

        result = get_ticker_sentiment_scores('AAPL', sentiment_results)

        self.assertEqual(result['ticker'], 'AAPL')
        self.assertEqual(result['sentiment_score'], 0.0)
        self.assertEqual(result['article_count'], 0)
        self.assertEqual(result['confidence'], 0.0)
        self.assertEqual(result['source_breakdown'], {})

    def test_get_ticker_sentiment_scores_case_insensitive(self):
        """Test that ticker matching is case-insensitive."""
        sentiment_results = [
            {'ticker': 'aapl', 'sentiment_score': 0.5, 'source': 'marketaux'},
            {'ticker': 'AAPL', 'sentiment_score': 0.7, 'source': 'newsapi'}
        ]

        result = get_ticker_sentiment_scores('AAPL', sentiment_results)
        self.assertEqual(result['article_count'], 2)

        result_lower = get_ticker_sentiment_scores('aapl', sentiment_results)
        self.assertEqual(result_lower['article_count'], 2)

    def test_get_ticker_sentiment_scores_confidence_calculation(self):
        """Test confidence calculation based on article count and source diversity."""
        # Single article, single source = low confidence
        single_result = [{'ticker': 'AAPL', 'sentiment_score': 0.5, 'source': 'marketaux'}]
        result = get_ticker_sentiment_scores('AAPL', single_result)
        low_confidence = result['confidence']

        # Multiple articles, multiple sources = higher confidence
        multi_result = [
            {'ticker': 'AAPL', 'sentiment_score': 0.5, 'source': 'marketaux'},
            {'ticker': 'AAPL', 'sentiment_score': 0.3, 'source': 'newsapi'},
            {'ticker': 'AAPL', 'sentiment_score': 0.7, 'source': 'massive'}
        ]
        result = get_ticker_sentiment_scores('AAPL', multi_result)
        high_confidence = result['confidence']

        self.assertLess(low_confidence, high_confidence)

    def test_sentiment_score_clamping(self):
        """Test that sentiment scores are properly clamped to -1.0 to 1.0 range."""
        with patch('engine.sentiment.anthropic.Anthropic') as mock_anthropic:
            mock_response = MagicMock()
            mock_response.content = [MagicMock()]
            mock_response.content[0].text = json.dumps({
                "sentiment_score": 2.5,  # Out of range
                "reasoning": "Very positive"
            })

            mock_client = MagicMock()
            mock_client.messages.create.return_value = mock_response
            mock_anthropic.return_value = mock_client

            result = analyze_newsapi_with_claude("Great news!", "AAPL")

            # Should be clamped to 1.0
            self.assertEqual(result['sentiment_score'], 1.0)

    def test_empty_articles_list(self):
        """Test handling of empty articles list."""
        result = batch_analyze_articles([])
        self.assertEqual(len(result), 0)

        result = get_ticker_sentiment_scores('AAPL', [])
        self.assertEqual(result['sentiment_score'], 0.0)
        self.assertEqual(result['article_count'], 0)


class TestSentimentIntegration(unittest.TestCase):
    """Integration tests for sentiment analysis workflow."""

    @patch('engine.sentiment.anthropic.Anthropic')
    def test_full_workflow(self, mock_anthropic):
        """Test complete sentiment analysis workflow."""
        # Mock Claude response
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = json.dumps({
            "sentiment_score": 0.4,
            "reasoning": "Mixed signals in the market"
        })

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.return_value = mock_client

        # Simulate a full workflow
        articles = [
            {
                'source': 'marketaux',
                'title': 'Apple earnings beat',
                'ticker': 'AAPL',
                'sentiment_score': 0.8
            },
            {
                'source': 'newsapi',
                'title': 'Market volatility concerns',
                'full_text': 'Market showing signs of volatility as Apple and Microsoft face headwinds.',
                'tickers': ['AAPL', 'MSFT'],
                'partial': False
            }
        ]

        # Run batch analysis
        sentiment_results = batch_analyze_articles(articles)

        # Should have 3 results: 1 from marketaux + 2 from newsapi (AAPL, MSFT)
        self.assertEqual(len(sentiment_results), 3)

        # Get aggregated sentiment for AAPL
        aapl_sentiment = get_ticker_sentiment_scores('AAPL', sentiment_results)

        self.assertEqual(aapl_sentiment['ticker'], 'AAPL')
        self.assertEqual(aapl_sentiment['article_count'], 2)
        self.assertIn('marketaux', aapl_sentiment['source_breakdown'])
        self.assertIn('newsapi', aapl_sentiment['source_breakdown'])


if __name__ == '__main__':
    # Set environment variable to avoid API key errors in tests
    os.environ.setdefault('ANTHROPIC_API_KEY', 'test-key')

    unittest.main()