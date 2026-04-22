# trading-claude

Claude-powered intraday trading **signal generator** for US equities. Paper-only, no auto-execution — the agent posts BUY/SELL/HOLD signals with reasoning to Telegram; you execute manually in Alpaca paper.

## Constraints

- **Two decision points per day** (US market time):
  - **Open slot**: 10:00 ET (30min after open) — open a position or stay flat
  - **Close slot**: 15:00 ET (1h before close) — flatten any open position
- **Intraday flat**: no overnight positions
- **Shares only**, long or short, no options/leverage
- **Capital**: <£1000 paper
- **Universe**: SPY + selected ETFs/stocks (see `config.yaml`)
- **Primary metric**: Sharpe ratio vs buy-and-hold SPY

## Components

```
src/
  data/          yfinance wrapper + parquet cache
  strategies/    pluggable intraday strategies
  backtest/      walk-forward sim + Sharpe/Sortino/DD metrics
  broker/        Alpaca paper client (read-only: equity, positions)
  notify/        Telegram notifier
  history/       JSONL store: decisions, rationale, outcomes
  agent/         Claude-driven decision routine
run_backtest.py  CLI: backtest one or all strategies
run_agent.py     CLI: invoked by scheduler at open/close slots
```

## Quick start

```bash
# 1. Install
pip install -r requirements.txt

# 2. Backtest (no keys needed)
python run_backtest.py --strategy orb --symbol SPY --days 500

# 3. Configure credentials
cp config.example.yaml config.yaml   # fill in Alpaca + Telegram
cp .env.example .env                 # ANTHROPIC_API_KEY

# 4. Manual run (what the scheduler will invoke)
python run_agent.py --slot open
python run_agent.py --slot close

# 5. Install schedule (Mac)
bash scripts/setup_launchd.sh
```

## Data limitations

yfinance free tier caps at **2 years of 1h bars**. Good enough for a Sharpe estimate; swap to Alpaca historical bars later if you want longer history.

## Strategies shipped

| Strategy | Thesis |
|---|---|
| `orb` | Opening Range Breakout — break of 9:30–10:00 ET range |
| `gap_fade` | Fade overnight gaps >0.5% |
| `overnight_momentum` | Ride overnight gap direction |
| `vwap_reversion` | Fade >1σ VWAP deviation at 10:00 ET |
| `ensemble` | VIX-regime-gated blend |

All strategies report Sharpe/Sortino/Max DD/excess-return **vs SPY buy-and-hold** baseline.
