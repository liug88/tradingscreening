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
inside a free GitHub Actions run and stops the AI step from ever spending a
request on a name that is already out.

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
        |  one Gemini call covering all ten -> catalyst verdicts
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
| RSI + Williams %R | 20 | the *lowest* RSI of the last 10 sessions, full credit 28–38; Williams %R under −80 |
| IV richness | 20 | half the put's IV against 20-day realised volatility, half where that IV sits in this name's own year |
| EMA / MACD / volume | 15 | price over EMA9/EMA20, MACD crossing up below zero, volume expanding on up days |
| Revenue growth | 15 | revenue up more than 10% year over year, positive quarter over quarter |
| Margin trend | 10 | gross and operating margin against the same quarter last year |
| Strike cushion | 10 | breakeven measured in ATRs, and the strike sitting under the 60-day low |
| Annualised yield | 10 | annualised return on the cash secured, docked for a wide spread |

Each is named for what it measures rather than for what it feels like, and the
tuning sliders on the page print these same names — a component is called one
thing in one place. The oversold test reads the *recent low* RSI, not today's,
so a name can score well on it while today's reading has already recovered.

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

Alongside it travels a ladder of `alternatives`: the other puts on that name
that would actually fill, aimed at deltas from 0.10 to 0.35, ordered safest
first. Every one carries the full set of fields the chosen put carries, so a
strike she switches to is scored from exactly the same shape.

The ladder is drawn from every expiry in the DTE window, not just the chosen
one. Measured on real chains, a single expiry holds nought to four fillable
puts — the open-interest and spread filters are strict, and should be —
so one expiry gave 33 alternatives across 43 names, which is not a control.
Window-wide gives 101. Ordered by delta rather than strike, because across
expiries the strike no longer says which one is safer.

It costs nothing to compute. The chain is already downloaded and every one of
these contracts was already examined and set aside.

## The AI layer

One Gemini call a day, covering all ten names at once. A Flash model on the
free tier, grounded with Google Search for recent news, over raw HTTP against
the Interactions API — the request is six fields and the Action should not
install an SDK to send them. The response comes back against a JSON schema, so
the page never parses loose prose.

Google does not document whether a schema may ride along with search grounding.
If a request comes back 400, the call is made again without the schema and the
answer is parsed out of prose. That is a worse guarantee, not a worse page.

It answers one question per name: did this stock fall for a reason that passes,
or for one that does not? The verdict is `transient`, `structural`, or
`uncertain`, with a headline and a short reason. A `structural` verdict costs
the name 30 points.

The same call returns one `brief` — three or four sentences on what the ten have
in common, which setups look strongest, and what to be careful of. It rides on
the request that was already being made, so it costs almost nothing. It
describes what the screen found. It never says what to buy or sell, and never
predicts a price.

The model never picks the stocks and never ranks them. That split is the whole
design.

It is free, which is the requirement rather than a nice result: one call a
weekday morning sits well inside the free tier's daily allowance. `--no-ai`
skips it. `store: false` on the request asks Google not to keep a copy.

## What gets published

### The bench

The options stage leaves roughly 75 names with a full chain, a chosen put and a
score. Ranking them and keeping ten used to throw the other 65 away. They now
ship in the same file under `bench`, in the same shape as `picks`, with `rank`
running unbroken from 1 through the end.

It costs one slice and no extra fetches, and it is what lets the page answer
"show me ten different ones" without a re-run. A re-run would return the
identical list anyway: same data, same criteria.

Two things the bench is not. Bench names carry no catalyst note — only the ten
are researched, and the page says so when she pages past the first ten. And the
bench is never promoted after the AI layer lands a structural penalty: the ten
are re-sorted among themselves, because swapping in a name with no researched
note would leave a hole on the page.

### Repeats

`select_put()` takes the expiry nearest 35 days out and the delta nearest 0.20.
Day over day those inputs barely move, so a name that keeps scoring well hands
back the *identical contract* — same strike, same expiry — for days running.

`_mark_repeats()` reads the last six history files and stamps each name with
`seen`: how many consecutive days it has appeared, whether the contract is the
same one, and the date the streak started. A gap ends a streak. Only past
`picks` count, never the bench — she never saw the bench.

Three states, and the distinction is the whole point:

| `seen` | Means |
|---|---|
| absent | New today |
| `same_contract: true` | Back again with nothing new to look at |
| `same_contract: false` | Back again on a different strike or expiry — a second angle |

