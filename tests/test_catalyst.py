"""The catalyst layer, against a stub session.

These never reach the network. The point is the wiring: that the request is
shaped the way the Interactions API expects, and that every way the call can go
wrong ends with a published page rather than an exception at 6:45 in the
morning.
"""

import json

import pytest
import requests
import yaml

from screener import catalyst


@pytest.fixture(scope="module")
def config():
    with open("config.yaml") as handle:
        return yaml.safe_load(handle)


@pytest.fixture(autouse=True)
def key(monkeypatch):
    """Every test but one runs as if the Action had handed us a key."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Backoff is real in production and pointless here."""
    monkeypatch.setattr(catalyst.time, "sleep", lambda _: None)


class Response:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class StubSession:
    """Records each request and replays canned responses, one per call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.requests.append({"url": url, "headers": headers, "body": json})
        answer = self.responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def answered(payload):
    """A 200 shaped the way the API returns one."""
    return Response(payload={"output_text": json.dumps(payload), "usage": {"total_tokens": 2000}})


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

BRIEF = ("Both names fell with their sectors rather than on company news. NEE is the "
         "cleaner setup: the drop is rate-driven and the business is intact. UNH carries "
         "a live regulatory question, which is the one to be careful with here.")

VERDICTS = {
    "brief": BRIEF,
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
        session = StubSession([answered(VERDICTS)])
        catalyst.explain(ROWS, config, session=session)
        return session.requests[0]

    def test_posts_to_the_interactions_endpoint(self, sent):
        assert sent["url"] == "https://generativelanguage.googleapis.com/v1beta/interactions"

    def test_sends_the_key_and_pins_the_revision(self, sent):
        assert sent["headers"]["x-goog-api-key"] == "test-key"
        assert sent["headers"]["Api-Revision"] == catalyst.API_REVISION

    def test_uses_the_configured_model(self, sent, config):
        assert sent["body"]["model"] == config["catalyst"]["model"]

    def test_the_rules_ride_as_a_system_instruction(self, sent):
        assert sent["body"]["system_instruction"] == catalyst.SYSTEM

    def test_asks_google_not_to_keep_it(self, sent):
        """Nothing here is hers, and there is still no reason to leave a copy."""
        assert sent["body"]["store"] is False

    def test_constrains_the_response_to_the_schema(self, sent):
        fmt = sent["body"]["response_format"]
        assert fmt["mime_type"] == "application/json"
        assert fmt["schema"] == catalyst.SCHEMA

    def test_verdict_is_a_closed_enum(self):
        """The page switches on this value, so it can't be free text."""
        item = catalyst.SCHEMA["properties"]["verdicts"]["items"]
        assert item["properties"]["verdict"]["enum"] == ["transient", "structural", "uncertain"]

    def test_schema_requires_the_brief(self):
        """A list-level summary rides on the same call; the page renders it if
        it is there, so the schema is what guarantees it arrives."""
        assert catalyst.SCHEMA["properties"]["brief"] == {"type": "string"}
        assert set(catalyst.SCHEMA["required"]) == {"verdicts", "brief"}

    def test_enables_search_grounding(self, sent):
        assert sent["body"]["tools"] == [{"type": "google_search"}]

    def test_prompt_carries_the_numbers(self, sent):
        prompt = sent["body"]["input"]
        assert "NEE" in prompt and "$84.22" in prompt
        assert "20% below its 52-week high" in prompt  # 84.22 / 105.00
        assert "next reports 2026-10-28" in prompt


