#!/usr/bin/env python3
"""Generate sys_arch.excalidraw — Autonomous AI Paper Trading System Architecture"""
import json
import random

random.seed(42)
elements = []
_id_counter = [0]

def uid(prefix="el"):
    _id_counter[0] += 1
    return f"{prefix}_{_id_counter[0]}"

def rect(x, y, w, h, bg="transparent", stroke="#1e1e1e", sw=2, rounded=True, opacity=100, group=None):
    eid = uid("r")
    el = {
        "type": "rectangle", "id": eid, "x": x, "y": y, "width": w, "height": h,
        "strokeColor": stroke, "backgroundColor": bg, "fillStyle": "solid",
        "strokeWidth": sw, "roughness": 1, "opacity": opacity,
        "seed": random.randint(1, 999999), "versionNonce": random.randint(1, 999999),
        "isDeleted": False, "groupIds": [group] if group else [],
        "boundElements": [], "updated": 1, "link": None, "locked": False,
    }
    if rounded:
        el["roundness"] = {"type": 3}
    elements.append(el)
    return eid

def text(x, y, txt, size=16, color="#1e1e1e", align="left", width=None):
    eid = uid("t")
    el = {
        "type": "text", "id": eid, "x": x, "y": y, "width": width or len(txt.split('\n')[0]) * size * 0.55,
        "height": size * 1.35 * len(txt.split('\n')),
        "text": txt, "fontSize": size, "fontFamily": 1, "textAlign": align,
        "verticalAlign": "top",
        "strokeColor": color, "backgroundColor": "transparent", "fillStyle": "solid",
        "strokeWidth": 1, "roughness": 1, "opacity": 100,
        "seed": random.randint(1, 999999), "versionNonce": random.randint(1, 999999),
        "isDeleted": False, "groupIds": [], "boundElements": None,
        "updated": 1, "link": None, "locked": False,
        "containerId": None, "originalText": txt, "lineHeight": 1.35,
    }
    elements.append(el)
    return eid

def labeled_rect(x, y, w, h, label_text, label_size=16, label_color="#1e1e1e",
                 bg="transparent", stroke="#1e1e1e", sw=2, rounded=True, opacity=100):
    rid = uid("r")
    tid = uid("t")
    lines = label_text.split('\n')
    text_h = label_size * 1.35 * len(lines)
    max_line = max(len(l) for l in lines)
    text_w = max_line * label_size * 0.55

    r_el = {
        "type": "rectangle", "id": rid, "x": x, "y": y, "width": w, "height": h,
        "strokeColor": stroke, "backgroundColor": bg, "fillStyle": "solid",
        "strokeWidth": sw, "roughness": 1, "opacity": opacity,
        "seed": random.randint(1, 999999), "versionNonce": random.randint(1, 999999),
        "isDeleted": False, "groupIds": [],
        "boundElements": [{"id": tid, "type": "text"}],
        "updated": 1, "link": None, "locked": False,
    }
    if rounded:
        r_el["roundness"] = {"type": 3}
    elements.append(r_el)

    t_el = {
        "type": "text", "id": tid,
        "x": x + (w - text_w) / 2, "y": y + (h - text_h) / 2,
        "width": text_w, "height": text_h,
        "text": label_text, "fontSize": label_size, "fontFamily": 1,
        "textAlign": "center", "verticalAlign": "middle",
        "strokeColor": label_color, "backgroundColor": "transparent", "fillStyle": "solid",
        "strokeWidth": 1, "roughness": 1, "opacity": 100,
        "seed": random.randint(1, 999999), "versionNonce": random.randint(1, 999999),
        "isDeleted": False, "groupIds": [],
        "boundElements": None, "updated": 1, "link": None, "locked": False,
        "containerId": rid, "originalText": label_text, "lineHeight": 1.35,
    }
    elements.append(t_el)
    return rid