Nothing is ever hidden and nothing is penalised. A name still oversold and still
bouncing on day three is a true result; suppressing it would misrepresent the
screen. The page marks it and gives her a filter, and the choice stays hers.

### The tuning panel

The file also carries the settings it was scored under — the weights, the
scoring thresholds, the penalty points, the gates, the option rules — under
`config`. `site/score.js` is a port of `screener/score.py` that reads them, and
the panel on the page turns them into sliders.

**Why the config ships.** A weight has one home. Change `config.yaml`, and the
next morning the sliders start from the new number, because the page was never
told what the weights are — it was handed them.

**What it can do.** Re-rank every name already in the file, and swap the target
put for one of the other strikes published beside it. Both components that read
the contract move with the swap, which is the point: a safer strike really does
earn a different score, and the page should say so. It is arithmetic over data
already on her machine — no network, no server, no wait.

**What it cannot do.** Widen the net. The gates ran at 570 symbols and a name
they dropped was never published to be re-scored, so no slider can bring it
back. The panel says so where she will read it. Widening is a re-run.

**Why the score stays out of 100.** The sliders are relative importance, not
points. `normalise()` rescales them to sum to 100 before scoring, so a tuned
score is still comparable to the one the file shipped with. At the shipped
weights, which already sum to 100, it is the identity.

**The parity requirement.** At the shipped settings, `rescore()` must return the
score the file already carries — otherwise she is moving a lookalike, not the
model. `tests/test_score_parity.py` holds it to that against a real published
payload, name by name and field by field.

Two things break the equality while looking correct, and both have:

- **Adding with a plain `reduce`.** Python's `sum()` has done compensated
  addition since 3.12; JavaScript's does not. Seven components came to `72.05`
  one way and `72.05000000000001` the other. `fsum()` is the answer.
- **Rounding by scaling.** `Math.round(v * 10) / 10` on `47.049999999999997`
  multiplies to *exactly* `470.5` — a tie the number never had — and rounds up.
  Python decides on the exact value and never sees a tie. `round()` reads the
  decimal expansion instead.

Each was worth a tenth of a point on one name in forty, which is enough to
reorder the list. Anyone touching either copy of the model needs to know.

### Two files, two audiences

`site/data/latest.json` is written compact — the browser downloads it every
morning. `history/YYYY-MM-DD.json` is written indented and key-sorted, because
it is committed and a readable diff is the point of keeping it.

## The chat

The page can answer questions about its own list. That is a Cloudflare Worker
in `worker/`, deployed on its own and reached over CORS — the Action, Pages and
the daily deploy know nothing about it, and the page renders its ten names
whether or not it is up.

A turn goes: the browser posts the passphrase and the conversation so far, the
Worker checks the passphrase and a daily counter in KV, fetches `latest.json`
itself, and streams Gemini's answer back as plain text. Same free tier as the
morning run, same reason.

Fetching the data server-side is what lets the chat see the bench without the
browser downloading it, so "why isn't NVDA on here?" is answerable at no cost to
the page's load. The rules and the file go in as one system instruction, the
file last, so every turn of a session shares a prefix Gemini caches on its own.
The conversation is re-sent in full each turn rather than kept server-side under
`previous_interaction_id`, because storing it would mean Google holding her
morning; `store: false` says the same thing about the request itself.

The system prompt is in `RULES` at the top of `src/index.js` and it draws one
line: it explains, it never advises. "Should I sell this put?" comes back as
what the numbers say and what the risks are, and the decision stays with her.
The day's data is labelled as data, never as instructions.

Nothing personal passes through it. There is no account, no position and no
holding anywhere in the request — the passphrase is a lock on the day's
allowance, not a login, so that a leaked URL cannot use up the questions she
meant to ask.

There is no bill to bound, so the limits bound requests instead: Google's
free-tier daily quota, a turn counter in the Worker set below it, and a
per-question character limit. The counter trips first on purpose — it fails with
a sentence she can read rather than a 429 she cannot.

`CHAT_URL` in `site/app.js` is empty until the Worker is deployed. Empty is a
working state — the panel simply never appears. See `worker/README.md` to
deploy it.

## Data sources

