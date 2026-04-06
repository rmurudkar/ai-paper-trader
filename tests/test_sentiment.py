"""Tests for engine.sentiment module."""

import unittest
from unittest.mock import patch, MagicMock
import json
import sys
import os
from datetime import datetime, timezone

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.sentiment import (
    analyze_article_sentiment,
    analyze_newsapi_with_claude,
    batch_analyze_articles,
    get_ticker_sentiment_scores,
    record_ticker_sentiment,
    batch_record_sentiments,
    _parse_claude_result,
    _fallback_parse,
    _compute_article_weight,
    _VALID_URGENCY,
    _VALID_MATERIALITY,
    _VALID_TIME_HORIZON,
    _SOURCE_CREDIBILITY,
    _MATERIALITY_WEIGHT,
    _URGENCY_WEIGHT,
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
        # published_at threaded through
        self.assertEqual(result[0]['published_at'], '2026-04-05T14:30:00Z')

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
            self.assertEqual(res['published_at'], '2026-04-05T13:15:00Z')

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
            {'ticker': 'AAPL', 'sentiment_score': 0.7, 'source': 'marketaux',
             'urgency': 'standard', 'materiality': 'unknown', 'time_horizon': 'medium_term'},
            {'ticker': 'AAPL', 'sentiment_score': 0.3, 'source': 'newsapi',
             'urgency': 'standard', 'materiality': 'unknown', 'time_horizon': 'medium_term'},
            {'ticker': 'MSFT', 'sentiment_score': -0.2, 'source': 'massive',
             'urgency': 'standard', 'materiality': 'unknown', 'time_horizon': 'medium_term'}
        ]

        result = get_ticker_sentiment_scores('AAPL', sentiment_results)

        self.assertEqual(result['ticker'], 'AAPL')
        # Weighted average: newsapi (1.0) vs marketaux (0.8) credibility, same other weights
        # newsapi weight: 1.0 * 0.5 * 1.0 * 1.0 = 0.5, marketaux: 0.8 * 0.5 * 1.0 * 1.0 = 0.4
        # weighted avg = (0.7*0.4 + 0.3*0.5) / (0.4+0.5) = 0.43 / 0.9 ≈ 0.478
        self.assertAlmostEqual(result['sentiment_score'], 0.478, places=2)
        self.assertEqual(result['article_count'], 2)
        self.assertEqual(result['source_breakdown']['marketaux'], 1)
        self.assertEqual(result['source_breakdown']['newsapi'], 1)
        self.assertGreater(result['confidence'], 0)
        self.assertIn('individual_weights', result)

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
                "urgency": "breaking",
                "materiality": "high",
                "time_horizon": "short_term",
                "reasoning": "Very positive"
            })

            mock_client = MagicMock()
            mock_client.messages.create.return_value = mock_response
            mock_anthropic.return_value = mock_client

            result = analyze_newsapi_with_claude("Great news!", "AAPL")

            # Should be clamped to 1.0
            self.assertEqual(result['sentiment_score'], 1.0)
            # Other fields should pass through normally
            self.assertEqual(result['urgency'], 'breaking')
            self.assertEqual(result['materiality'], 'high')

    def test_empty_articles_list(self):
        """Test handling of empty articles list."""
        result = batch_analyze_articles([])
        self.assertEqual(len(result), 0)

        result = get_ticker_sentiment_scores('AAPL', [])
        self.assertEqual(result['sentiment_score'], 0.0)
        self.assertEqual(result['article_count'], 0)