def arrow(x, y, points, stroke="#94a3b8", sw=2, end="arrow", start=None,
          dashed=False, label_text=None, label_size=14, label_color="#94a3b8"):
    eid = uid("a")
    dx = max(p[0] for p in points) - min(p[0] for p in points)
    dy = max(p[1] for p in points) - min(p[1] for p in points)
    el = {
        "type": "arrow", "id": eid, "x": x, "y": y,
        "width": dx if dx else 1, "height": dy if dy else 1,
        "points": points,
        "strokeColor": stroke, "backgroundColor": "transparent", "fillStyle": "solid",
        "strokeWidth": sw, "roughness": 1, "opacity": 100,
        "seed": random.randint(1, 999999), "versionNonce": random.randint(1, 999999),
        "isDeleted": False, "groupIds": [], "boundElements": None,
        "updated": 1, "link": None, "locked": False,
        "endArrowhead": end, "startArrowhead": start,
    }
    if dashed:
        el["strokeStyle"] = "dashed"
    if label_text:
        lid = uid("t")
        el["boundElements"] = [{"id": lid, "type": "text"}]
        # compute label position at midpoint
        mid_idx = len(points) // 2
        mp = points[mid_idx]
        lw = len(label_text.split('\n')[0]) * label_size * 0.55
        lh = label_size * 1.35 * len(label_text.split('\n'))
        elements.append(el)
        t_el = {
            "type": "text", "id": lid,
            "x": x + mp[0] - lw/2, "y": y + mp[1] - lh/2,
            "width": lw, "height": lh,
            "text": label_text, "fontSize": label_size, "fontFamily": 1,
            "textAlign": "center", "verticalAlign": "middle",
            "strokeColor": label_color, "backgroundColor": "transparent", "fillStyle": "solid",
            "strokeWidth": 1, "roughness": 1, "opacity": 100,
            "seed": random.randint(1, 999999), "versionNonce": random.randint(1, 999999),
            "isDeleted": False, "groupIds": [], "boundElements": None,
            "updated": 1, "link": None, "locked": False,
            "containerId": eid, "originalText": label_text, "lineHeight": 1.35,
        }
        elements.append(t_el)
    else:
        elements.append(el)
    return eid


# ─── LAYOUT CONSTANTS ───
ZW = 2500        # main zone width
ZX = 100         # main zone left x
DX = 2750        # dashboard x
DW = 650         # dashboard width

# ─── TITLE ───
text(750, 15, "Autonomous AI Paper Trader", 32, "#e5e5e5")
text(850, 58, "System Architecture", 22, "#a0a0a0")

# ═══════════════════════════════════════════════
# ZONE 1 — ORCHESTRATION
# ═══════════════════════════════════════════════
Z1Y = 100
rect(ZX, Z1Y, ZW, 130, bg="#374151", stroke="#6b7280", sw=1, opacity=35)
text(ZX+20, Z1Y+8, "ORCHESTRATION", 18, "#d1d5db")
labeled_rect(450, Z1Y+25, 1700, 80,
    "Scheduler (APScheduler)\nEvery 15min market hours  |  Pre-market 9:00 AM  |  Post-market 4:30 PM",
    16, "#e5e5e5", bg="#4b5563", stroke="#9ca3af", sw=2)

# Z1 -> Z2 arrow
arrow(1350, Z1Y+130, [[0,0],[0,50]], label_text="triggers cycle")

# ═══════════════════════════════════════════════
# ZONE 2 — DISCOVERY ENGINE
# ═══════════════════════════════════════════════
Z2Y = 280
rect(ZX, Z2Y, ZW, 280, bg="#92400e", stroke="#d97706", sw=1, opacity=22)
text(ZX+20, Z2Y+8, "DISCOVERY ENGINE", 18, "#fbbf24")

# 5 discovery sources
src_y = Z2Y + 45
labeled_rect(130, src_y, 430, 55, "News Mentions\nMarketaux + Massive + NewsAPI", 14, "#fef3c7",
             bg="#78350f", stroke="#d97706", sw=1)
