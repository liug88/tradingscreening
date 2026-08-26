"""The catalyst layer, against a stub client.

These never reach the network. The point is the wiring: that the request is
shaped the way the API expects, and that every way the call can go wrong ends
with a published page rather than an exception at 6:45 in the morning.
"""

import json
from types import SimpleNamespace

import pytest
import yaml

from screener import catalyst


@pytest.fixture(scope="module")
def config():
    with open("config.yaml") as handle:
        return yaml.safe_load(handle)


def text_block(payload: dict):
    return SimpleNamespace(type="text", text=json.dumps(payload))


def search_block():
    """Server tool results share the content list with the answer."""
    return SimpleNamespace(type="web_search_tool_result", content=[])


def message(content, stop_reason="end_turn", **extra):
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=1200, output_tokens=800),
        **extra,
    )


class StubClient:
    """Records the request and replays canned responses, one per call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        outer = self

        class Stream:
            def __init__(self, kwargs):
                outer.requests.append(kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get_final_message(self):
                return outer.responses.pop(0)

        self.beta = SimpleNamespace(
            messages=SimpleNamespace(stream=lambda **kwargs: Stream(kwargs))
        )


ROWS = [
    {
        "symbol": "NEE",
        "name": "NextEra Energy, Inc.",
        "tech": {"close": 84.22, "rsi14": 36.5, "high_52w": 105.0, "change_5d": -0.06},
        "fund": {"revenue_yoy": 0.08, "next_earnings": "2026-10-28"},
    },
    {
        "symbol": "UNH",
        "name": "UnitedHealth Group Incorporated",
        "tech": {"close": 401.01, "rsi14": 47.7, "high_52w": 620.0, "change_5d": 0.02},
        "fund": {"revenue_yoy": -0.03, "next_earnings": "2026-10-14"},
    },
]

VERDICTS = {
    "verdicts": [
        {"ticker": "NEE", "verdict": "transient", "headline": "Utilities sold off on rate fears",
         "reason": "Sector-wide move on the rate outlook.", "confidence": "high"},
        {"ticker": "UNH", "verdict": "structural", "headline": "DOJ probe into billing practices",
         "reason": "Regulatory action, not a market move.", "confidence": "medium"},
    ]
}


class TestRequestShape:
    @pytest.fixture
    def sent(self, config):
        client = StubClient([message([search_block(), text_block(VERDICTS)])])
        catalyst.explain(ROWS, config, client=client)
        return client.requests[0]

    def test_uses_the_configured_model(self, sent, config):
        assert sent["model"] == config["catalyst"]["model"]

    def test_adaptive_thinking(self, sent):
        assert sent["thinking"] == {"type": "adaptive"}

    def test_constrains_the_response_to_the_schema(self, sent):
        assert sent["output_config"]["format"]["type"] == "json_schema"
        assert sent["output_config"]["format"]["schema"] == catalyst.SCHEMA

    def test_verdict_is_a_closed_enum(self):
        """The page switches on this value, so it can't be free text."""
        item = catalyst.SCHEMA["properties"]["verdicts"]["items"]
        assert item["properties"]["verdict"]["enum"] == ["transient", "structural", "uncertain"]
        assert item["additionalProperties"] is False

    def test_enables_web_search(self, sent, config):
        tool = sent["tools"][0]
        assert tool["type"] == "web_search_20260209"
        assert tool["max_uses"] == config["catalyst"]["max_web_searches"]

    def test_refusal_fallbacks_use_the_matching_beta_header(self, sent):
        """The scalar `default` form pairs only with the 07-01 header; the other
        combination is a 400."""
        assert sent["fallbacks"] == "default"
        assert sent["betas"] == ["server-side-fallback-2026-07-01"]

    def test_caches_the_system_prompt(self, sent):
        assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}

    def test_prompt_carries_the_numbers(self, sent):
        prompt = sent["messages"][0]["content"]
        assert "NEE" in prompt and "$84.22" in prompt
        assert "20% below its 52-week high" in prompt  # 84.22 / 105.00
        assert "next reports 2026-10-28" in prompt


class TestParsing:
    def test_returns_verdicts_keyed_by_ticker(self, config):
        client = StubClient([message([search_block(), text_block(VERDICTS)])])
        result = catalyst.explain(ROWS, config, client=client)
        assert result["UNH"]["verdict"] == "structural"
        assert result["NEE"]["headline"] == "Utilities sold off on rate fears"

    def test_finds_json_after_server_tool_blocks(self, config):
        """output_config guarantees valid JSON, not that it's the first block."""
        client = StubClient([message([search_block(), search_block(), text_block(VERDICTS)])])
        assert len(catalyst.explain(ROWS, config, client=client)) == 2

    def test_lowercase_tickers_still_match(self, config):
        payload = {"verdicts": [dict(VERDICTS["verdicts"][0], ticker="nee")]}
        client = StubClient([message([text_block(payload)])])
        assert "NEE" in catalyst.explain(ROWS, config, client=client)

    def test_empty_rows_makes_no_call(self, config):
        client = StubClient([])
        assert catalyst.explain([], config, client=client) == {}
        assert client.requests == []


class TestFailureModes:
    """Every one of these has to end with a page, not a traceback."""

    def test_a_refusal_returns_nothing_rather_than_raising(self, config):
        client = StubClient([
            message([], stop_reason="refusal",
                    stop_details=SimpleNamespace(category="other", explanation="declined"))
        ])
        assert catalyst.explain(ROWS, config, client=client) == {}

    def test_resumes_a_paused_search_turn(self, config):
        """The long-search failure mode: a paused turn must be continued, not
        treated as the final answer."""
        client = StubClient([
            message([search_block()], stop_reason="pause_turn"),
            message([text_block(VERDICTS)]),
        ])
        result = catalyst.explain(ROWS, config, client=client)
        assert len(client.requests) == 2
        assert len(result) == 2

    def test_a_resumed_call_carries_the_earlier_turn(self, config):
        client = StubClient([
            message([search_block()], stop_reason="pause_turn"),
            message([text_block(VERDICTS)]),
        ])
        catalyst.explain(ROWS, config, client=client)
        assert len(client.requests[1]["messages"]) == 2
        assert client.requests[1]["messages"][1]["role"] == "assistant"

    def test_gives_up_after_repeated_pauses(self, config):
        client = StubClient([
            message([search_block()], stop_reason="pause_turn")
            for _ in range(catalyst.MAX_RESUMES)
        ])
        assert catalyst.explain(ROWS, config, client=client) == {}

    def test_unparseable_content_returns_nothing(self, config):
        client = StubClient([message([SimpleNamespace(type="text", text="not json")])])
        assert catalyst.explain(ROWS, config, client=client) == {}

    def test_a_partial_answer_is_still_used(self, config):
        """Nine verdicts beat none."""
        payload = {"verdicts": [VERDICTS["verdicts"][0]]}
        client = StubClient([message([text_block(payload)])])
        result = catalyst.explain(ROWS, config, client=client)
        assert set(result) == {"NEE"}