class TestParseClaudeResult(unittest.TestCase):
    """Test _parse_claude_result validation and defaults."""

    def test_valid_complete_result(self):
        """Test parsing a fully valid Claude response."""
        result = _parse_claude_result({
            'sentiment_score': 0.6,
            'urgency': 'breaking',
            'materiality': 'high',
            'time_horizon': 'intraday',
            'reasoning': 'Earnings beat expectations',
        })
        self.assertEqual(result['sentiment_score'], 0.6)
        self.assertEqual(result['urgency'], 'breaking')
        self.assertEqual(result['materiality'], 'high')
        self.assertEqual(result['time_horizon'], 'intraday')
        self.assertEqual(result['reasoning'], 'Earnings beat expectations')

    def test_invalid_urgency_defaults_to_standard(self):
        result = _parse_claude_result({
            'sentiment_score': 0.3,
            'urgency': 'urgent',  # invalid
            'materiality': 'high',
            'time_horizon': 'short_term',
            'reasoning': 'test',
        })
        self.assertEqual(result['urgency'], 'standard')

    def test_invalid_materiality_defaults_to_unknown(self):
        result = _parse_claude_result({
            'sentiment_score': 0.3,
            'urgency': 'breaking',
            'materiality': 'critical',  # invalid
            'time_horizon': 'short_term',
            'reasoning': 'test',
        })
        self.assertEqual(result['materiality'], 'unknown')

    def test_invalid_time_horizon_defaults_to_medium_term(self):
        result = _parse_claude_result({
            'sentiment_score': 0.3,
            'urgency': 'breaking',
            'materiality': 'high',
            'time_horizon': 'weekly',  # invalid
            'reasoning': 'test',
        })
        self.assertEqual(result['time_horizon'], 'medium_term')

    def test_missing_fields_get_defaults(self):
        result = _parse_claude_result({
            'sentiment_score': -0.5,
        })
        self.assertEqual(result['sentiment_score'], -0.5)
        self.assertEqual(result['urgency'], 'standard')
        self.assertEqual(result['materiality'], 'unknown')
        self.assertEqual(result['time_horizon'], 'medium_term')
        self.assertEqual(result['reasoning'], 'Claude sentiment analysis')

    def test_sentiment_clamped_high(self):
        result = _parse_claude_result({'sentiment_score': 5.0})
        self.assertEqual(result['sentiment_score'], 1.0)

    def test_sentiment_clamped_low(self):
        result = _parse_claude_result({'sentiment_score': -3.0})
        self.assertEqual(result['sentiment_score'], -1.0)

    def test_all_valid_urgency_values(self):
        for val in _VALID_URGENCY:
            result = _parse_claude_result({'sentiment_score': 0.0, 'urgency': val})
            self.assertEqual(result['urgency'], val)

    def test_all_valid_materiality_values(self):
        for val in _VALID_MATERIALITY:
            result = _parse_claude_result({'sentiment_score': 0.0, 'materiality': val})
            self.assertEqual(result['materiality'], val)

    def test_all_valid_time_horizon_values(self):
        for val in _VALID_TIME_HORIZON:
            result = _parse_claude_result({'sentiment_score': 0.0, 'time_horizon': val})
            self.assertEqual(result['time_horizon'], val)


class TestFallbackParse(unittest.TestCase):
    """Test _fallback_parse text-based sentiment extraction."""

    def test_very_positive(self):
        result = _fallback_parse("The outlook is very positive for this company.")
        self.assertEqual(result['sentiment_score'], 0.8)

    def test_strongly_positive(self):
        result = _fallback_parse("Analysts are strongly positive on earnings.")
        self.assertEqual(result['sentiment_score'], 0.8)

    def test_positive(self):
        result = _fallback_parse("Sentiment is generally positive.")
        self.assertEqual(result['sentiment_score'], 0.5)

    def test_very_negative(self):
        result = _fallback_parse("The market reaction is very negative.")
        self.assertEqual(result['sentiment_score'], -0.8)

    def test_strongly_negative(self):
        result = _fallback_parse("Strongly negative outlook from analysts.")
        self.assertEqual(result['sentiment_score'], -0.8)

    def test_negative(self):
        result = _fallback_parse("Sentiment turned negative after the report.")
        self.assertEqual(result['sentiment_score'], -0.5)

    def test_neutral(self):
        result = _fallback_parse("The company held its annual meeting today.")
        self.assertEqual(result['sentiment_score'], 0.0)

    def test_breaking_urgency_detection(self):
        result = _fallback_parse("Breaking: company just announced a major deal.")
        self.assertEqual(result['urgency'], 'breaking')

    def test_just_reported_urgency_detection(self):
        result = _fallback_parse("The company just reported quarterly earnings above expectations.")
        self.assertEqual(result['urgency'], 'breaking')

    def test_developing_urgency_detection(self):
        result = _fallback_parse("A developing situation is unfolding at the headquarters.")
        self.assertEqual(result['urgency'], 'developing')

    def test_standard_urgency_default(self):
        result = _fallback_parse("The company is performing well this quarter.")
        self.assertEqual(result['urgency'], 'standard')

    def test_default_enrichment_fields(self):
        result = _fallback_parse("Some analysis text.")
        self.assertEqual(result['materiality'], 'unknown')
        self.assertEqual(result['time_horizon'], 'medium_term')

    def test_long_text_truncation(self):
        long_text = "x" * 300
        result = _fallback_parse(long_text)
        self.assertTrue(result['reasoning'].endswith('...'))
        self.assertLessEqual(len(result['reasoning']), 204)  # 200 + '...'

    def test_short_text_no_truncation(self):
        short_text = "Brief analysis."
        result = _fallback_parse(short_text)
        self.assertEqual(result['reasoning'], short_text)