| What | Where | Key needed |
|---|---|---|
| Symbols with weekly options | CBOE weeklys CSV | no |
| Daily OHLCV, one year | Yahoo `chart/v8` | no |
| Option chains with delta and IV | CBOE delayed quotes | no |
| Quarterly revenue and margins | Yahoo `fundamentals-timeseries` | cookie + crumb |
| Earnings date | Yahoo `quoteSummary` | cookie + crumb |
| Reddit mentions | ApeWisdom | no |
| Catalyst verdict | Gemini API, free tier | `GEMINI_API_KEY` |

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
  catalyst.py    the Gemini call
  cache.py       JSON cache on disk, committed back to the repo
  run.py         the orchestrator and CLI
site/            the page: index.html, style.css, app.js, data/
  score.js       score.py again, in the browser, for the sliders
  method.html    the backtest, written out once, limits above numbers
tools/
  backtest.py    what the screen would have picked, run over past dates
worker/          the chat, deployed separately to Cloudflare
  src/index.js   passphrase, daily cap, the Gemini call, the streamed answer
  wrangler.toml  the origin it trusts and the file it reads
cache/           fundamentals.json, iv_history.json, universe.json
history/         YYYY-MM-DD.json, one per run, kept
DESIGN.md        the page's visual system
PRODUCT.md       who it is for and what it must not do
```

## Running it

```bash
pip install -r requirements.txt

python -m screener.run --dry-run --no-ai --limit 20   # 20 names, no AI, writes nothing
python -m screener.run --no-ai                        # full run, no AI
python -m screener.run                                # full run with the AI layer
pytest
```

`--date YYYY-MM-DD` runs as of another day. `-v` turns on debug logging.

A run writes `site/data/latest.json` and `history/<date>.json`. To see the page,
serve `site/` over HTTP — opening `index.html` from disk will not work, because
the page fetches its data file.

## The schedule

`.github/workflows/daily-screen.yml` runs at 12:30 UTC on weekdays: 8:30am ET in
summer, 7:30am ET in winter. Both are before the open, which is deliberate —
every quote is the prior close either way.

It also runs on demand (`workflow_dispatch`, with a switch to skip the catalyst
step), and on any push that touches `site/**` so a design change redeploys the
page without re-running the screen.

Setting it up once:

1. Repo **Settings → Pages → Source: GitHub Actions**. Pages needs a public
   repo on a free account, which is why nothing personal is in here.
2. Repo **Settings → Secrets and variables → Actions**, add `GEMINI_API_KEY`.
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

## The backtest

`tools/backtest.py` re-runs the screen on a past date and measures what happened
next. `python -m tools.backtest --monthly` walks it forward a month at a time
from September 2024 — 23 dates, 230 trades. `site/method.html` is that output
written out by hand, once, for her to read.

`technicals.compute()` only ever reads the tail of the frame it is handed, so
truncating a price history to a past date yields exactly the indicators that
morning's run would have computed. That part is honest. Four things are not, and
the page states all four above its first number:

- **The option is estimated.** CBOE serves the current chain and nothing else,
  and no free source keeps historical quotes. The strike comes from
  Black-Scholes on realised vol at r=0, which sits closer to the money than the
  real one — the error runs toward being too strict. `trade_quality` and the
  fillable-put gate are gone outright, and most of `strike_safety` with them.
- **The fundamentals are gone too.** Today's revenue against a year-ago pick is
  look-ahead, so `sales_growth` and `margin_trend` are dropped rather than
  faked. What is left reconstructs 49 of the shipped 100 points.
- **The universe survived.** It is today's CBOE weeklys list, so anything
  delisted in the last two years was never a candidate.
- **The tape only went one way.** Two years, mostly rising. Nothing here has
  been through a market that fell for a year.

It runs two variants — price signals alone, and price signals plus the
recoverable halves of premium and strike — because the gap between them is the
finding. The tilt cut assignments from 26% to 18% for the same average return,
which is the thesis working. Against every name that merely qualified, though,
the top ten returned less: +0.7% against +1.8% over 35 days. It buys safety, and
safety costs upside. Both readings are on the page.

## What it does not do

- It does not connect to a broker, and holds no account data, positions, or
  personal information. It never will.
- It does not tell her to place a trade. It surfaces names to research.
- It does not claim a track record. There is a backtest now, and it is on the
  page, but it reconstructs half the model against an estimated option and its
  limits are printed above its numbers. It is evidence about the ranking. It is
  not a win rate, and nothing on the page may round it up into one.

Selling puts on stocks that have fallen means deliberately catching falling
knives, and high implied volatility is the market pricing a real chance of a
large move. The premium is not free. The job here is to price that visibly, not
to hide it.
