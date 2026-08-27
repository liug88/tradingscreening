"""The orchestrator's own logic: what gets published, and what she has seen before.

Nothing here touches the network. The pipeline stages are stubbed out, because
the questions worth testing are the two decisions run.py makes on its own --
where to cut the list, and whether a name is a repeat.
"""

import json
from datetime import date

import pytest
import yaml

from screener import run


@pytest.fixture(scope="module")
def config():
    with open("config.yaml") as handle:
        return yaml.safe_load(handle)


def row(symbol, strike=50.0, expiry="2026-09-30"):
    return {
        "symbol": symbol,
        "name": symbol + " Inc.",
        "tech": {"close": 100.0},
        "trade": {"strike": strike, "expiry": expiry},
    }


def published(as_of, picks):
    """One history file's worth of payload."""
    return {"as_of": as_of, "picks": picks}


def presented(symbol, strike=50.0, expiry="2026-09-30"):
    return {"symbol": symbol, "trade": {"strike": strike, "expiry": expiry}}


class TestContract:
    def test_identifies_a_put_by_strike_and_expiry(self):
        assert run._contract(row("AAA")) == (50.0, "2026-09-30")

    def test_no_trade_is_not_a_contract(self):
        assert run._contract({"symbol": "AAA"}) is None

    def test_a_half_filled_trade_is_not_a_contract(self):
        """A missing strike must not compare equal to another missing strike."""
        assert run._contract({"trade": {"expiry": "2026-09-30"}}) is None
        assert run._contract({"trade": {"strike": 50.0}}) is None


class TestMarkRepeats:
    def test_a_name_not_seen_before_is_new(self):
        rows = [row("AAA")]
        run._mark_repeats(rows, [published("2026-08-25", [presented("BBB")])])
        assert rows[0]["seen"] is None

    def test_no_history_at_all_leaves_everything_new(self):
        """Day one: the feature ships inert rather than guessing."""
        rows = [row("AAA")]
        run._mark_repeats(rows, [])
        assert rows[0]["seen"] is None

    def test_the_same_contract_is_flagged_as_the_same(self):
        rows = [row("AAA", strike=50.0, expiry="2026-09-30")]
        run._mark_repeats(rows, [
            published("2026-08-25", [presented("AAA", 50.0, "2026-09-30")]),
        ])
        assert rows[0]["seen"] == {"days": 2, "same_contract": True, "since": "2026-08-25"}

    def test_a_different_strike_is_a_new_angle(self):
        rows = [row("AAA", strike=47.5)]
        run._mark_repeats(rows, [published("2026-08-25", [presented("AAA", 50.0)])])
        assert rows[0]["seen"]["same_contract"] is False

    def test_a_different_expiry_is_a_new_angle(self):
        rows = [row("AAA", expiry="2026-10-30")]
        run._mark_repeats(rows, [published("2026-08-25", [presented("AAA")])])
        assert rows[0]["seen"]["same_contract"] is False

    def test_counts_consecutive_days_including_today(self):
        rows = [row("AAA")]
        run._mark_repeats(rows, [
            published("2026-08-25", [presented("AAA")]),
            published("2026-08-24", [presented("AAA")]),
            published("2026-08-21", [presented("AAA")]),
        ])
        assert rows[0]["seen"]["days"] == 4
        assert rows[0]["seen"]["since"] == "2026-08-21"

    def test_a_gap_ends_the_streak(self):
        """Back today after a day off is a shorter story than back every day."""
        rows = [row("AAA")]
        run._mark_repeats(rows, [
            published("2026-08-25", [presented("AAA")]),
            published("2026-08-24", [presented("BBB")]),
            published("2026-08-21", [presented("AAA")]),
        ])
        assert rows[0]["seen"]["days"] == 2
        assert rows[0]["seen"]["since"] == "2026-08-25"

    def test_the_bench_does_not_count_as_seen(self):
        """She reads the ten. A name that only sat on the bench is new to her."""
        past = {"as_of": "2026-08-25", "picks": [presented("BBB")],
                "bench": [presented("AAA")]}
        rows = [row("AAA")]
        run._mark_repeats(rows, [past])
        assert rows[0]["seen"] is None

    def test_compares_against_the_most_recent_appearance(self):
        """Two days ago is not what she is holding in mind; yesterday is."""
        rows = [row("AAA", strike=50.0)]
        run._mark_repeats(rows, [
            published("2026-08-25", [presented("AAA", 50.0)]),
            published("2026-08-24", [presented("AAA", 42.0)]),
        ])
        assert rows[0]["seen"]["same_contract"] is True