class TestComputeArticleWeight(unittest.TestCase):
    """Test _compute_article_weight weighting logic."""

    def _make_now(self):
        return datetime(2026, 4, 5, 16, 0, 0, tzinfo=timezone.utc)

    def test_source_credibility_newsapi_highest(self):
        """newsapi (Claude-analyzed) should weight higher than marketaux or massive."""
        now = self._make_now()
        base = {'urgency': 'standard', 'materiality': 'unknown', 'time_horizon': 'medium_term'}
        w_newsapi = _compute_article_weight({'source': 'newsapi', **base}, now)
        w_marketaux = _compute_article_weight({'source': 'marketaux', **base}, now)
        w_massive = _compute_article_weight({'source': 'massive', **base}, now)
        self.assertGreater(w_newsapi, w_marketaux)
        self.assertGreater(w_marketaux, w_massive)

    def test_high_materiality_outweighs_low(self):
        now = self._make_now()
        base = {'source': 'newsapi', 'urgency': 'standard', 'time_horizon': 'medium_term'}
        w_high = _compute_article_weight({**base, 'materiality': 'high'}, now)
        w_low = _compute_article_weight({**base, 'materiality': 'low'}, now)
        self.assertGreater(w_high, w_low)
        # high (2.0) should be 4x low (0.5)
        self.assertAlmostEqual(w_high / w_low, 4.0, places=1)

    def test_breaking_urgency_outweighs_standard(self):
        now = self._make_now()
        base = {'source': 'newsapi', 'materiality': 'medium', 'time_horizon': 'medium_term'}
        w_breaking = _compute_article_weight({**base, 'urgency': 'breaking'}, now)
        w_standard = _compute_article_weight({**base, 'urgency': 'standard'}, now)
        self.assertGreater(w_breaking, w_standard)
        self.assertAlmostEqual(w_breaking / w_standard, 2.0, places=1)

    def test_recency_last_hour_3x_baseline(self):
        """Articles from <1 hour ago should get 3x the weight of 3-6 hour old articles."""
        now = self._make_now()
        base = {'source': 'newsapi', 'urgency': 'standard', 'materiality': 'unknown', 'time_horizon': 'medium_term'}
        # 30 minutes ago
        recent = {**base, 'published_at': '2026-04-05T15:30:00Z'}
        # 4 hours ago (baseline bracket)
        older = {**base, 'published_at': '2026-04-05T12:00:00Z'}
        w_recent = _compute_article_weight(recent, now)
        w_older = _compute_article_weight(older, now)
        self.assertAlmostEqual(w_recent / w_older, 3.0, places=1)

    def test_recency_6plus_hours_half_weight(self):
        """Articles 6+ hours old should get 0.5x baseline."""
        now = self._make_now()
        base = {'source': 'newsapi', 'urgency': 'standard', 'materiality': 'unknown', 'time_horizon': 'medium_term'}
        # 4 hours ago (baseline)
        baseline = {**base, 'published_at': '2026-04-05T12:00:00Z'}
        # 8 hours ago
        old = {**base, 'published_at': '2026-04-05T08:00:00Z'}
        w_baseline = _compute_article_weight(baseline, now)
        w_old = _compute_article_weight(old, now)
        self.assertAlmostEqual(w_old / w_baseline, 0.5, places=1)

    def test_no_published_at_uses_baseline_recency(self):
        """Missing published_at should use 1.0 recency (baseline)."""
        now = self._make_now()
        base = {'source': 'newsapi', 'urgency': 'standard', 'materiality': 'unknown', 'time_horizon': 'medium_term'}
        w = _compute_article_weight(base, now)
        # newsapi(1.0) * unknown(0.5) * standard(1.0) * baseline(1.0) = 0.5
        self.assertAlmostEqual(w, 0.5, places=2)

    def test_combined_weight_breaking_high_materiality_recent(self):
        """A breaking, high-materiality, recent article should have massive weight."""
        now = self._make_now()
        heavy = {
            'source': 'newsapi', 'urgency': 'breaking', 'materiality': 'high',
            'time_horizon': 'intraday', 'published_at': '2026-04-05T15:45:00Z',
        }
        light = {
            'source': 'massive', 'urgency': 'standard', 'materiality': 'low',
            'time_horizon': 'long_term', 'published_at': '2026-04-05T09:00:00Z',
        }
        w_heavy = _compute_article_weight(heavy, now)
        w_light = _compute_article_weight(light, now)
        # heavy: 1.0 * 2.0 * 2.0 * 3.0 = 12.0
        # light: 0.6 * 0.5 * 1.0 * 0.5 = 0.15
        self.assertAlmostEqual(w_heavy, 12.0, places=1)
        self.assertAlmostEqual(w_light, 0.15, places=2)
        self.assertGreater(w_heavy / w_light, 50)

    def test_minimum_weight_floor(self):
        """Weight should never drop below 0.1."""
        now = self._make_now()
        worst = {
            'source': 'unknown_source', 'urgency': 'standard', 'materiality': 'unknown',
            'time_horizon': 'long_term', 'published_at': '2026-04-04T08:00:00Z',
        }
        w = _compute_article_weight(worst, now)
        self.assertGreaterEqual(w, 0.1)

    def test_invalid_published_at_uses_baseline(self):
        """Unparseable published_at should fallback to 1.0 recency."""
        now = self._make_now()
        result = {'source': 'newsapi', 'urgency': 'standard', 'materiality': 'unknown',
                  'published_at': 'not-a-date'}
        w = _compute_article_weight(result, now)
        expected = 1.0 * 0.5 * 1.0 * 1.0  # baseline recency
        self.assertAlmostEqual(w, expected, places=2)

    def test_datetime_object_published_at(self):
        """published_at as datetime object should work."""
        now = self._make_now()
        pub = datetime(2026, 4, 5, 15, 30, 0, tzinfo=timezone.utc)  # 30 min ago
        result = {'source': 'newsapi', 'urgency': 'standard', 'materiality': 'unknown',
                  'published_at': pub}
        w = _compute_article_weight(result, now)
        # 1.0 * 0.5 * 1.0 * 3.0 = 1.5
        self.assertAlmostEqual(w, 1.5, places=2)