labeled_rect(580, src_y, 400, 55, "Market Movers\nS&P 500 gainers / losers", 14, "#fef3c7",
             bg="#78350f", stroke="#d97706", sw=1)
labeled_rect(1000, src_y, 400, 55, "Sector Rotation\n20 ETFs + top holdings", 14, "#fef3c7",
             bg="#78350f", stroke="#d97706", sw=1)
labeled_rect(1420, src_y, 350, 55, "Existing Positions\n(always tracked)", 14, "#fef3c7",
             bg="#78350f", stroke="#d97706", sw=1)
labeled_rect(1790, src_y, 350, 55, "User Watchlist\n(always included)", 14, "#fef3c7",
             bg="#78350f", stroke="#d97706", sw=1)

# Arrows from sources to merge
merge_y = src_y + 100
for sx in [345, 780, 1200, 1595, 1965]:
    arrow(sx, src_y+55, [[0,0],[1350-sx+50, merge_y-src_y-55]], stroke="#d97706", sw=1)

# Merge / validate box
labeled_rect(420, merge_y, 1700, 60,
    "Validate (price >= $5, mcap >= $1B, vol >= 500K)  |  Dedup  |  Cap MAX_DISCOVERY_TICKERS = 30",
    15, "#fef3c7", bg="#451a03", stroke="#f59e0b", sw=2)

text(1100, merge_y+72, "Active Ticker List", 16, "#fbbf24")

# Z2 -> Z3
arrow(1350, Z2Y+280, [[0,0],[0,50]], label_text="tickers")

# ═══════════════════════════════════════════════
# ZONE 3 — DATA FETCHING
# ═══════════════════════════════════════════════
Z3Y = 610
rect(ZX, Z3Y, ZW, 640, bg="#1e3a5f", stroke="#3b82f6", sw=1, opacity=22)
text(ZX+20, Z3Y+8, "DATA FETCHING", 18, "#60a5fa")

# 4 fetcher boxes (2x2)
fy1 = Z3Y + 45
labeled_rect(130, fy1, 560, 70, "Marketaux API\nPre-scored sentiment (-1 to +1), ticker-tagged", 15, "#bfdbfe",
             bg="#1e3a5f", stroke="#3b82f6", sw=2)
labeled_rect(720, fy1, 560, 70, "Massive API\nPre-scored sentiment, ticker-tagged", 15, "#bfdbfe",
             bg="#1e3a5f", stroke="#3b82f6", sw=2)
fy2 = fy1 + 90
labeled_rect(130, fy2, 560, 70, "NewsAPI.ai (EventRegistry)\nMacro / geopolitical headlines, needs full text", 15, "#bfdbfe",
             bg="#1e3a5f", stroke="#3b82f6", sw=2)
labeled_rect(720, fy2, 560, 70, "Alpaca News (Benzinga)\nFull content, HTML stripped, 1200 word cap", 15, "#bfdbfe",
             bg="#1e3a5f", stroke="#3b82f6", sw=2)

# 4-Step Waterfall
wfx = 1380
wfy = fy1
text(wfx, wfy, "4-Step Waterfall Enrichment", 16, "#93c5fd")
text(wfx+40, wfy+22, "(for NewsAPI articles)", 14, "#60a5fa")

wsy = wfy + 50
for i, label in enumerate([
    "1. Polygon.io licensed full text",
    "2. Alpaca News URL / title match",
    "3. Scraper (trafilatura > n3k > BS4)",
    "4. Snippet only (partial=True)",
]):
    labeled_rect(wfx, wsy + i*55, 420, 42, label, 14, "#bfdbfe",
                 bg="#172554", stroke="#60a5fa", sw=1)
    if i < 3:
        arrow(wfx+210, wsy + i*55+42, [[0,0],[0,13]], stroke="#60a5fa", sw=1)

