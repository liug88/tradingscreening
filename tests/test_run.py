"""The orchestrator's own logic: what gets published, and what she has seen before.

Nothing here touches the network. The pipeline stages are stubbed out, because
the questions worth testing are the two decisions run.py makes on its own --
where to cut the list, and whether a name is a repeat.
"""

import json
import re
from datetime import date
from pathlib import Path

import pytest
import yaml

from screener import run

ROOT = Path(__file__).resolve().parents[1]

# Every way either scorer names a technicals field: `tech.get("x")`,
# `tech["x"]`, `_recent(tech, "low", "today")`, and the two JS spellings.
# Literals go in character classes rather than behind backslashes -- the
# patterns read no worse and cannot be mangled by whatever writes this file.
PY_READS = re.compile(
    'tech[.]get[(][ ]*"([a-z0-9_]+)"'
    # The bare dot is the subscript bracket: nothing but `tech["x"]` puts a
    # quote one character after `tech`, and a class holding `[` warns.
    '|tech.[ ]*"([a-z0-9_]+)"'
    '|_recent[(][ ]*tech,[ ]*"([a-z0-9_]+)",[ ]*"([a-z0-9_]+)"')
JS_READS = re.compile(
    'tech[.]([A-Za-z_][A-Za-z0-9_]*)'
    '|recent[(][ ]*tech,[ ]*"([a-z0-9_]+)",[ ]*"([a-z0-9_]+)"')


def keys_read(pattern, path):
    source = (ROOT / path).read_text(encoding="utf-8")
    return {g for m in pattern.finditer(source) for g in m.groups() if g}


@pytest.fixture(scope="module")
def config():
    with open("config.yaml") as handle:
        return yaml.safe_load(handle)


def row(symbol, strike=50.0, expiry="2026-09-30", put=True):
    """One candidate out of the options stage. `put=False` is a name that
    cleared every gate about the company and had no fillable contract."""
    return {
        "symbol": symbol,
        "name": symbol + " Inc.",
        "tech": {"close": 100.0},
        "trade": {"strike": strike, "expiry": expiry} if put else None,
    }


def stub_scores(monkeypatch):
    """Score descending by the digits in the symbol, on every ranking, so the
    expected order is known and the same on all three."""
    monkeypatch.setattr(run.score, "score", lambda r, c, profile="put": {
        "score": 100.0 - int(r["symbol"][1:]),
        "score_before_penalties": 100.0,
        "badges": [], "penalties": [], "components": {},
    })
    monkeypatch.setattr(run.score, "badges", lambda r, c: [])


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


class TestPublishedTechnicals:
    """The page re-scores from the published file and nothing else.

    So a field the scorer reads but `_present` does not publish is not a
    cosmetic omission: it is absent in the browser, the browser reads absent as
    "a file from before this rule shipped" and falls back to the old model, and
    the ranking she is handed stops matching the one her sliders produce. There
    is nothing on screen to say so, which is what makes it worth a test rather
    than care.

    Both directions of the funnel are pinned here: what the scorers read has to
    be published, and what is published has to be computable.
    """

    def test_python_reads_nothing_the_payload_leaves_out(self):
        missing = keys_read(PY_READS, "screener/score.py") - set(run.PUBLISHED_TECHNICALS)
        assert not missing, f"score.py reads but run.py never publishes: {sorted(missing)}"

    def test_the_browser_reads_nothing_the_payload_leaves_out(self):
        missing = keys_read(JS_READS, "site/score.js") - set(run.PUBLISHED_TECHNICALS)
        assert not missing, f"score.js reads but run.py never publishes: {sorted(missing)}"

    def test_the_scan_actually_finds_the_fields(self):
        """A regex that quietly matches nothing would pass both tests above."""
        found = keys_read(PY_READS, "screener/score.py")
        assert {"rsi_min_recent", "stoch_d", "golden_cross"} <= found
        assert "close" in keys_read(JS_READS, "site/score.js")


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
        stub_scores(monkeypatch)
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