class TestEnrichedAggregation(unittest.TestCase):
    """Test get_ticker_sentiment_scores aggregation of enriched fields."""

    def test_urgency_breaking_wins(self):
        """Breaking urgency should dominate over developing and standard."""
        results = [
            {'ticker': 'AAPL', 'sentiment_score': 0.5, 'source': 'newsapi',
             'urgency': 'standard', 'materiality': 'low', 'time_horizon': 'medium_term'},
            {'ticker': 'AAPL', 'sentiment_score': 0.3, 'source': 'marketaux',
             'urgency': 'breaking', 'materiality': 'medium', 'time_horizon': 'short_term'},
        ]
        agg = get_ticker_sentiment_scores('AAPL', results)
        self.assertEqual(agg['urgency'], 'breaking')

    def test_urgency_developing_over_standard(self):
        results = [
            {'ticker': 'AAPL', 'sentiment_score': 0.5, 'source': 'newsapi',
             'urgency': 'standard', 'materiality': 'low', 'time_horizon': 'medium_term'},
            {'ticker': 'AAPL', 'sentiment_score': 0.3, 'source': 'marketaux',
             'urgency': 'developing', 'materiality': 'low', 'time_horizon': 'medium_term'},
        ]
        agg = get_ticker_sentiment_scores('AAPL', results)
        self.assertEqual(agg['urgency'], 'developing')

    def test_urgency_all_standard(self):
        results = [
            {'ticker': 'AAPL', 'sentiment_score': 0.5, 'source': 'newsapi',
             'urgency': 'standard', 'materiality': 'low', 'time_horizon': 'medium_term'},
        ]
        agg = get_ticker_sentiment_scores('AAPL', results)
        self.assertEqual(agg['urgency'], 'standard')

    def test_materiality_highest_wins(self):
        """Highest materiality should be selected."""
        results = [
            {'ticker': 'AAPL', 'sentiment_score': 0.5, 'source': 'newsapi',
             'urgency': 'standard', 'materiality': 'low', 'time_horizon': 'medium_term'},
            {'ticker': 'AAPL', 'sentiment_score': 0.3, 'source': 'marketaux',
             'urgency': 'standard', 'materiality': 'high', 'time_horizon': 'medium_term'},
            {'ticker': 'AAPL', 'sentiment_score': 0.1, 'source': 'massive',
             'urgency': 'standard', 'materiality': 'medium', 'time_horizon': 'medium_term'},
        ]
        agg = get_ticker_sentiment_scores('AAPL', results)
        self.assertEqual(agg['materiality'], 'high')

    def test_materiality_unknown_is_lowest(self):
        results = [
            {'ticker': 'AAPL', 'sentiment_score': 0.5, 'source': 'newsapi',
             'urgency': 'standard', 'materiality': 'unknown', 'time_horizon': 'medium_term'},
            {'ticker': 'AAPL', 'sentiment_score': 0.3, 'source': 'marketaux',
             'urgency': 'standard', 'materiality': 'low', 'time_horizon': 'medium_term'},
        ]
        agg = get_ticker_sentiment_scores('AAPL', results)
        self.assertEqual(agg['materiality'], 'low')

    def test_time_horizon_shortest_wins(self):
        """Shortest (most actionable) time horizon should be selected."""
        results = [
            {'ticker': 'AAPL', 'sentiment_score': 0.5, 'source': 'newsapi',
             'urgency': 'standard', 'materiality': 'low', 'time_horizon': 'long_term'},
            {'ticker': 'AAPL', 'sentiment_score': 0.3, 'source': 'marketaux',
             'urgency': 'standard', 'materiality': 'low', 'time_horizon': 'intraday'},
            {'ticker': 'AAPL', 'sentiment_score': 0.1, 'source': 'massive',
             'urgency': 'standard', 'materiality': 'low', 'time_horizon': 'medium_term'},
        ]
        agg = get_ticker_sentiment_scores('AAPL', results)
        self.assertEqual(agg['time_horizon'], 'intraday')

    def test_no_data_returns_no_enrichment(self):
        """No matching results should not include enrichment fields."""
        agg = get_ticker_sentiment_scores('AAPL', [])
        self.assertNotIn('urgency', agg)
        self.assertNotIn('materiality', agg)
        self.assertNotIn('time_horizon', agg)

    def test_single_article_enrichment(self):
        results = [
            {'ticker': 'AAPL', 'sentiment_score': 0.7, 'source': 'newsapi',
             'urgency': 'developing', 'materiality': 'medium', 'time_horizon': 'short_term'},
        ]
        agg = get_ticker_sentiment_scores('AAPL', results)
        self.assertEqual(agg['urgency'], 'developing')
        self.assertEqual(agg['materiality'], 'medium')
        self.assertEqual(agg['time_horizon'], 'short_term')