class TestPastRuns:
    def test_reads_newest_first_and_skips_today(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run, "HISTORY_DIR", tmp_path)
        for day in ("2026-08-24", "2026-08-25", "2026-08-26"):
            (tmp_path / (day + ".json")).write_text(json.dumps(published(day, [])))
        runs = run._past_runs(date(2026, 8, 26))
        assert [r["as_of"] for r in runs] == ["2026-08-25", "2026-08-24"]

    def test_stops_at_the_lookback(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run, "HISTORY_DIR", tmp_path)
        for day in range(1, 20):
            name = "2026-08-%02d" % day
            (tmp_path / (name + ".json")).write_text(json.dumps(published(name, [])))
        assert len(run._past_runs(date(2026, 8, 26))) == run.REPEAT_LOOKBACK

    def test_a_corrupt_history_file_does_not_stop_the_run(self, tmp_path, monkeypatch):
        """A half-written file from a killed run must not cost her the morning."""
        monkeypatch.setattr(run, "HISTORY_DIR", tmp_path)
        (tmp_path / "2026-08-25.json").write_text("{ truncated")
        (tmp_path / "2026-08-24.json").write_text(json.dumps(published("2026-08-24", [])))
        assert [r["as_of"] for r in run._past_runs(date(2026, 8, 26))] == ["2026-08-24"]

    def test_no_history_directory_is_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run, "HISTORY_DIR", tmp_path / "nothing-here")
        assert run._past_runs(date(2026, 8, 26)) == []


class TestBench:
    """Everything past the options stage is already scored and priced. The cut
    at ten decides what she reads, not what was computed."""

    @pytest.fixture
    def payload(self, config, monkeypatch, tmp_path):
        rows = [row("S%02d" % i) for i in range(25)]
        monkeypatch.setattr(run, "HISTORY_DIR", tmp_path)
        monkeypatch.setattr(run.universe, "load", lambda *a, **k: [r["symbol"] for r in rows])
        monkeypatch.setattr(run, "YahooSession", lambda *a, **k: None)
        monkeypatch.setattr(run, "_technicals_stage", lambda *a, **k: rows)
        monkeypatch.setattr(run, "_fundamentals_stage", lambda rows_, *a, **k: rows_)
        monkeypatch.setattr(run, "_options_stage", lambda rows_, *a, **k: rows_)
        monkeypatch.setattr(run, "_add_buzz", lambda rows_: [])
        # Score descending by position, so the expected order is known.
        monkeypatch.setattr(run.score, "score", lambda r, c: {
            "score": 100.0 - int(r["symbol"][1:]),
            "badges": [], "penalties": [], "components": {},
        })
        return run.build(config, use_ai=False, as_of=date(2026, 8, 26))

    def test_publishes_ten_names(self, payload, config):
        assert len(payload["picks"]) == config["funnel"]["final"]

    def test_keeps_the_rest_on_the_bench(self, payload, config):
        assert len(payload["bench"]) == 25 - config["funnel"]["final"]

    def test_the_bench_is_the_names_that_just_missed(self, payload):
        assert payload["picks"][-1]["symbol"] == "S09"
        assert payload["bench"][0]["symbol"] == "S10"

    def test_rank_runs_unbroken_through_the_bench(self, payload):
        ranks = [p["rank"] for p in payload["picks"] + payload["bench"]]
        assert ranks == list(range(1, 26))

    def test_a_bench_name_carries_a_tradeable_put(self, payload):
        """The bench is only useful if a swapped-in name renders like the rest."""
        assert payload["bench"][0]["trade"]["strike"] == 50.0

    def test_no_brief_without_the_ai_layer(self, payload):
        assert payload["brief"] is None
        assert payload["catalyst_ran"] is False