class TestThreeRankings:
    """The same pool, ordered three ways, in one file.

    The pipeline is stubbed as in TestBench; what is under test is the shape
    run.py publishes, not the model. Two names carry no put and the highest
    scores in the set, which is the case that used to be thrown away at the
    options stage and now has to survive it without contaminating the list she
    reads.
    """

    @pytest.fixture
    def payload(self, config, monkeypatch, tmp_path):
        rows = [row("S%02d" % i) for i in range(10, 30)]
        rows += [row("S00", put=False), row("S05", put=False)]
        monkeypatch.setattr(run, "HISTORY_DIR", tmp_path)
        monkeypatch.setattr(run.universe, "load", lambda *a, **k: [r["symbol"] for r in rows])
        monkeypatch.setattr(run, "YahooSession", lambda *a, **k: None)
        monkeypatch.setattr(run, "_technicals_stage", lambda *a, **k: rows)
        monkeypatch.setattr(run, "_fundamentals_stage", lambda rows_, *a, **k: rows_)
        monkeypatch.setattr(run, "_options_stage", lambda rows_, *a, **k: rows_)
        monkeypatch.setattr(run, "_add_buzz", lambda rows_: [])
        stub_scores(monkeypatch)
        return run.build(config, use_ai=False, as_of=date(2026, 8, 26))

    @staticmethod
    def every(payload):
        return payload["picks"] + payload["bench"]

    def test_a_name_with_no_put_is_still_published(self, payload):
        """The whole point of the change. She can buy a stock there is no put
        worth selling against, and these were being dropped unseen."""
        assert {"S00", "S05"} <= {p["symbol"] for p in self.every(payload)}

    def test_it_never_reaches_the_list_she_sells_from(self, payload):
        """It outscores every other name in the set and still cannot be a pick,
        because there is no contract to sell."""
        assert all(p["trade"] for p in payload["picks"])
        assert payload["picks"][0]["symbol"] == "S10"

    def test_its_sell_puts_score_is_absent_rather_than_low(self, payload):
        """Zero would read as a bad trade. There is no trade."""
        card = next(p for p in self.every(payload) if p["symbol"] == "S00")
        assert card["score"] is None
        assert card["trade"] is None
        assert card["components"] == {}
        assert card["penalties"] == []

    def test_but_it_is_ranked_on_the_lists_that_apply(self, payload):
        card = next(p for p in self.every(payload) if p["symbol"] == "S00")
        assert card["buy"]["score"] == 100.0
        assert card["long"]["score"] == 100.0

    def test_every_card_carries_every_ranking(self, payload):
        for card in self.every(payload):
            for profile in run.OTHER_RANKINGS:
                assert set(card[profile]) == {
                    "score", "score_before_penalties", "components", "penalties"}

    def test_the_put_ranking_stays_where_the_page_reads_it(self, payload):
        """Top level, unnested. An older page reading a newer file must find
        the sell-puts list exactly where it has always been."""
        card = payload["picks"][0]
        assert card["score"] == 90.0
        assert "score" not in card.get("put", {})

    def test_the_put_less_names_sort_to_the_end_of_the_file(self, payload):
        symbols = [p["symbol"] for p in payload["bench"]]
        assert symbols[-2:] == ["S00", "S05"]

    def test_rank_runs_unbroken_through_them(self, payload):
        ranks = [p["rank"] for p in self.every(payload)]
        assert ranks == list(range(1, 23))

    def test_the_browser_gets_the_weights_for_every_ranking(self, payload, config):
        """Same invariant as the technicals allowlist: the page re-scores from
        the published file alone, so a weight block left out means a slider
        that silently falls back to an older model."""
        published_cfg = payload["config"]
        for profile in run.OTHER_RANKINGS:
            assert published_cfg["weights_" + profile] == config["weights_" + profile]

    def test_and_the_penalties_a_ranking_charges_extra(self, payload, config):
        assert payload["config"]["penalties_long"] == config["penalties_long"]

    def test_a_new_profile_cannot_be_added_and_left_unpublished(self):
        """score.PROFILES is where a ranking is declared. If one appears there
        without joining this tuple, it scores in Python and does not exist in
        the browser -- the failure the publish contract exists to prevent."""
        from screener import score

        assert set(score.PROFILES) == {"put", *run.OTHER_RANKINGS}
