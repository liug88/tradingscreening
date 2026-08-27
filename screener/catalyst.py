"""Why did it sell off? -- the one question the screener can't answer itself.

Every other stage of this pipeline is arithmetic. This one needs judgment: a
stock down 20% because the whole sector sold off is a different trade from a
stock down 20% because it lost its largest customer, and no indicator tells
them apart. That's step 3 of Mom's own checklist, and it's the part she was
using Gemini for.

So it uses Gemini. Not for the symmetry: the free tier is the only tier this
project is allowed to have, and one grounded call a weekday morning sits well
inside it. The chat Worker makes the same choice for the same reason.

One call covers all ten names, over raw HTTP rather than the SDK -- the request
is six fields and the Action should not install a dependency to send them. The
response comes back against a JSON schema, so the page never has to parse loose
prose.
"""

from __future__ import annotations

import json
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"

# Pinned rather than left to drift. Google's May 2026 revision is already the
# default, so today this changes nothing -- it is here so the next revision
# lands when someone chooses it rather than on a Tuesday morning.
API_REVISION = "2026-05-20"

MAX_TRIES = 3
BACKOFF = 4  # seconds, doubling

SYSTEM = """You research why stocks have sold off, for someone who sells cash-secured puts.

Her strategy: she sells puts on stocks that have dropped, collecting the premium, and \
she does NOT want the shares assigned to her. So the question she needs answered is not \
"is this a good company" but "is this drop the kind that stabilises, or the kind that \
keeps going for months?"

For each ticker, search recent news and classify the cause of the decline:

- transient: sector rotation, a broad market selloff, a one-off miss, profit-taking, \
an analyst downgrade with no new information. The business is intact.
- structural: lost a major customer or contract, a competitor took the market, \
regulatory action, guidance cut on secular decline, accounting problems, an existential \
threat to the product. The business itself changed.
- uncertain: you could not find a clear cause, or the evidence genuinely points both ways.

Rules:
- Search for actual recent news. Do not infer a cause from the price move alone.
- Say "uncertain" when you don't know. A confident wrong answer costs her real money.
- `headline` is the single specific cause, under 12 words, concrete: "Guidance cut on \
weak China demand" not "Company faces headwinds".
- `reason` is two sentences at most, and must say what you actually found.
- Prefer structural when a real threat to the business exists, even if the stock bounced.

Then write `brief`: three or four sentences about the list as a whole, for someone reading it over coffee. Say what these names have in common (a sector, a shared cause, or nothing at all -- say so if they are unrelated), which one or two setups look cleanest and why, and what on this list deserves the most caution.

The brief describes what the screen found. It never tells her to place a trade, never says what to buy or sell, and never predicts a price. Plain English -- she reads "the drop looks sector-wide", not "beta-driven multiple compression"."""

SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["transient", "structural", "uncertain"]},
                    "headline": {"type": "string"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["ticker", "verdict", "headline", "reason", "confidence"],
            },
        },
        "brief": {"type": "string"},
    },
    "required": ["verdicts", "brief"],
}

# Said in the prompt as well as in the schema, because the schema is the part
# that gets dropped if the API refuses it alongside search grounding. When that
# happens this sentence is the only thing still asking for parseable output.
SHAPE = (
    "\n\nReply with one JSON object and nothing else -- no prose around it, no code "
    'fence. Keys: "verdicts", an array of {ticker, verdict, headline, reason, '
    "confidence} with verdict one of transient/structural/uncertain and confidence "
    'one of low/medium/high; and "brief", a string.'
)


def _prompt(rows: list[dict]) -> str:
    """The ten names with the numbers that make the question answerable."""
    lines = [
        "Research why each of these has sold off. One verdict per ticker, all of them.",
        "",
    ]
    for row in rows:
        tech, fund = row["tech"], row.get("fund") or {}
        off_high = ""
        if tech.get("high_52w"):
            off_high = f", {(1 - tech['close'] / tech['high_52w']):.0%} below its 52-week high"
        change = tech.get("change_5d")
        recent = f", {change:+.1%} over the last five sessions" if change is not None else ""
        earnings = f", next reports {fund['next_earnings']}" if fund.get("next_earnings") else ""
        growth = ""
        if fund.get("revenue_yoy") is not None:
            growth = f", revenue {fund['revenue_yoy']:+.1%} year over year"

        lines.append(
            f"- {row['symbol']} ({row.get('name', row['symbol'])}): ${tech['close']:.2f}"
            f"{off_high}{recent}, RSI {tech.get('rsi14', 0):.0f}{growth}{earnings}"
        )
    return "\n".join(lines)