# Aggregator
agy = Z3Y + 490
labeled_rect(350, agy, 1850, 70,
    "Aggregator: Dedup by URL + Title Similarity (>80% Jaccard)\nMerge all 5 sources (Marketaux + Massive + NewsAPI + Alpaca + Polygon)  |  Sort by published_at",
    15, "#e0f2fe", bg="#0c4a6e", stroke="#38bdf8", sw=2)

# Fetchers to aggregator arrows
for fx in [410, 1000]:
    arrow(fx, fy1+70, [[0,0],[300, agy-fy1-70]], stroke="#3b82f6", sw=1)
    arrow(fx, fy2+70, [[0,0],[300, agy-fy2-70]], stroke="#3b82f6", sw=1)
# Waterfall to aggregator
arrow(wfx+210, wsy + 3*55+42, [[0,0],[0, agy - (wsy+3*55+42)]], stroke="#60a5fa", sw=1)

# Z3 -> Z4
arrow(1350, Z3Y+640, [[0,0],[0,50]], label_text="enriched articles")

# ═══════════════════════════════════════════════
# ZONE 4 — INTELLIGENCE
# ═══════════════════════════════════════════════
Z4Y = 1300
rect(ZX, Z4Y, ZW, 420, bg="#3b0764", stroke="#7c3aed", sw=1, opacity=22)
text(ZX+20, Z4Y+8, "INTELLIGENCE", 18, "#a78bfa")

by = Z4Y + 40
bh = 350
# Sentiment Engine
labeled_rect(130, by, 760, bh,
    "Sentiment Engine (Claude API)\n"
    "\n"
    "Marketaux / Massive:\n"
    "  Pass-through (NO Claude call)\n"
    "  Use pre-scored sentiment directly\n"
    "\n"
    "NewsAPI / Alpaca / Polygon:\n"
    "  Claude analyzes full_text (never headlines)\n"
    "  Model: claude-3-haiku-20240307\n"
    "  Truncate to 1200 words\n"
    "\n"
    "Output per ticker:\n"
    "  sentiment_score (-1 to +1)\n"
    "  urgency (breaking/developing/standard)\n"
    "  materiality (high/medium/low)\n"
    "  time_horizon (intraday/short/med/long)\n"
    "  sentiment_delta vs previous cycle",
    14, "#ddd6fe", bg="#2e1065", stroke="#8b5cf6", sw=2)

# Strategy Engine
labeled_rect(920, by, 760, bh,
    "Strategy Engine (8 strategies)\n"
    "\n"
    "Cat 1 — Primary Signals:\n"
    "  sentiment_price_divergence\n"
    "  multi_source_consensus\n"
    "  sentiment_momentum\n"
    "\n"
    "Cat 2 — Modifiers Only (never standalone):\n"
    "  volume_confirmation (1.4x / 0.6x)\n"
    "  vwap_position (+/-0.15)\n"
    "  relative_strength (+/-0.2)\n"
    "\n"
    "Cat 3 — Post-News Drift:\n"
    "  news_catalyst_drift\n"
    "\n"
    "Standalone Technical:\n"
    "  momentum_signal (MA cross)\n"
    "  mean_reversion_signal (RSI)",
    14, "#ddd6fe", bg="#2e1065", stroke="#8b5cf6", sw=2)

# Regime Classifier
labeled_rect(1710, by, 760, bh,
    "Macro Regime Classifier\n"
    "\n"
    "Weighted Indicator Scoring:\n"
    "  VIX (35%):  <20 risk_on, >25 risk_off\n"
    "  SPY vs 200MA (30%):  +/-2% threshold\n"
    "  Yield Spread 10yr-2yr (25%):\n"
    "    >0.5% risk_on, <-0.5% risk_off\n"
    "  Macro Sentiment (10-25%):\n"
    "    Override if abs(score) > 0.6\n"
    "\n"
    "Thresholds:\n"
    "  Score > 0.3  = RISK_ON\n"
    "  Score < -0.3 = RISK_OFF\n"
    "  Otherwise    = NEUTRAL\n"
    "\n"
    "Source: yfinance market data\n"
    "Returns: regime, confidence",
    14, "#ddd6fe", bg="#2e1065", stroke="#8b5cf6", sw=2)

