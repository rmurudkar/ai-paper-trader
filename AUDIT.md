# Architecture Migration Audit

This audit identifies all files that need changes to work with the new autonomous paper trading architecture. Changes are needed to integrate with the discovery engine, Turso database, new signal combination system, and comprehensive feedback loop.

## Core Architecture Changes

**Key Principles:**
- Discovery engine runs FIRST in every cycle - no hardcoded ticker lists
- All data flows through Turso database, not local files  
- Signals flow: strategies → combiner → risk manager → executor
- Every trade logged with full attribution for feedback loop
- Circuit breaker protection integrated throughout

---

## Files Requiring Updates

### fetchers/marketaux.py
- [x] Add `broad=True` mode to fetch without ticker filter (Task 2 — completed)
- [ ] **Implement broad mode logic**: Currently has TODO comment, needs actual implementation
- [ ] **Accept dynamic ticker list**: Replace TICKER_MODE env var logic with direct parameter
- [ ] **Return discovery-compatible format**: Ensure ticker extraction works for discovery.py
- [ ] **Turso integration**: Replace any local caching with database calls

### fetchers/newsapi.py
- [x] Add `broad=True` mode to fetch without ticker filter (Task 2 — completed)
- [ ] **Implement broad mode logic**: Currently has TODO comment, needs actual implementation
- [ ] **Discovery integration**: Ensure ticker extraction feeds back to discovery engine
- [ ] **Remove legacy watchlist handling**: Replace with discovery_context parameter usage
- [ ] **Turso integration**: Any caching should use database, not local storage

### fetchers/market.py
- [ ] **CRITICAL: Full implementation needed**: Currently just stubs with pass statements
- [ ] **Add technical indicators**: Implement RSI(14), 20MA calculation for strategies
- [ ] **Add macro indicators**: Fetch VIX, SPY vs 200MA, yield spreads for regime detection
- [ ] **Accept dynamic ticker list**: No hardcoded watchlists, accept from discovery.py
- [ ] **Discovery mode support**: Fetch sector ETF data when is_premarket=True
- [ ] **Efficient batch fetching**: Use yfinance efficiently for multiple tickers
- [ ] **Error handling**: Graceful failures for individual ticker fetch problems

### fetchers/aggregator.py
- [ ] **Discovery integration**: Update to work with discovery_context instead of watchlist
- [ ] **Remove watchlist parameter**: Replace with dynamic ticker list from discovery
- [ ] **Waterfall enhancement**: Ensure all enrichment steps work with discovery mode
- [ ] **Deduplication improvements**: Handle discovery mode article volumes efficiently
- [ ] **Error handling**: Don't crash discovery pipeline on aggregation failures

### fetchers/polygon.py
- [ ] **Remove hardcoded watchlist**: Line 16 has DEFAULT_WATCHLIST - violates architecture
- [ ] **Accept dynamic ticker list**: All ticker filtering should come from discovery.py
- [ ] **Discovery mode support**: Add broad fetching capability for discovery engine
- [ ] **Rate limiting integration**: Coordinate with other fetchers to avoid API overload
- [ ] **Turso caching**: Replace any local caching with database storage

### fetchers/alpaca_news.py
- [ ] **Accept dynamic ticker list**: Remove hardcoded watchlist dependencies
- [ ] **Discovery integration**: Support broad mode fetching for discovery engine
- [ ] **Error handling**: Graceful degradation when Alpaca News unavailable
- [ ] **Integration with waterfall**: Ensure it works properly in aggregator waterfall

### engine/sentiment.py
- [ ] **Remove aggregator import**: Line 6 imports from old aggregator interface
- [ ] **Discovery integration**: Accept dynamic ticker list, not hardcoded
- [ ] **Turso integration**: Replace any local storage with database calls
- [ ] **Regime classification**: Add macro sentiment analysis for regime.py integration
- [ ] **Attribution tracking**: Tag sentiment with source for feedback loop
- [ ] **Error handling**: Don't crash on individual sentiment analysis failures
- [ ] **Claude API optimization**: Batch requests efficiently, handle rate limits

### engine/signals.py
- [ ] **DEPRECATED**: Replace entirely with engine/combiner.py
- [ ] **Migration strategy**: Move any reusable logic to engine/strategies.py
- [ ] **Remove references**: Update any imports from other modules
- [ ] **Historical compatibility**: Ensure old signal format can be migrated

### executor/alpaca.py
- [ ] **Risk manager integration**: ALL trades must go through risk/manager.py first
- [ ] **Market hours checking**: Integrate with scheduler's market hours awareness
- [ ] **Position tracking**: Provide current positions to risk manager and discovery
- [ ] **Stop loss/take profit**: Implement bracket orders or separate order management
- [ ] **Order metadata**: Track order IDs for feedback loop correlation
- [ ] **Error handling**: Comprehensive error handling for order failures
- [ ] **Circuit breaker integration**: Check circuit breaker status before any trade

### dashboard/app.py
- [ ] **Import updates**: Line 4-6 import from old module structure
- [ ] **Discovery integration**: Add active tickers panel showing discovery results
- [ ] **Regime indicator**: Show current market regime with technical inputs
- [ ] **Weight visualization**: Display learned strategy and source weights
- [ ] **Circuit breaker status**: Show system halt status with manual override
- [ ] **Sector exposure**: Add pie chart of portfolio allocation by sector
- [ ] **Performance metrics**: Rolling win rate, drawdown tracking
- [ ] **Settings panel**: Toggle TICKER_MODE, edit MAX_DISCOVERY_TICKERS
- [ ] **Turso integration**: All data queries should hit database, not local files
- [ ] **Real-time updates**: Integrate with scheduler for live cycle status