def _body(rows: list[dict], settings: dict, schema: bool) -> dict:
    """The request. Only fields with a documented example behind them.

    No token ceiling and no effort dial: both exist under names this code cannot
    verify from here, and a wrong field name is a 400 at 6:45 in the morning.
    The prompt is what bounds the length.
    """
    body = {
        "model": settings["model"],
        "system_instruction": SYSTEM,
        "input": _prompt(rows) + ("" if schema else SHAPE),
        "tools": [{"type": "google_search"}],
        # Nothing here is hers, but there is no reason for Google to keep a copy
        # of it either. The history worth having is committed to history/.
        "store": False,
    }
    if schema:
        body["response_format"] = {
            "type": "text",
            "mime_type": "application/json",
            "schema": SCHEMA,
        }
    return body


def _text(data: dict) -> str:
    """The model's answer, however this response happens to be shaped.

    `output_text` is the documented accessor; the walk under it is there because
    a grounded turn returns its search steps in the same timeline, and losing a
    morning's research to a shape change is not worth the two lines saved.
    """
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct

    parts = []
    for step in data.get("steps") or []:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        for block in step.get("content") or []:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
    return "".join(parts)


def _extract(data: dict) -> dict:
    """Pull the day's answer out of that text.

    Tolerant on purpose. With the schema attached this is a plain json.loads;
    without it the model tends to wrap the object in a fence or a sentence, and
    the research is already paid for by the time that shows up.
    """
    text = _text(data).strip()
    if not text:
        return {}

    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}

    return parsed if isinstance(parsed, dict) else {}


def _ask(session, key: str, rows: list[dict], settings: dict) -> dict | None:
    """One answer, or None if the morning has to go without it.

    Two things go wrong here and they deserve different answers. A 429 or a 5xx
    is weather: wait and ask again. A 400 is most likely the schema -- Google
    does not document whether a JSON schema may be attached to a grounded call,
    and if it may not, the fix is to ask again in prose and parse that, not to
    publish a page with ten blank verdicts.
    """
    headers = {
        "x-goog-api-key": key,
        "Content-Type": "application/json",
        "Api-Revision": API_REVISION,
    }
    schema = True

    for attempt in range(MAX_TRIES):
        try:
            response = session.post(
                ENDPOINT,
                headers=headers,
                json=_body(rows, settings, schema),
                timeout=300,  # grounded research is slow, and this runs once a day
            )
        except requests.RequestException as exc:
            log.warning("catalyst: request failed (%s)", exc)
            time.sleep(BACKOFF * 2**attempt)
            continue

        if response.status_code == 200:
            try:
                return response.json()
            except ValueError:
                log.warning("catalyst: response was not JSON")
                return None

        detail = (response.text or "")[:300]
        if response.status_code == 400 and schema:
            log.warning("catalyst: schema refused, asking in prose instead (%s)", detail)
            schema = False
            continue
        if response.status_code in (429, 500, 502, 503, 504):
            log.warning("catalyst: http %d, retrying", response.status_code)
            time.sleep(BACKOFF * 2**attempt)
            continue

        log.warning("catalyst: http %d (%s)", response.status_code, detail)
        return None

    log.warning("catalyst: no answer after %d tries", MAX_TRIES)
    return None


EMPTY: dict = {"verdicts": {}, "brief": None}


def explain(rows: list[dict], config: dict, session=None) -> dict:
    """The day's answer: `verdicts` keyed by ticker, and a list-level `brief`.

    Never raises -- a missing note is a worse page, but an exception at 6:45 in
    the morning is no page at all.
    """
    if not rows:
        return dict(EMPTY)

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        log.warning("catalyst: no GEMINI_API_KEY set, skipping")
        return dict(EMPTY)

    data = _ask(session or requests, key, rows, config["catalyst"])
    if data is None:
        return dict(EMPTY)

    answer = _extract(data)
    verdicts = {
        verdict["ticker"].upper(): verdict
        for verdict in answer.get("verdicts") or []
        if isinstance(verdict, dict) and verdict.get("ticker")
    }
    brief = (answer.get("brief") or "").strip() or None

    usage = data.get("usage") or (data.get("interaction") or {}).get("usage") or {}
    log.info(
        "catalyst: %d verdicts, brief %s, %s tokens",
        len(verdicts), "yes" if brief else "no", usage.get("total_tokens", "?"),
    )
    missing = {row["symbol"] for row in rows} - set(verdicts)
    if missing:
        log.warning("catalyst: no verdict for %s", ", ".join(sorted(missing)))
    return {"verdicts": verdicts, "brief": brief}