# Z4 -> Z5
arrow(1350, Z4Y+420, [[0,0],[0,50]])

# ═══════════════════════════════════════════════
# ZONE 5 — SIGNAL COMBINATION
# ═══════════════════════════════════════════════
Z5Y = 1770
rect(ZX, Z5Y, ZW, 200, bg="#312e81", stroke="#6366f1", sw=1, opacity=22)
text(ZX+20, Z5Y+8, "SIGNAL COMBINATION (4-Stage Pipeline)", 18, "#a5b4fc")
text(ZX+20, Z5Y+32, "Reads learned weights from weights table", 14, "#818cf8")

# 4 stage boxes
sy = Z5Y + 60
sw_ = 440
for i, (title, desc) in enumerate([
    ("Stage 1: Primary Direction", "Weighted Cat 1 + standalone signals\nConflict penalty applied"),
    ("Stage 2: Tech Modifiers", "Cat 2 multipliers applied\nvolume x VWAP x rel_strength"),
    ("Stage 3: Catalyst Drift", "Cat 3 integration\nConfirm +20% / Contradict -15%"),
    ("Stage 4: Regime Filter", "risk_off: BUY conf x 0.7\nrisk_on: SELL conf x 0.8"),
]):
    labeled_rect(130 + i*(sw_+15), sy, sw_, 65,
        f"{title}\n{desc}", 14, "#c7d2fe",
        bg="#1e1b4b", stroke="#818cf8", sw=2)
    if i < 3:
        arrow(130 + (i+1)*(sw_+15) - 15, sy+32, [[0,0],[15,0]], stroke="#818cf8", sw=2)

# Confidence gate
gx = 130 + 4*(sw_+15) - 10
# Diamond for gate
gate_id = uid("d")
elements.append({
    "type": "diamond", "id": gate_id,
    "x": gx, "y": sy-2, "width": 110, "height": 68,
    "strokeColor": "#f59e0b", "backgroundColor": "#312e81", "fillStyle": "solid",
    "strokeWidth": 2, "roughness": 1, "opacity": 100,
    "seed": random.randint(1, 999999), "versionNonce": random.randint(1, 999999),
    "isDeleted": False, "groupIds": [], "boundElements": [],
    "updated": 1, "link": None, "locked": False,
})
text(gx+18, sy+12, "conf\n> 0.55", 14, "#fef3c7")
text(gx+120, sy+18, "BUY / SELL\n/ HOLD", 14, "#fbbf24")

# Z5 -> Z6
arrow(1350, Z5Y+200, [[0,0],[0,50]], label_text="BUY/SELL signals")

# ═══════════════════════════════════════════════
# ZONE 6 — RISK MANAGEMENT
# ═══════════════════════════════════════════════
Z6Y = 2020
rect(ZX, Z6Y, ZW, 360, bg="#7f1d1d", stroke="#ef4444", sw=1, opacity=22)
text(ZX+20, Z6Y+8, "RISK MANAGEMENT", 18, "#fca5a5")

labeled_rect(130, Z6Y+40, 1100, 280,
    "7 Hard Rules (ALL must pass)\n"
    "\n"
    "1. Price >= $5 (no penny stocks)\n"
    "2. Market cap >= $1B (no micro-caps)\n"
    "3. Max 15 open positions\n"
    "4. Cash reserve >= 20% of portfolio\n"
    "5. Single ticker <= 10% of portfolio\n"
    "6. Single sector <= 30% of portfolio\n"
    "7. No duplicate signal within 2 hours\n"
    "\n"
    "Sector lookup via yfinance -> sector_cache\n"
    "Market cap check skipped if data unavailable",
    15, "#fecaca", bg="#450a0a", stroke="#ef4444", sw=2)