### test_newsapi.py
- [ ] **Update test parameters**: Add broad mode testing
- [ ] **Discovery integration**: Test discovery_context parameter handling
- [ ] **Mocking strategy**: Mock Turso database calls appropriately
- [ ] **Error handling tests**: Verify graceful degradation on API failures
- [ ] **Rate limiting tests**: Ensure tests don't hit real API rate limits

### test_polygon.py  
- [ ] **Remove hardcoded watchlists**: Replace with dynamic test data
- [ ] **Discovery integration**: Test broad mode fetching
- [ ] **Rate limiting tests**: Ensure tests respect API limits
- [ ] **Error handling**: Test failure modes don't crash system

---

## Database Migration Requirements

### Turso Integration Needed In:
- [ ] **All fetchers**: Replace any local caching with Turso sector_cache, discovery_log
- [ ] **Sentiment engine**: Cache sentiment analysis results, track attribution
- [ ] **Risk manager**: Query sector_cache, log risk decisions
- [ ] **Dashboard**: All data visualization should query Turso tables
- [ ] **Test files**: Mock database connections appropriately

### Missing Database Operations:
- [ ] **db/client.py**: Implement Turso connection manager (referenced but missing)
- [ ] **Schema initialization**: Implement schema.sql table creation
- [ ] **Connection pooling**: Efficient database connection management
- [ ] **Error handling**: Database failure graceful degradation
- [ ] **Migration scripts**: Handle schema updates and data migration

---

## Performance and Reliability Issues

### API Rate Limiting:
- [ ] **Coordinate fetchers**: Prevent simultaneous API calls that exceed limits
- [ ] **Backoff strategies**: Exponential backoff for failed API calls
- [ ] **Circuit breakers**: API-level circuit breakers for external service failures
- [ ] **Caching strategy**: Aggressive caching to minimize API usage

### Error Recovery:
- [ ] **Partial failures**: System continues operating with degraded data
- [ ] **Retry logic**: Smart retries for transient failures
- [ ] **Fallback data**: Use cached/stale data when fresh data unavailable
- [ ] **Monitoring**: Comprehensive logging for debugging and alerting

### Memory Management:
- [ ] **Large dataset handling**: Efficiently process high-volume news in discovery mode
- [ ] **Resource cleanup**: Proper cleanup of network connections and data structures
- [ ] **Memory limits**: Prevent memory bloat during extended operation

---

## Configuration Management

### Environment Variables:
- [ ] **Turso configuration**: Add TURSO_CONNECTION_URL and TURSO_AUTH_TOKEN usage
- [ ] **Discovery parameters**: Implement MAX_DISCOVERY_TICKERS throughout system
- [ ] **Alert configuration**: Implement ALERT_EMAIL and SLACK_WEBHOOK_URL
- [ ] **Validation**: Startup validation of all required configuration

### Settings Integration:
- [ ] **Dynamic configuration**: Support runtime configuration changes via dashboard
- [ ] **Configuration persistence**: Store user preferences in database
- [ ] **Validation**: Validate configuration changes don't break system

---

## Security and Compliance

### API Key Management:
- [ ] **Secure storage**: Ensure API keys never logged or exposed
- [ ] **Rotation support**: Handle API key rotation gracefully
- [ ] **Access control**: Proper scoping of API permissions

### Data Privacy:
- [ ] **Personal data**: Ensure no personal information collected from news sources
- [ ] **Data retention**: Implement appropriate data retention policies
- [ ] **Audit trails**: Comprehensive audit logging for regulatory compliance

---

## Testing Requirements

### Integration Tests Needed:
- [ ] **End-to-end pipeline**: Full discovery → analysis → execution → feedback cycle
- [ ] **Database integration**: All Turso operations with test database
- [ ] **API failure scenarios**: Test system behavior under various failure modes
- [ ] **Performance testing**: System behavior under high-volume news cycles
- [ ] **Circuit breaker testing**: Verify automatic halt and recovery mechanisms

### Unit Tests Missing:
- [ ] **All new modules**: comprehensive unit test coverage for stubs
- [ ] **Edge cases**: Test boundary conditions and error scenarios
- [ ] **Mocking strategy**: Consistent mocking of external dependencies

---

## Documentation Updates

### Missing Documentation:
- [ ] **Setup instructions**: Complete setup with Turso database initialization
- [ ] **Configuration guide**: All environment variables and their purposes
- [ ] **Troubleshooting**: Common issues and resolution steps
- [ ] **Architecture diagrams**: Updated diagrams showing new data flow
- [ ] **API documentation**: Document internal module interfaces

### Code Documentation:
- [ ] **Inline comments**: Add comments to complex logic in existing modules
- [ ] **Function documentation**: Ensure all public functions have proper docstrings
- [ ] **Example usage**: Add usage examples to complex modules

---

## Priority Implementation Order

Based on dependencies and criticality:

### Phase 1 (Critical Foundation):
1. **db/client.py** - Database connection manager
2. **fetchers/market.py** - Technical indicator implementation
3. **engine/sentiment.py** - Fix imports and discovery integration

### Phase 2 (Core Pipeline):
4. **executor/alpaca.py** - Risk manager integration
5. **fetchers/aggregator.py** - Discovery mode support
6. **Remove engine/signals.py** - Deprecated module cleanup

### Phase 3 (User Interface):
7. **dashboard/app.py** - Complete UI overhaul for new architecture
8. **test_*.py files** - Update test suite
9. **Documentation** - Complete setup and usage documentation

### Phase 4 (Optimization):
10. **Performance optimization** - Rate limiting, caching, error handling
11. **Security hardening** - API key management, data privacy
12. **Monitoring and alerting** - Comprehensive system monitoring