"""Why did it sell off? -- the one question the screener can't answer itself.

Every other stage of this pipeline is arithmetic. This one needs judgment: a
stock down 20% because the whole sector sold off is a different trade from a
stock down 20% because it lost its largest customer, and no indicator tells
them apart. That's step 3 of Mom's own checklist, and it's the part she was
using Gemini for.

One call covers all ten names. The response comes back against a JSON schema,
so the page never has to parse loose prose.

Note on approach: the obvious shape here is a tool-calling loop with a
`record_catalyst` tool, but that tool would do no work -- it only records. A
single call with a schema-constrained response gets the same guarantee with no
loop, and sidesteps a documented failure mode where the Python tool runner
exits silently on `pause_turn` mid-search.
"""

from __future__ import annotations

import json
import logging

import anthropic

log = logging.getLogger(__name__)

MAX_RESUMES = 4  # a long search turn can pause; resume it rather than truncate

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
- Prefer structural when a real threat to the business exists, even if the stock bounced."""

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
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


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


def _extract(message) -> list[dict]:
    """Pull the schema-constrained JSON out of the response.

    Server tool results share the content list with the answer, so the text
    block isn't reliably first.
    """
    for block in message.content:
        if getattr(block, "type", None) != "text":
            continue
        try:
            return json.loads(block.text).get("verdicts", [])
        except (json.JSONDecodeError, AttributeError):
            continue
    return []


def explain(rows: list[dict], config: dict, client=None) -> dict[str, dict]:
    """Catalyst verdicts keyed by ticker. Returns {} rather than raising."""
    if not rows:
        return {}

    settings = config["catalyst"]
    client = client or anthropic.Anthropic()
    messages = [{"role": "user", "content": _prompt(rows)}]

    message = None
    for _ in range(MAX_RESUMES):
        with client.beta.messages.stream(
            model=settings["model"],
            max_tokens=8000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            thinking={"type": "adaptive"},
            output_config={
                "effort": settings["effort"],
                "format": {"type": "json_schema", "schema": SCHEMA},
            },
            system=[{
                "type": "text",
                "text": SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            tools=[{
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": settings["max_web_searches"],
            }],
            messages=messages,
        ) as stream:
            message = stream.get_final_message()

        if message.stop_reason != "pause_turn":
            break
        # A long run of searches can pause mid-turn; hand it back to continue.
        messages.append({"role": "assistant", "content": message.content})
    else:
        log.warning("catalyst: still paused after %d resumes, using what we have", MAX_RESUMES)

    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        log.warning("catalyst: refused (%s)", getattr(details, "explanation", "no detail"))
        return {}

    verdicts = {
        verdict["ticker"].upper(): verdict
        for verdict in _extract(message)
        if verdict.get("ticker")
    }

    usage = message.usage
    log.info(
        "catalyst: %d verdicts, %d in / %d out tokens",
        len(verdicts), usage.input_tokens, usage.output_tokens,
    )
    missing = {row["symbol"] for row in rows} - set(verdicts)
    if missing:
        log.warning("catalyst: no verdict for %s", ", ".join(sorted(missing)))
    return verdicts