labeled_rect(1260, Z6Y+40, 1300, 280,
    "Position Sizing Formula\n"
    "\n"
    "risk_budget   = equity x 2%\n"
    "stop_distance = price x 3%\n"
    "base_shares   = risk_budget / stop_distance\n"
    "shares = base x confidence\n"
    "             x regime_factor\n"
    "             x sector_factor\n"
    "\n"
    "regime_factor = 0.75 if risk_off\n"
    "sector_factor = 0.5 if sector >= 20%\n"
    "Capped at 500 shares max\n"
    "\n"
    "Stop Loss: entry x 0.97\n"
    "Take Profit: entry x 1.03",
    15, "#fecaca", bg="#450a0a", stroke="#ef4444", sw=2)

# Z6 -> Z7
arrow(1350, Z6Y+360, [[0,0],[0,50]], label_text="approved orders")

# ═══════════════════════════════════════════════
# ZONE 7 — EXECUTION
# ═══════════════════════════════════════════════
Z7Y = 2430
rect(ZX, Z7Y, ZW, 170, bg="#14532d", stroke="#22c55e", sw=1, opacity=22)
text(ZX+20, Z7Y+8, "EXECUTION", 18, "#86efac")

labeled_rect(350, Z7Y+40, 1900, 100,
    "Alpaca Paper Trading Executor\n"
    "Checks market hours (Alpaca calendar API)  |  Checks circuit_breaker before every order\n"
    "Places market orders (MarketOrderRequest)  |  No retry within same cycle on failure\n"
    "Returns: order_id, symbol, side, qty, status, filled_avg_price, submitted_at",
    15, "#bbf7d0", bg="#052e16", stroke="#22c55e", sw=2)

# Z7 -> Z8
arrow(1350, Z7Y+170, [[0,0],[0,50]], label_text="executed trades")

# ═══════════════════════════════════════════════
# ZONE 8 — FEEDBACK LOOP
# ═══════════════════════════════════════════════
Z8Y = 2650
rect(ZX, Z8Y, ZW, 360, bg="#134e4a", stroke="#14b8a6", sw=1, opacity=22)
text(ZX+20, Z8Y+8, "FEEDBACK LOOP", 18, "#5eead4")

fb_y = Z8Y + 40
fb_h = 280
labeled_rect(130, fb_y, 750, fb_h,
    "Outcome Measurement\n"
    "(runs every 4 hours + market close)\n"
    "\n"
    "Fetch current price via yfinance\n"
    "\n"
    "Exit criteria (first hit wins):\n"
    "  Stop loss price hit\n"
    "  Take profit price hit\n"
    "  Age >= 8 hours\n"
    "\n"
    "Classification:\n"
    "  WIN: return > +1%\n"
    "  LOSS: return < -1%\n"
    "  NEUTRAL: in between",
    14, "#ccfbf1", bg="#042f2e", stroke="#14b8a6", sw=2)

labeled_rect(910, fb_y, 720, fb_h,
    "Weight Updater (EMA)\n"
    "\n"
    "On WIN outcome:\n"
    "  w = w x 0.95 + 1.0 x 0.05\n"
    "On LOSS outcome:\n"
    "  w = w x 0.95 + 0.0 x 0.05\n"
    "On NEUTRAL:\n"
    "  no update\n"
    "\n"
    "Clamp: 0.1 <= weight <= 1.0\n"
    "\n"
    "Updates both:\n"
    "  Strategy weights\n"
    "  Source credibility weights",
    14, "#ccfbf1", bg="#042f2e", stroke="#14b8a6", sw=2)

labeled_rect(1660, fb_y, 780, fb_h,
    "Circuit Breaker\n"
    "\n"
    "Monitors rolling 7-day win rate\n"
    "  (WIN / (WIN + LOSS), excl. NEUTRAL)\n"
    "\n"
    "Trips if:\n"
    "  win_rate < 40%\n"
    "  AND >= 10 qualifying trades\n"
    "\n"
    "On trip:\n"
    "  Halts ALL new trades\n"
    "  Writes to circuit_breaker table\n"
    "  Sends Slack + email alert\n"
    "\n"
    "Manual reset via dashboard / CLI",
    14, "#ccfbf1", bg="#042f2e", stroke="#14b8a6", sw=2)

