# Daily put screen — how it works

## The problem

Every morning Mom typed the same screening criteria into Gemini and Perplexity,
compared the two answers, and lost the morning to it. The lists came back
different each day and usually held three names, because a chat model running a
web search is not a screener. It cannot hold a set of rules steady from one day
to the next.

So the rules moved into code. A real screener runs the numbers; the language
model is left with the one job it is good at — reading the news and saying *why*
a stock fell.

What she gets is a bookmark. She opens it, ten ranked names are already there,
each with a concrete cash-secured put to look at. Same criteria every day.

## Her morning

1. Open the page.
2. Read the ten rows. Each shows how many of the eleven criteria it passes, the
   odds of keeping the premium, the suggested strike, and why the stock fell.
3. Press **Copy for cross-check**. That puts the whole day's list into the
   clipboard as a ready-made prompt.
4. Paste it into Gemini or Perplexity. She has always cross-checked; this makes
   it ten seconds instead of an hour.

Every quote on the page is delayed and reflects the prior close. The page says
so. She verifies the strike in her broker before trading.

## The pipeline

The run narrows at every stage, cheapest checks first. That is what keeps it
inside a free GitHub Actions run and stops the paid step from ever touching a
name that is already out.

```
570 symbols with weekly options   (CBOE weeklys list, refreshed weekly)
        |  one year of daily prices each  (Yahoo chart v8, concurrent)
        |  RSI, Williams %R, MACD, EMAs, ATR, volume, support
        v
      ~150  best on technicals
        |  quarterly revenue and margins  (Yahoo timeseries; cached a quarter)
        v
       ~75  best on technicals + fundamentals
        |  full option chain each  (CBOE delayed quotes, with greeks)
        |  pick the target put, apply the gates
        v
        10  ranked
        |  one Claude call covering all ten -> catalyst verdicts
        |  ApeWisdom -> Reddit mention counts
        v
     site/data/latest.json  ->  GitHub Pages
```

A full run takes about 80 seconds without the AI step.

## What it decides

### Gates — fail one and the name is dropped

These only ask whether she could actually trade it. They are not opinions about
the stock.

- price ≥ $10, 30-day average volume ≥ 500k, market cap ≥ $1B
- RSI was under 50 at some point in the lookback, and is under 65 today
- a put exists at 21–56 days out with delta between 0.10 and 0.35
- that contract: bid ≥ $0.20, open interest ≥ 100, spread ≤ 15% of mid

The RSI test is two-sided because the setup is "oversold *but bouncing*", and a
single threshold can only say half of that. Set at 50 it drops names that have
started to recover; measured only on the recent low it lets back in names that
have already run.

### Score — everything else, 0 to 100

Nothing below is a gate, which is what keeps the list ten deep every day.

| Component | Weight | What it measures |
|---|---|---|
| Oversold | 20 | RSI (full credit 28–38), Williams %R under −80 |
| Bounce confirmed | 15 | price over EMA9/EMA20, MACD crossing up below zero, volume on up days |
| Premium richness | 20 | the put's IV against 20-day realised volatility |
| Sales growth | 15 | revenue up more than 10% year over year, positive quarter over quarter |
| Margin trend | 10 | gross and operating margin, quarter over quarter |
| Strike safety | 10 | strike against support and the 52-week low, breakeven in ATRs |
| Trade quality | 10 | annualised yield on the cash secured, docked for a wide spread |

### Penalties — subtracted from the score

Earnings before expiry (−25), IV/HV over 2.5 (−15), down more than 15% in five
sessions (−15), a new 52-week low while under the 200-day EMA (−10), and a
catalyst the AI layer judged structural (−30).

Every threshold and weight above lives in `config.yaml`. Tuning the screen does
not mean touching the code.

### Why it does not just AND the rules together

Her rules ANDed return nothing most days — the same frustration she had with
Gemini. Ranking always returns ten, and each row shows which rules it actually
passes, so "ranked third" is never a black box. Ten names typically miss five or
six of the eleven criteria. That is the design, not a fault: these are the best
ten available, not ten perfect matches.

## The put it picks

Nearest expiry to 35 days out inside a 21–56 day window, then the put whose
delta is closest to 0.20 and which clears the liquidity gates. The window has to
be wider than one monthly cycle or a stock with monthly-only expiries falls
between two expiries and is dropped for no good reason.

Delta is the point. It is close enough to the market's own estimate of the odds
the put finishes in the money — which is the odds she is assigned the stock she
was trying not to buy. The page states it as plain odds of keeping the premium,
not as a decimal.

The row reports strike, expiry, bid/ask, credit, delta, IV, open interest, cash
required, annualised return, breakeven, and distance to support.

## The AI layer

One Claude call a day, covering all ten names at once. `claude-opus-5`, adaptive
thinking, medium effort, with the server-side web search tool for recent news.
The response comes back against a JSON schema, so the page never parses loose
prose.