class TestWeightedAggregation(unittest.TestCase):
    """Test that get_ticker_sentiment_scores weights by source, materiality, urgency, recency."""

    def test_high_materiality_dominates_score(self):
        """A high-materiality article should pull the weighted average toward its score."""
        results = [
            {'ticker': 'AAPL', 'sentiment_score': -0.8, 'source': 'newsapi',
             'urgency': 'breaking', 'materiality': 'high', 'time_horizon': 'short_term'},
            {'ticker': 'AAPL', 'sentiment_score': 0.3, 'source': 'massive',
             'urgency': 'standard', 'materiality': 'low', 'time_horizon': 'long_term'},
        ]
        agg = get_ticker_sentiment_scores('AAPL', results)
        # High-mat breaking newsapi: 1.0*2.0*2.0*1.0 = 4.0
        # Low-mat standard massive:  0.6*0.5*1.0*1.0 = 0.3
        # Weighted: (-0.8*4.0 + 0.3*0.3) / (4.0+0.3) = -3.11/4.3 ≈ -0.723
        self.assertLess(agg['sentiment_score'], -0.5)
        # Much closer to -0.8 than to the midpoint of -0.25
        self.assertLess(agg['sentiment_score'], -0.7)

    def test_recent_article_dominates_over_stale(self):
        """A recent article should count 3x more than a 4-hour-old one."""
        from datetime import timedelta
        now = datetime(2026, 4, 5, 16, 0, 0, tzinfo=timezone.utc)
        results = [
            {'ticker': 'AAPL', 'sentiment_score': 0.9, 'source': 'newsapi',
             'urgency': 'standard', 'materiality': 'unknown', 'time_horizon': 'medium_term',
             'published_at': '2026-04-05T15:30:00Z'},  # 30 min ago -> 3x
            {'ticker': 'AAPL', 'sentiment_score': -0.5, 'source': 'newsapi',
             'urgency': 'standard', 'materiality': 'unknown', 'time_horizon': 'medium_term',
             'published_at': '2026-04-05T12:00:00Z'},  # 4 hours ago -> 1x
        ]
        with patch('engine.sentiment.datetime') as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            agg = get_ticker_sentiment_scores('AAPL', results)
        # recent weight: 1.0*0.5*1.0*3.0 = 1.5, stale: 1.0*0.5*1.0*1.0 = 0.5
        # weighted: (0.9*1.5 + -0.5*0.5) / (1.5+0.5) = 1.1/2.0 = 0.55
        self.assertGreater(agg['sentiment_score'], 0.4)

    def test_equal_weights_produces_simple_average(self):
        """With identical source/materiality/urgency and no timestamps, should behave like average."""
        results = [
            {'ticker': 'AAPL', 'sentiment_score': 0.6, 'source': 'newsapi',
             'urgency': 'standard', 'materiality': 'medium', 'time_horizon': 'medium_term'},
            {'ticker': 'AAPL', 'sentiment_score': 0.2, 'source': 'newsapi',
             'urgency': 'standard', 'materiality': 'medium', 'time_horizon': 'medium_term'},
        ]
        agg = get_ticker_sentiment_scores('AAPL', results)
        # Same source, same enrichment, no published_at -> equal weights -> simple average
        self.assertAlmostEqual(agg['sentiment_score'], 0.4, places=2)

    def test_individual_weights_returned(self):
        """Result should include individual_weights list."""
        results = [
            {'ticker': 'AAPL', 'sentiment_score': 0.5, 'source': 'newsapi',
             'urgency': 'standard', 'materiality': 'high', 'time_horizon': 'medium_term'},
            {'ticker': 'AAPL', 'sentiment_score': 0.3, 'source': 'massive',
             'urgency': 'standard', 'materiality': 'low', 'time_horizon': 'medium_term'},
        ]
        agg = get_ticker_sentiment_scores('AAPL', results)
        self.assertEqual(len(agg['individual_weights']), 2)
        # newsapi + high materiality should be heavier
        self.assertGreater(agg['individual_weights'][0], agg['individual_weights'][1])

    def test_confidence_incorporates_weight_quality(self):
        """Higher average weight should produce higher confidence."""
        low_quality = [
            {'ticker': 'AAPL', 'sentiment_score': 0.5, 'source': 'massive',
             'urgency': 'standard', 'materiality': 'unknown', 'time_horizon': 'medium_term'},
        ]
        high_quality = [
            {'ticker': 'AAPL', 'sentiment_score': 0.5, 'source': 'newsapi',
             'urgency': 'breaking', 'materiality': 'high', 'time_horizon': 'short_term'},
        ]
        conf_low = get_ticker_sentiment_scores('AAPL', low_quality)['confidence']
        conf_high = get_ticker_sentiment_scores('AAPL', high_quality)['confidence']
        self.assertGreater(conf_high, conf_low)