# Internal feedback arrows
arrow(880, fb_y + fb_h//2, [[0,0],[30,0]], stroke="#14b8a6", sw=2, label_text="outcome", label_color="#5eead4")
arrow(1630, fb_y + fb_h//2, [[0,0],[30,0]], stroke="#14b8a6", sw=2, label_text="win rate", label_color="#5eead4")

# Z8 -> Z9
arrow(1350, Z8Y+360, [[0,0],[0,50]], label_text="persists all data")

# ═══════════════════════════════════════════════
# ZONE 9 — DATABASE
# ═══════════════════════════════════════════════
Z9Y = 3060
rect(ZX, Z9Y, ZW, 820, bg="#0f172a", stroke="#334155", sw=1, opacity=45)
text(ZX+20, Z9Y+10, "TURSO DATABASE (8 Tables)", 20, "#94a3b8")

# Row 1
ty1 = Z9Y + 50
tw = 570
th1 = 190
labeled_rect(130, ty1, tw, th1,
    "trades\n"
    "\n"
    "trade_id TEXT (PK)\n"
    "ticker TEXT, signal TEXT\n"
    "confidence REAL\n"
    "sentiment_score REAL\n"
    "strategies_fired TEXT (JSON)\n"
    "discovery_sources TEXT (JSON)\n"
    "regime_mode TEXT\n"
    "entry_price REAL, shares INT\n"
    "stop_loss_price, take_profit_price\n"
    "order_id TEXT, created_at TEXT",
    13, "#cbd5e1", bg="#1e293b", stroke="#475569", sw=2)

labeled_rect(730, ty1, tw, th1,
    "outcomes\n"
    "\n"
    "trade_id TEXT (PK, FK -> trades)\n"
    "exit_price REAL\n"
    "return_pct REAL\n"
    "outcome TEXT\n"
    "  (WIN / LOSS / NEUTRAL)\n"
    "holding_period_hours REAL\n"
    "measured_at TEXT",
    13, "#cbd5e1", bg="#1e293b", stroke="#475569", sw=2)

labeled_rect(1330, ty1, tw, th1,
    "weights\n"
    "\n"
    "category TEXT\n"
    "  ('strategy' or 'source')\n"
    "name TEXT\n"
    "  (e.g. 'sentiment_divergence')\n"
    "weight REAL (0.1 - 1.0)\n"
    "updated_at TEXT\n"
    "\n"
    "PK: (category, name)",
    13, "#cbd5e1", bg="#1e293b", stroke="#475569", sw=2)

labeled_rect(1930, ty1, tw, th1,
    "circuit_breaker\n"
    "\n"
    "id INTEGER (PK, CHECK id=1)\n"
    "  (single row table)\n"
    "tripped BOOLEAN\n"
    "tripped_at TEXT\n"
    "reason TEXT\n"
    "win_rate_at_trip REAL",
    13, "#cbd5e1", bg="#1e293b", stroke="#475569", sw=2)

# Row 2
ty2 = ty1 + th1 + 30
th2 = 170
labeled_rect(130, ty2, tw, th2,
    "sector_cache\n"
    "\n"
    "ticker TEXT (PK)\n"
    "sector TEXT\n"
    "market_cap REAL\n"
    "avg_volume REAL\n"
    "fetched_at TEXT (7-day TTL)",
    13, "#cbd5e1", bg="#1e293b", stroke="#475569", sw=2)

labeled_rect(730, ty2, tw, th2,
    "sp500_cache\n"
    "\n"
    "id INTEGER (PK, CHECK id=1)\n"
    "  (single row table)\n"
    "tickers TEXT (JSON array)\n"
    "fetched_at TEXT",
    13, "#cbd5e1", bg="#1e293b", stroke="#475569", sw=2)

labeled_rect(1330, ty2, tw, th2,
    "sentiment_history\n"
    "\n"
    "ticker TEXT\n"
    "sentiment_score REAL\n"
    "article_count INTEGER\n"
    "recorded_at TEXT\n"
    "Index: (ticker, recorded_at DESC)",
    13, "#cbd5e1", bg="#1e293b", stroke="#475569", sw=2)

labeled_rect(1930, ty2, tw, th2,
    "discovery_log\n"
    "\n"
    "cycle_id TEXT\n"
    "ticker TEXT\n"
    "source TEXT (news / gainer /\n"
    "  loser / sector_rotation /\n"
    "  position / watchlist)\n"
    "discovered_at TEXT\n"
    "PK: (cycle_id, ticker, source)",
    13, "#cbd5e1", bg="#1e293b", stroke="#475569", sw=2)

# ═══════════════════════════════════════════════
# ZONE 10 — DASHBOARD (right sidebar)
# ═══════════════════════════════════════════════
rect(DX, 100, DW, 900, bg="#1a2e05", stroke="#65a30d", sw=1, opacity=25)
text(DX+20, 108, "STREAMLIT DASHBOARD", 18, "#a3e635")

panels = [
    "Portfolio Overview (equity, cash, P&L)",
    "Positions + Sector Exposure Pie Chart",
    "Trade History with Outcomes",
    "Win/Loss Breakdown + Avg Returns",
    "Cumulative P&L Chart (Plotly)",
    "Learned Weights Bar Charts",
    "Regime Indicator (VIX, SPY, Yield)",
    "Rolling Win Rate + 40% Threshold",
    "Active Tickers + Discovery Sources",
    "Circuit Breaker Status + Reset Btn",
    "Settings: Mode, Watchlist, API Keys",
]
for i, p in enumerate(panels):
    labeled_rect(DX+20, 145 + i*55, DW-40, 45, p, 14, "#d9f99d",
                 bg="#1a2e05", stroke="#65a30d", sw=1)

text(DX+20, 145 + len(panels)*55 + 10, "Reads all 8 Turso tables\n30-60s auto-refresh (st.cache_data)", 14, "#a3e635")

# Arrow from dashboard down to DB
arrow(DX + DW//2, 1000, [[0,0],[0, Z9Y - 950]], stroke="#65a30d", sw=2, dashed=True,
      label_text="reads all tables", label_color="#a3e635")

# ═══════════════════════════════════════════════
# FEEDBACK RETURN ARROWS
# ═══════════════════════════════════════════════

# Left side: dashed red — feedback weights back to combiner
arrow(60, Z5Y + 100, [[0,0],[0, Z8Y + 200 - Z5Y - 100]],
      stroke="#ef4444", sw=2, dashed=True, start="arrow", end=None,
      label_text="adjusts learned\nweights", label_color="#fca5a5")

# Right side: dashed orange — positions back to discovery
arrow(ZX + ZW + 30, Z2Y + 140, [[0,0],[0, Z7Y + 85 - Z2Y - 140]],
      stroke="#f59e0b", sw=2, dashed=True, start="arrow", end=None,
      label_text="track held\npositions", label_color="#fbbf24")


# ═══════════════════════════════════════════════
# WRITE FILE
# ═══════════════════════════════════════════════
doc = {
    "type": "excalidraw",
    "version": 2,
    "source": "https://excalidraw.com",
    "elements": elements,
    "appState": {
        "viewBackgroundColor": "#1a1b26",
        "gridSize": None,
        "theme": "dark"
    },
    "files": {}
}

with open("sys_arch.excalidraw", "w") as f:
    json.dump(doc, f, indent=2)

print(f"Generated sys_arch.excalidraw with {len(elements)} elements")
print(f"Canvas bounds: x=[{min(e.get('x',0) for e in elements)}, {max(e.get('x',0)+e.get('width',0) for e in elements)}]")
print(f"               y=[{min(e.get('y',0) for e in elements)}, {max(e.get('y',0)+e.get('height',0) for e in elements)}]")
