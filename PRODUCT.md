# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One primary user: the project owner's mother, a self-directed retail options trader.

She sells cash-secured puts on stocks that have sold off, collecting the premium,
and specifically does **not** want the shares assigned to her. She trades this
strategy already and understands it well — but she thinks in outcomes ("will I
keep the premium?") rather than in greeks.

Her situation each morning: she opens a browser at a desk, before or around the
market open, wanting a short list of names worth researching further. Today that
morning costs her hours. There is no second audience; nothing about this product
needs to serve anyone else.

## Product Purpose

Replace a repetitive manual chat-prompting ritual with one bookmark.

Every morning she re-types the same screening criteria into Gemini and
Perplexity, cross-checks the two, and burns the whole morning. The answers come
back inconsistent — different names each time, usually only three — because a
chat model doing web search is not a screener. It cannot hold her criteria stable
from one day to the next.

Success is narrow and testable: she opens one page, and ten names are already
there, screened against the same rules every day, each with a concrete
put-selling setup. She stops re-typing criteria. Her morning comes back.

## Positioning

The mechanism is the position: **a real screener runs the numbers, and the
language model is demoted to the one job it is actually good at.**

Every ranking input — RSI, Williams %R, MACD, EMAs, ATR, revenue growth, margin
trend, implied volatility, option delta, open interest, bid/ask spread — is
computed deterministically from market data. The LLM never picks the stocks. It
answers only the question arithmetic cannot: *why* did this name sell off, and is
that cause transient or structural?

That split is what a chat prompt cannot copy. It is also what makes the list
consistent day over day, which was her actual complaint.

## Operating Context

- **Ritual:** a morning routine, at a desk, before deciding what to research.
- **Cross-checking is a feature of her process, not a flaw.** She deliberately
  runs the same question past multiple LLMs. The product should make that take
  ten seconds instead of an hour, not try to talk her out of it.
- **Prior tooling:** Gemini and Perplexity, via a search box. She has noted they
  cannot load PowerPoints and similar external files.
- **Delivery:** a static page, published on a schedule by a scheduled job. She
  never installs, configures, runs, or logs into anything. A bookmark is the
  entire interface contract.
- **Data is delayed.** The run happens outside market hours and every quote
  reflects the prior close. She verifies live in her broker before trading.
  The product must never imply otherwise.

## Capabilities and Constraints

**Confirmed capabilities**

- Ten ranked names daily, always ten — never three, never zero.
- A concrete cash-secured put per name: expiry, strike, credit, assignment odds,
  cash required, breakeven, annualized return.
- A per-name badge row showing which of her criteria each name passes and fails,
  so the ranking is inspectable rather than a black box.
- Reddit mention counts and the day's most-discussed tickers — she asked for this
  by name. It is context only and never affects the ranking.
- A catalyst verdict per name (transient / structural / uncertain) with a
  headline and a short reason, written by the LLM from recent news.
- Every past day's list is kept.

**Hard constraints**

- **Free.** Every data source is free and unauthenticated, and the LLM layer
  runs on Gemini's free tier with no card on file. Nothing here is billed, and
  with no card there is no way for it to start being billed by accident.
- **No sensitive data.** No brokerage connection, no account data, no positions,
  no personal information anywhere in the repository or on the page. This is a
  standing constraint, stated by the user, and applies to every future change.
- **Zero install for the user.** No login, no setup, no local runtime.
- Quotes are delayed; the page must say so plainly and visibly.

**Terminology**

Present outcomes in plain English first — "85% chance you keep the premium"
rather than a bare delta. The trader vocabulary (delta, IV, IV/HV, DTE,
annualized yield) stays available on demand for the numbers she wants to check,
but it is never the primary label. Confirmed with the user.

**Undecided**

- Whether the list should ever be filtered or re-sorted by the reader.
- Whether scoring weights get tuned against accumulated history later.

## Brand Commitments

None. No existing name, logo, palette, or identity constraint. This is a private
tool for one person, not a public product.

Voice: plain and direct. Short words over long ones. No inflated claims, no
hedging, and — given the subject — no implied promises about outcomes.

## Evidence on Hand

- `Trading Screening.docx` — her own written screening criteria. The source of
  truth for what the screen tests.
- The user's notes from a conversation with her, describing the current morning
  workflow and its failures.
- `history/` — every past daily list, accumulating from first run.
- `cache/iv_history.json` — per-symbol implied volatility, accumulating daily. It
  becomes a true IV percentile after roughly three months.
- `tools/backtest.py` and `site/method.html` — what each of the three rankings
  would have picked each month since September 2022, and what happened over the
  35 days after (180 for long). Part of the model — 49 points of 100 for the put,
  70 for buy, 50 for long — an estimated strike, a surviving universe, and a
  rising tape.

**Absences future work must not fabricate:** there are no users besides her, no
testimonials, no performance record, no win rate, and no evidence whatsoever
that the screen picks winners. The backtest is the single narrow exception:
1,360 reconstructed positions across three rankings, each scoring part of its
model, published with its four limits stated above its numbers. Two of the three
results are unflattering and the page says so. Nothing may round any of it up
into a track record.

## Product Principles

1. **The numbers are computed, not narrated.** Anything a formula can decide is
   decided by a formula. The language model explains; it never ranks.
2. **Consistency is the product.** The same criteria produce the same list from
   the same data, every day. Inconsistency is the failure mode she came here to
   escape — a name silently dropped by a network error is a correctness bug.
3. **Be honest about the risk.** Selling puts on oversold stocks is deliberately
   catching falling knives, and high implied volatility is the market pricing a
   real chance of a large move. The premium is not free. The product's job is to
   price that risk visibly, not to hide it.
4. **Show the work.** Every rank is traceable to criteria she can read. A name
   that fails a rule says so on its face.
5. **Research tool, not advice.** It surfaces candidates to investigate. It never
   tells her to place a trade.

## Accessibility & Inclusion

Confirmed requirements, to be built in from the start rather than retrofitted:

- **Larger text by default.** A generous base type size and line height, not a
  reliance on browser zoom.
- **High contrast**, comfortably past WCAG AA — this is read in daylight.
- **Never color alone.** Every pass/fail badge and every positive/negative figure
  carries a word, shape, or icon as well as a color. This also covers color
  blindness.