It answers one question per name: did this stock fall for a reason that passes,
or for one that does not? The verdict is `transient`, `structural`, or
`uncertain`, with a headline and a short reason. A `structural` verdict costs
the name 30 points.

The model never picks the stocks and never ranks them. That split is the whole
design.

Cost is roughly $0.40–0.80 a day. It is the only thing here that is not free.
`--no-ai` skips it.

## Data sources

| What | Where | Key needed |
|---|---|---|
| Symbols with weekly options | CBOE weeklys CSV | no |
| Daily OHLCV, one year | Yahoo `chart/v8` | no |
| Option chains with delta and IV | CBOE delayed quotes | no |
| Quarterly revenue and margins | Yahoo `fundamentals-timeseries` | cookie + crumb |
| Earnings date | Yahoo `quoteSummary` | cookie + crumb |
| Reddit mentions | ApeWisdom | no |
| Catalyst verdict | Claude API | `ANTHROPIC_API_KEY` |

CBOE throttles by cumulative volume rather than by rate, so `options.py` backs
off globally across threads once it is told to.

Everything CBOE-specific sits in `fetch_chain()`. If that feed changes, one
function gets rewritten — `select_put()` works on our own shapes.

## Layout

```
.github/workflows/daily-screen.yml   the schedule, the commit, the deploy
config.yaml                          every threshold and weight
screener/
  universe.py    CBOE weeklys -> symbols
  prices.py      Yahoo chart v8, concurrent, with backoff
  technicals.py  RSI, Williams %R, MACD, EMAs, ATR, volume, support
  fundamentals.py  revenue and margin trend
  options.py     CBOE chain -> the target put
  buzz.py        ApeWisdom
  score.py       gates, score, penalties, badges
  catalyst.py    the Claude call
  cache.py       JSON cache on disk, committed back to the repo
  run.py         the orchestrator and CLI
site/            the page: index.html, style.css, app.js, data/
cache/           fundamentals.json, iv_history.json, universe.json
history/         YYYY-MM-DD.json, one per run, kept
DESIGN.md        the page's visual system
PRODUCT.md       who it is for and what it must not do
```

## Running it

```bash
pip install -r requirements.txt

python -m screener.run --dry-run --no-ai --limit 20   # 20 names, no spend, writes nothing
python -m screener.run --no-ai                        # full run, no spend
python -m screener.run                                # full run with the AI layer
pytest
```

`--date YYYY-MM-DD` runs as of another day. `-v` turns on debug logging.

A run writes `site/data/latest.json` and `history/<date>.json`. To see the page,
serve `site/` over HTTP — opening `index.html` from disk will not work, because
the page fetches its data file.

## The schedule

`.github/workflows/daily-screen.yml` runs at 10:45 UTC on weekdays: 6:45am ET in
summer, 5:45am ET in winter. Both are before the open, which is deliberate —
every quote is the prior close either way.

It also runs on demand (`workflow_dispatch`, with a switch to skip the paid
step), and on any push that touches `site/**` so a design change redeploys
without spending anything.

Setting it up once:

1. Repo **Settings → Pages → Source: GitHub Actions**. Pages needs a public
   repo on a free account, which is why nothing personal is in here.
2. Repo **Settings → Secrets and variables → Actions**, add `ANTHROPIC_API_KEY`.
   The key lives there and nowhere else. It is never committed.

Each run commits `cache/` and `history/` back, which is how the cache survives
between throwaway runners and how the IV history accumulates.

## When it breaks

`site/data/latest.json` is gitignored, so a fresh checkout never has one. On a
market holiday, on a design push, or after a failed run, the workflow copies the
newest file in `history/` into place and publishes that instead. The file
carries its own date, and the page raises a stale banner once that date is more
than four days old. A stale list she can see the date of beats a blank page.

A failed screen still turns the run red, so it does not pass unnoticed.

The holiday list is hardcoded in the workflow through 2027. Once it runs out the
workflow starts writing a warning into the log. Adding a year is one line.

## What compounds

`cache/iv_history.json` records each name's at-the-money IV every day. After
about three months it supports a true IV percentile — is this premium rich *for
this stock* — which is what a put seller actually wants and which no free source
gives away. Until then the page says "building" rather than guessing.

`history/` keeps every past list, so the question of whether the weights are any
good becomes answerable later.

## What it does not do

- It does not connect to a broker, and holds no account data, positions, or
  personal information. It never will.
- It does not tell her to place a trade. It surfaces names to research.
- There is no backtest, no win rate, and no evidence that the screen picks
  winners. Nothing on the page implies otherwise.

Selling puts on stocks that have fallen means deliberately catching falling
knives, and high implied volatility is the market pricing a real chance of a
large move. The premium is not free. The job here is to price that visibly, not
to hide it.
