# tradingscreening

Ten stocks every morning, screened against the same criteria every day, each
with a cash-secured put to look at. Built for Mom, to replace the hour she spent
re-typing the same prompt into Gemini and Perplexity.

A GitHub Action runs before the open and publishes a page. She opens a bookmark.
Nothing to install, nothing to log into.

## Quick start

```bash
pip install -r requirements.txt
python -m screener.run --no-ai        # full run, no AI, ~80 seconds
python -m http.server -d site 8000    # then open http://localhost:8000
```

`--dry-run` prints the table and writes nothing. `--limit 20` scans 20 symbols.
Drop `--no-ai` to include the catalyst step — one grounded Gemini call that asks
why each of the ten sold off. It needs `GEMINI_API_KEY` and runs on the free
tier; nothing in this project is billed.

Every threshold and weight lives in `config.yaml`. Tuning the screen does not
mean touching the code.

## How it works

[`docs/specs/screener.md`](docs/specs/screener.md) — the pipeline, the gates, the
score, the data sources, the schedule, and what happens when a run fails.

[`DESIGN.md`](DESIGN.md) — the page's visual system.
[`PRODUCT.md`](PRODUCT.md) — who it is for and what it must not do.

## Two things to be clear about

Quotes are delayed and reflect the prior close. Verify the strike live before
placing any trade.

This is a research tool, not advice. There is a backtest — 230 trades since
September 2024, and four reasons it proves less than it looks like, all set out
on the page — but no track record. Selling puts on stocks that have fallen means
deliberately catching falling knives; the premium is not free.