class TestParsing:
    def test_returns_verdicts_keyed_by_ticker(self, config):
        session = StubSession([answered(VERDICTS)])
        result = catalyst.explain(ROWS, config, session=session)
        assert result["verdicts"]["UNH"]["verdict"] == "structural"
        assert result["verdicts"]["NEE"]["headline"] == "Utilities sold off on rate fears"

    def test_returns_the_list_level_brief(self, config):
        session = StubSession([answered(VERDICTS)])
        assert catalyst.explain(ROWS, config, session=session)["brief"] == BRIEF

    def test_a_blank_brief_reads_as_absent(self, config):
        """The page branches on truthiness, so whitespace must not pass for prose."""
        session = StubSession([answered(dict(VERDICTS, brief="   "))])
        assert catalyst.explain(ROWS, config, session=session)["brief"] is None

    def test_verdicts_survive_a_missing_brief(self, config):
        session = StubSession([answered({"verdicts": VERDICTS["verdicts"]})])
        result = catalyst.explain(ROWS, config, session=session)
        assert result["brief"] is None
        assert len(result["verdicts"]) == 2

    def test_reads_the_answer_out_of_the_step_timeline(self, config):
        """A grounded turn shares the timeline with its search steps, and
        output_text is a convenience that a shape change could take away."""
        session = StubSession([Response(payload={"steps": [
            {"type": "google_search_call", "content": [{"type": "text", "text": "why did NEE fall"}]},
            {"type": "model_output", "content": [{"type": "text", "text": json.dumps(VERDICTS)}]},
        ]})])
        assert len(catalyst.explain(ROWS, config, session=session)["verdicts"]) == 2

    def test_unfences_json_the_model_wrapped_in_a_code_block(self, config):
        """Only reachable once the schema has been dropped, which is exactly
        when the answer stops being guaranteed clean."""
        fenced = "```json\n" + json.dumps(VERDICTS) + "\n```"
        session = StubSession([Response(payload={"output_text": fenced})])
        assert len(catalyst.explain(ROWS, config, session=session)["verdicts"]) == 2

    def test_digs_json_out_of_surrounding_prose(self, config):
        chatty = "Here is what I found:\n" + json.dumps(VERDICTS) + "\nHope that helps."
        session = StubSession([Response(payload={"output_text": chatty})])
        assert len(catalyst.explain(ROWS, config, session=session)["verdicts"]) == 2

    def test_lowercase_tickers_still_match(self, config):
        payload = dict(VERDICTS, verdicts=[dict(VERDICTS["verdicts"][0], ticker="nee")])
        session = StubSession([answered(payload)])
        assert "NEE" in catalyst.explain(ROWS, config, session=session)["verdicts"]

    def test_empty_rows_makes_no_call(self, config):
        session = StubSession([])
        assert catalyst.explain([], config, session=session) == {"verdicts": {}, "brief": None}
        assert session.requests == []


class TestFailureModes:
    """Every one of these has to end with a page, not a traceback."""

    def test_no_key_skips_the_call(self, config, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        session = StubSession([])
        assert catalyst.explain(ROWS, config, session=session) == {"verdicts": {}, "brief": None}
        assert session.requests == []

    def test_a_refused_schema_is_asked_again_in_prose(self, config):
        """Google does not document whether a schema may ride along with search
        grounding. If it may not, the answer is still worth having."""
        session = StubSession([
            Response(status_code=400, text="response_format is not supported with google_search"),
            answered(VERDICTS),
        ])
        result = catalyst.explain(ROWS, config, session=session)
        assert len(result["verdicts"]) == 2
        assert "response_format" in session.requests[0]["body"]
        assert "response_format" not in session.requests[1]["body"]

    def test_the_prose_retry_says_what_shape_it_wants(self, config):
        """The schema was the only thing asking for parseable output; once it is
        gone the prompt has to."""
        session = StubSession([Response(status_code=400, text="nope"), answered(VERDICTS)])
        catalyst.explain(ROWS, config, session=session)
        assert catalyst.SHAPE in session.requests[1]["body"]["input"]

    def test_a_second_400_gives_up_rather_than_looping(self, config):
        session = StubSession([
            Response(status_code=400, text="nope"),
            Response(status_code=400, text="still nope"),
        ])
        assert catalyst.explain(ROWS, config, session=session) == {"verdicts": {}, "brief": None}
        assert len(session.requests) == 2

    def test_retries_a_rate_limit(self, config):
        session = StubSession([Response(status_code=429, text="slow down"), answered(VERDICTS)])
        assert len(catalyst.explain(ROWS, config, session=session)["verdicts"]) == 2

    def test_retries_a_network_failure(self, config):
        session = StubSession([requests.ConnectionError("reset"), answered(VERDICTS)])
        assert len(catalyst.explain(ROWS, config, session=session)["verdicts"]) == 2

    def test_gives_up_after_repeated_failures(self, config):
        session = StubSession([Response(status_code=503, text="down")] * catalyst.MAX_TRIES)
        assert catalyst.explain(ROWS, config, session=session) == {"verdicts": {}, "brief": None}

    def test_an_auth_failure_stops_immediately(self, config):
        """A bad key will be a bad key on the third try too."""
        session = StubSession([Response(status_code=403, text="invalid api key")])
        assert catalyst.explain(ROWS, config, session=session) == {"verdicts": {}, "brief": None}
        assert len(session.requests) == 1

    def test_a_body_that_is_not_json_returns_nothing(self, config):
        session = StubSession([Response(payload=None, text="<html>502</html>")])
        assert catalyst.explain(ROWS, config, session=session) == {"verdicts": {}, "brief": None}

    def test_unparseable_content_returns_nothing(self, config):
        session = StubSession([Response(payload={"output_text": "not json"})])
        assert catalyst.explain(ROWS, config, session=session) == {"verdicts": {}, "brief": None}

    def test_a_partial_answer_is_still_used(self, config):
        """Nine verdicts beat none."""
        session = StubSession([answered(dict(VERDICTS, verdicts=[VERDICTS["verdicts"][0]]))])
        result = catalyst.explain(ROWS, config, session=session)
        assert set(result["verdicts"]) == {"NEE"}