class TestSentimentIntegration(unittest.TestCase):
    """Integration tests for sentiment analysis workflow."""

    @patch('engine.sentiment.anthropic.Anthropic')
    def test_full_workflow(self, mock_anthropic):
        """Test complete sentiment analysis workflow."""
        # Mock Claude response with enriched fields
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = json.dumps({
            "sentiment_score": 0.4,
            "urgency": "developing",
            "materiality": "medium",
            "time_horizon": "short_term",
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
                'sentiment_score': 0.8,
                'published_at': '2026-04-05T14:00:00Z'
            },
            {
                'source': 'newsapi',
                'title': 'Market volatility concerns',
                'full_text': 'Market showing signs of volatility as Apple and Microsoft face headwinds.',
                'tickers': ['AAPL', 'MSFT'],
                'partial': False,
                'published_at': '2026-04-05T15:00:00Z'
            }
        ]

        # Run batch analysis
        sentiment_results = batch_analyze_articles(articles)

        # Should have 3 results: 1 from marketaux + 2 from newsapi (AAPL, MSFT)
        self.assertEqual(len(sentiment_results), 3)

        # Verify published_at is threaded through
        for r in sentiment_results:
            self.assertIn('published_at', r)

        # Get aggregated sentiment for AAPL
        aapl_sentiment = get_ticker_sentiment_scores('AAPL', sentiment_results)

        self.assertEqual(aapl_sentiment['ticker'], 'AAPL')
        self.assertEqual(aapl_sentiment['article_count'], 2)
        self.assertIn('marketaux', aapl_sentiment['source_breakdown'])
        self.assertIn('newsapi', aapl_sentiment['source_breakdown'])
        self.assertIn('individual_weights', aapl_sentiment)
        # Marketaux has 'standard' urgency, newsapi has 'developing' -> developing wins
        self.assertEqual(aapl_sentiment['urgency'], 'developing')
        # Marketaux has 'unknown' materiality, newsapi has 'medium' -> medium wins
        self.assertEqual(aapl_sentiment['materiality'], 'medium')
        # Marketaux has 'medium_term', newsapi has 'short_term' -> short_term wins
        self.assertEqual(aapl_sentiment['time_horizon'], 'short_term')
        # newsapi has higher weight (medium materiality + developing urgency + higher credibility)
        # so weighted average should be pulled toward 0.4 (newsapi) rather than 0.6 (flat avg)
        self.assertLess(aapl_sentiment['sentiment_score'], 0.6)

    @patch('engine.sentiment.anthropic.Anthropic')
    def test_markdown_code_fence_stripping(self, mock_anthropic):
        """Test that markdown code fences are stripped from Claude responses."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = '```json\n{"sentiment_score": 0.5, "urgency": "standard", "materiality": "medium", "time_horizon": "short_term", "reasoning": "test"}\n```'

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.return_value = mock_client

        result = analyze_newsapi_with_claude("Some article text", "AAPL")

        self.assertEqual(result['sentiment_score'], 0.5)
        self.assertEqual(result['urgency'], 'standard')
        self.assertEqual(result['materiality'], 'medium')


class TestRecordTickerSentiment(unittest.TestCase):
    """Test record_ticker_sentiment DB integration and delta computation."""

    def _base_results(self, score=0.5):
        return [
            {'ticker': 'AAPL', 'sentiment_score': score, 'source': 'newsapi',
             'urgency': 'standard', 'materiality': 'medium', 'time_horizon': 'medium_term'},
        ]

    @patch('db.client.save_sentiment_score')
    @patch('db.client.get_previous_sentiment')
    def test_first_cycle_no_previous(self, mock_get_prev, mock_save):
        """First cycle ever — no previous sentiment, delta is None."""
        mock_get_prev.return_value = None

        result = record_ticker_sentiment('AAPL', self._base_results(0.5))

        self.assertEqual(result['sentiment_score'], 0.5)
        self.assertIsNone(result['sentiment_delta'])
        self.assertIsNone(result['previous_score'])
        self.assertIsNone(result['previous_recorded_at'])
        self.assertEqual(result['delta_direction'], 'stable')
        mock_save.assert_called_once_with('AAPL', 0.5, 1)

    @patch('db.client.save_sentiment_score')
    @patch('db.client.get_previous_sentiment')
    def test_bullish_shift_detected(self, mock_get_prev, mock_save):
        """Score went from -0.3 to +0.5 — bullish shift."""
        mock_get_prev.return_value = {
            'sentiment_score': -0.3,
            'article_count': 2,
            'recorded_at': '2026-04-05T14:45:00Z',
        }

        result = record_ticker_sentiment('AAPL', self._base_results(0.5))

        self.assertAlmostEqual(result['sentiment_delta'], 0.8, places=2)
        self.assertEqual(result['previous_score'], -0.3)
        self.assertEqual(result['previous_recorded_at'], '2026-04-05T14:45:00Z')
        self.assertEqual(result['delta_direction'], 'bullish_shift')

    @patch('db.client.save_sentiment_score')
    @patch('db.client.get_previous_sentiment')
    def test_bearish_shift_detected(self, mock_get_prev, mock_save):
        """Score went from +0.7 to +0.2 — bearish shift."""
        mock_get_prev.return_value = {
            'sentiment_score': 0.7,
            'article_count': 3,
            'recorded_at': '2026-04-05T14:45:00Z',
        }

        result = record_ticker_sentiment('AAPL', self._base_results(0.2))

        self.assertAlmostEqual(result['sentiment_delta'], -0.5, places=2)
        self.assertEqual(result['delta_direction'], 'bearish_shift')

    @patch('db.client.save_sentiment_score')
    @patch('db.client.get_previous_sentiment')
    def test_stable_small_delta(self, mock_get_prev, mock_save):
        """Score changed by less than 0.1 — stable."""
        mock_get_prev.return_value = {
            'sentiment_score': 0.45,
            'article_count': 1,
            'recorded_at': '2026-04-05T14:45:00Z',
        }

        result = record_ticker_sentiment('AAPL', self._base_results(0.5))

        self.assertAlmostEqual(result['sentiment_delta'], 0.05, places=2)
        self.assertEqual(result['delta_direction'], 'stable')

    @patch('db.client.save_sentiment_score')
    @patch('db.client.get_previous_sentiment')
    def test_save_called_even_with_zero_articles(self, mock_get_prev, mock_save):
        """Even with no matching articles, save the 0.0 score (absence is signal)."""
        mock_get_prev.return_value = None

        result = record_ticker_sentiment('AAPL', [])  # No matching articles

        self.assertEqual(result['sentiment_score'], 0.0)
        self.assertEqual(result['article_count'], 0)
        mock_save.assert_called_once_with('AAPL', 0.0, 0)

    @patch('db.client.save_sentiment_score')
    @patch('db.client.get_previous_sentiment')
    def test_save_failure_does_not_crash(self, mock_get_prev, mock_save):
        """DB write failure should log error but return the aggregated result."""
        mock_get_prev.return_value = None
        mock_save.side_effect = Exception("DB connection lost")

        result = record_ticker_sentiment('AAPL', self._base_results(0.5))

        # Should still return valid aggregation
        self.assertEqual(result['sentiment_score'], 0.5)
        self.assertIsNone(result['sentiment_delta'])

    @patch('db.client.save_sentiment_score')
    @patch('db.client.get_previous_sentiment')
    def test_delta_at_boundary(self, mock_get_prev, mock_save):
        """Delta of exactly 0.1 should be stable (threshold is >0.1)."""
        mock_get_prev.return_value = {
            'sentiment_score': 0.4,
            'article_count': 1,
            'recorded_at': '2026-04-05T14:45:00Z',
        }

        result = record_ticker_sentiment('AAPL', self._base_results(0.5))

        self.assertAlmostEqual(result['sentiment_delta'], 0.1, places=2)
        self.assertEqual(result['delta_direction'], 'stable')

    @patch('db.client.save_sentiment_score')
    @patch('db.client.get_previous_sentiment')
    def test_preserves_aggregation_fields(self, mock_get_prev, mock_save):
        """All fields from get_ticker_sentiment_scores should be preserved."""
        mock_get_prev.return_value = None

        result = record_ticker_sentiment('AAPL', self._base_results(0.5))

        # Core aggregation fields still present
        self.assertIn('ticker', result)
        self.assertIn('sentiment_score', result)
        self.assertIn('article_count', result)
        self.assertIn('source_breakdown', result)
        self.assertIn('confidence', result)
        self.assertIn('individual_scores', result)
        self.assertIn('individual_weights', result)
        self.assertIn('urgency', result)
        self.assertIn('materiality', result)
        self.assertIn('time_horizon', result)
        # Delta fields added
        self.assertIn('sentiment_delta', result)
        self.assertIn('previous_score', result)
        self.assertIn('delta_direction', result)


class TestBatchRecordSentiments(unittest.TestCase):
    """Test batch_record_sentiments convenience function."""

    @patch('db.client.save_sentiment_score')
    @patch('db.client.get_previous_sentiment')
    def test_records_all_tickers(self, mock_get_prev, mock_save):
        """Should produce results for every ticker in the list."""
        mock_get_prev.return_value = None
        sentiment_results = [
            {'ticker': 'AAPL', 'sentiment_score': 0.5, 'source': 'newsapi',
             'urgency': 'standard', 'materiality': 'medium', 'time_horizon': 'medium_term'},
            {'ticker': 'MSFT', 'sentiment_score': -0.3, 'source': 'marketaux',
             'urgency': 'standard', 'materiality': 'unknown', 'time_horizon': 'medium_term'},
        ]

        result = batch_record_sentiments(['AAPL', 'MSFT', 'NVDA'], sentiment_results)

        self.assertIn('AAPL', result)
        self.assertIn('MSFT', result)
        self.assertIn('NVDA', result)
        self.assertEqual(result['AAPL']['sentiment_score'], 0.5)
        self.assertEqual(result['MSFT']['sentiment_score'], -0.3)
        self.assertEqual(result['NVDA']['sentiment_score'], 0.0)  # No articles
        self.assertEqual(mock_save.call_count, 3)

    @patch('db.client.save_sentiment_score')
    @patch('db.client.get_previous_sentiment')
    def test_deltas_computed_per_ticker(self, mock_get_prev, mock_save):
        """Each ticker should get its own delta from its own history."""
        def prev_side_effect(ticker):
            if ticker == 'AAPL':
                return {'sentiment_score': -0.5, 'article_count': 2, 'recorded_at': '2026-04-05T14:45:00Z'}
            elif ticker == 'MSFT':
                return {'sentiment_score': 0.8, 'article_count': 1, 'recorded_at': '2026-04-05T14:45:00Z'}
            return None

        mock_get_prev.side_effect = prev_side_effect

        sentiment_results = [
            {'ticker': 'AAPL', 'sentiment_score': 0.5, 'source': 'newsapi',
             'urgency': 'standard', 'materiality': 'medium', 'time_horizon': 'medium_term'},
            {'ticker': 'MSFT', 'sentiment_score': 0.2, 'source': 'newsapi',
             'urgency': 'standard', 'materiality': 'medium', 'time_horizon': 'medium_term'},
        ]

        result = batch_record_sentiments(['AAPL', 'MSFT'], sentiment_results)

        # AAPL: -0.5 -> 0.5 = bullish shift
        self.assertEqual(result['AAPL']['delta_direction'], 'bullish_shift')
        self.assertAlmostEqual(result['AAPL']['sentiment_delta'], 1.0, places=2)

        # MSFT: 0.8 -> 0.2 = bearish shift
        self.assertEqual(result['MSFT']['delta_direction'], 'bearish_shift')
        self.assertAlmostEqual(result['MSFT']['sentiment_delta'], -0.6, places=2)

    @patch('db.client.save_sentiment_score')
    @patch('db.client.get_previous_sentiment')
    def test_single_ticker_failure_doesnt_block_others(self, mock_get_prev, mock_save):
        """If one ticker's DB call fails, others should still succeed."""
        call_count = 0

        def flaky_get_prev(ticker):
            nonlocal call_count
            call_count += 1
            if ticker == 'AAPL':
                raise Exception("DB timeout")
            return None

        mock_get_prev.side_effect = flaky_get_prev

        sentiment_results = [
            {'ticker': 'AAPL', 'sentiment_score': 0.5, 'source': 'newsapi',
             'urgency': 'standard', 'materiality': 'medium', 'time_horizon': 'medium_term'},
            {'ticker': 'MSFT', 'sentiment_score': 0.3, 'source': 'newsapi',
             'urgency': 'standard', 'materiality': 'medium', 'time_horizon': 'medium_term'},
        ]

        result = batch_record_sentiments(['AAPL', 'MSFT'], sentiment_results)

        # AAPL should fall back to pure aggregation (no delta fields)
        self.assertIn('AAPL', result)
        self.assertEqual(result['AAPL']['sentiment_score'], 0.5)
        # MSFT should have delta fields
        self.assertIn('MSFT', result)
        self.assertIn('sentiment_delta', result['MSFT'])


if __name__ == '__main__':
    # Set environment variable to avoid API key errors in tests
    os.environ.setdefault('ANTHROPIC_API_KEY', 'test-key')

    unittest.main()