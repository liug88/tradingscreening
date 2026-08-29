"""The browser's copy of the model against the published file.

site/score.js exists so the sliders re-rank the list without a re-run. That is
only honest if, at the settings the screener shipped with, it returns exactly
what the file already carries -- otherwise she is moving a lookalike.

The equality is fragile in ways that look correct. It has broken twice: once on
adding the components with a plain reduce, where Python's sum() carries the
rounding error forward and JavaScript's does not, and once on rounding by
scaling, where 47.049999999999997 times ten is exactly 470.5 and rounds up. Both
were worth a tenth of a point on one name in forty, which is enough to reorder a
list. Hence a test that compares every field rather than the score alone.

The fixture is a real published payload, trimmed. USB is in it deliberately: it
is the name both bugs showed up on.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from screener import score

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / "tests" / "score_bridge.js"
FIXTURE = ROOT / "tests" / "fixtures" / "parity.json"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="needs node to run the browser's copy of the model",
)


def run_bridge(job: dict) -> dict:
    """Hand a job to score.js under node and read the answer back."""
    done = subprocess.run(
        ["node", str(BRIDGE)],
        input=json.dumps(job),
        capture_output=True,
        text=True,
        encoding="utf-8",  # node speaks UTF-8; the Windows locale is cp1252
        cwd=ROOT,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.fixture(scope="module")
def published() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def recomputed(published) -> dict:
    answer = run_bridge({"config": published["config"], "rows": published["names"]})
    by_symbol = {row["symbol"]: row for row in answer["scored"]}
    by_symbol["__order__"] = answer["order"]
    return by_symbol


class TestAgainstThePublishedFile:
    def test_the_fixture_is_a_real_payload(self, published):
        """A hand-written fixture would only prove score.js agrees with itself."""
        assert len(published["names"]) >= 10
        assert {"weights", "scoring", "penalties"} <= set(published["config"])
        assert "USB" in {row["symbol"] for row in published["names"]}

    def test_every_score_is_reproduced(self, published, recomputed):
        wrong = [
            (row["symbol"], row["score"], recomputed[row["symbol"]]["score"])
            for row in published["names"]
            if recomputed[row["symbol"]]["score"] != row["score"]
        ]
        assert not wrong, f"score.js disagrees with the file on {wrong}"

    def test_usb_lands_on_the_published_side_of_the_boundary(self, recomputed):
        """The regression itself: a gross of 72.05 and 25 of penalty leave a
        value whose exact decimal sits just under 47.05. Naive addition or
        scaled rounding each push it to 47.1 on their own."""
        assert recomputed["USB"]["score"] == 47.0

    @pytest.mark.parametrize("field", ["raw", "points", "max"])
    def test_every_component_is_reproduced(self, published, recomputed, field):
        wrong = []
        for row in published["names"]:
            mine = recomputed[row["symbol"]]["components"]
            for name, component in row["components"].items():
                if mine[name][field] != component[field]:
                    wrong.append((row["symbol"], name, component[field], mine[name][field]))
        assert not wrong

    def test_every_penalty_is_reproduced(self, published, recomputed):
        """Compared field by field: the file is key-sorted and score.js is not,
        so comparing serialised objects would fail on key order alone."""
        for row in published["names"]:
            mine = recomputed[row["symbol"]]["penalties"]
            assert len(mine) == len(row["penalties"]), row["symbol"]
            for got, want in zip(mine, row["penalties"]):
                assert got["points"] == want["points"], row["symbol"]
                assert got["reason"] == want["reason"], row["symbol"]

    def test_the_order_is_reproduced(self, published, recomputed):
        """A score right to the tenth still ranks wrong if the tie-breaks move,
        and the order is the part of this she actually reads."""
        expected = [
            row["symbol"]
            for row in sorted(published["names"], key=lambda r: -r["score"])
        ]
        assert recomputed["__order__"] == expected


# Rows built to fire each penalty, for the comparison below. Kept at module
# scope so the fixture that runs them can be too.
PENALTY_CASES = {
    "nothing wrong": {
        "tech": {"close": 50.0, "change_5d": 0.01},
        "fund": {"next_earnings": "2026-12-01"},
        "trade": {"expiry": "2026-10-16", "strike": 45.0},
    },
    "earnings before expiry": {
        "tech": {"close": 50.0, "change_5d": 0.01},
        "fund": {"next_earnings": "2026-10-15"},
        "trade": {"expiry": "2026-10-16", "strike": 45.0},
    },
    "earnings on the expiry itself": {
        "tech": {"close": 50.0, "change_5d": 0.01},
        "fund": {"next_earnings": "2026-10-16"},
        "trade": {"expiry": "2026-10-16", "strike": 45.0},
    },
    "iv far above realized": {
        "tech": {"close": 50.0, "change_5d": 0.01},
        "trade": {"expiry": "2026-10-16", "strike": 45.0},
        "iv_hv": 3.4,
    },
    "still falling": {
        "tech": {"close": 50.0, "change_5d": -0.19},
        "trade": {"expiry": "2026-10-16", "strike": 45.0},
    },
    "new low under the 200-day": {
        "tech": {"close": 50.0, "change_5d": 0.0, "at_52w_low": True, "above_ema200": False},
        "trade": {"expiry": "2026-10-16", "strike": 45.0},
    },
    # The RBLX shape: 74% below its high, the 50 under the 200, nothing turning.
    "confirmed downtrend": {
        "tech": {"close": 50.0, "change_5d": 0.0, "above_ema200": False,
                 "golden_cross": False, "pct_above_52w_low": 0.11,
                 "pct_below_52w_high": 0.73},
        "trade": {"expiry": "2026-10-16", "strike": 45.0},
    },
    # Same wreck, but bouncing -- the milder penalty, not the downtrend one.
    "down but turning": {
        "tech": {"close": 50.0, "change_5d": 0.0, "above_ema200": False,
                 "golden_cross": False, "pct_above_52w_low": 0.11,
                 "pct_below_52w_high": 0.73, "above_ema9": True,
                 "up_day_volume_expansion": True},
        "trade": {"expiry": "2026-10-16", "strike": 45.0},
    },
    # Near the low but the 50 is still over the 200: the trend is intact.
    "near the low, trend intact": {
        "tech": {"close": 50.0, "change_5d": 0.0, "above_ema200": False,
                 "golden_cross": True, "pct_above_52w_low": 0.04},
        "trade": {"expiry": "2026-10-16", "strike": 45.0},
    },
    "sitting exactly on the low": {
        "tech": {"close": 50.0, "change_5d": 0.0, "above_ema200": False,
                 "golden_cross": True, "pct_above_52w_low": 0.001},
        "trade": {"expiry": "2026-10-16", "strike": 45.0},
    },
    "structural selloff": {
        "tech": {"close": 50.0, "change_5d": 0.0},
        "trade": {"expiry": "2026-10-16", "strike": 45.0},
        "catalyst": {"verdict": "structural", "headline": "DOJ probe into billing"},
    },
    "everything at once": {
        "tech": {"close": 50.0, "change_5d": -0.30, "at_52w_low": True, "above_ema200": False},
        "fund": {"next_earnings": "2026-10-01"},
        "trade": {"expiry": "2026-10-16", "strike": 45.0},
        "iv_hv": 4.0,
        "catalyst": {"verdict": "structural", "headline": "DOJ probe into billing"},
    },
}


def as_published(row: dict) -> dict:
    """The same row under the names run.py publishes it by."""
    out = {k: v for k, v in row.items() if k not in ("tech", "fund")}
    out["technicals"] = row["tech"]
    out["fundamentals"] = row.get("fund")
    return out


@pytest.fixture(scope="module")
def live_config(published) -> dict:
    """The fixture's config with the shipping penalty rules merged over it.

    The published file is deliberately frozen -- the test above needs it to
    re-score to exactly what it says. But comparing the two implementations is
    a different question, and it should be asked about the rules that ship.
    """
    import yaml

    live = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    merged = dict(published["config"])
    merged["penalties"] = live["penalties"]
    merged["scoring"] = live["scoring"]
    # The rankings that did not exist when the fixture was frozen. Taken whole
    # from config.yaml, so the comparison below runs on the shipping weights.
    for key, value in live.items():
        if key.startswith(("weights_", "penalties_")):
            merged[key] = value
    return merged


def as_internal(row: dict) -> dict:
    """Published shape back to the one score.py reads.

    The two halves of the model name the same block differently -- run.py
    publishes `technicals`, score.py takes `tech` -- so a row can only be handed
    to both after one of them is renamed.
    """
    inner = dict(row)
    inner["tech"] = row.get("technicals") or {}
    inner["fund"] = row.get("fundamentals")
    # And the call's contract, which the file keeps inside the ranking that
    # scored it while score.py takes it at the top level, under the key the
    # options stage set. The put's needs no rename: it is `trade` in both.
    if isinstance(row.get("call"), dict):
        inner["call"] = row["call"].get("contract")
    return inner


# Four readings, at a spread of levels, plus the two "not published yet" cases
# the guard has to survive. Cycled over the fixture's names so the comparison
# covers a real row's other fields rather than a hand-built stub.
COMPOSITE_CASES = [
    {"stoch_d": 8.0, "stoch_cross_up": True, "mfi14": 11.0, "bb_percent_b": -0.2},
    {"stoch_d": 19.9, "stoch_cross_up": False, "mfi14": 20.0, "bb_percent_b": 0.0},
    {"stoch_d": 35.0, "stoch_cross_up": True, "mfi14": 62.0, "bb_percent_b": 0.35},
    {"stoch_d": 80.0, "stoch_cross_up": False, "mfi14": 90.0, "bb_percent_b": 1.4},
    {"stoch_d": 12.0, "stoch_cross_up": False, "mfi14": 15.0, "bb_percent_b": -0.05,
     "stoch_d_min_recent": 4.0, "mfi_min_recent": 9.0, "bb_percent_b_min_recent": -0.3},
    # Present but null, the shape a short history publishes.
    {"stoch_d": None, "stoch_cross_up": False, "mfi14": None, "bb_percent_b": None},
    {},  # the key absent entirely -- a file from before the composite shipped
]


@pytest.fixture(scope="module")
def paired(published, live_config):
    """Each case scored by both implementations, on a real row's other fields."""
    rows = []
    for i, extra in enumerate(COMPOSITE_CASES):
        row = json.loads(json.dumps(published["names"][i]))
        row["technicals"].update(extra)
        row["symbol"] = f"{row['symbol']}-{i}"
        rows.append(row)
    answer = run_bridge({"config": live_config, "rows": rows})
    mine = [score.score(as_internal(r), live_config) for r in rows]
    return list(zip(rows, mine, answer["scored"]))


class TestTheOversoldComposite:
    """The composite reads four things where the published fixture has two, so
    nothing in the file exercises it. These rows do, in both implementations at
    once: a value that ramps differently in JavaScript would move the ten."""

    def test_the_cases_actually_span_the_component(self, paired):
        """A parity test that only ever compares 0.0 to 0.0 proves nothing."""
        seen = {round(m["components"]["oversold"]["raw"], 4) for _, m, _ in paired}
        assert len(seen) >= 5, f"only {len(seen)} distinct readings: {seen}"

    def test_both_implementations_agree_on_the_raw_reading(self, paired):
        wrong = [
            (row["symbol"], mine["components"]["oversold"]["raw"],
             theirs["components"]["oversold"]["raw"])
            for row, mine, theirs in paired
            if mine["components"]["oversold"]["raw"]
            != theirs["components"]["oversold"]["raw"]
        ]
        assert not wrong

    def test_both_agree_on_every_component_and_the_total(self, paired):
        for row, mine, theirs in paired:
            assert mine["score"] == theirs["score"], row["symbol"]
            for name, got in mine["components"].items():
                assert got == theirs["components"][name], (row["symbol"], name)

    def test_a_row_without_the_new_fields_scores_the_old_way(self, published, live_config):
        """The guard, in both copies. A site-only push republishes a history
        file that has no stochastic in it, and it must re-score to what it says.
        """
        rows = json.loads(json.dumps(published["names"]))
        for row in rows:
            assert "stoch_d" not in row["technicals"]
        answer = run_bridge({"config": published["config"], "rows": rows})
        for row, theirs in zip(rows, answer["scored"]):
            assert theirs["score"] == row["score"], row["symbol"]


@pytest.fixture(scope="module")
def both(live_config) -> dict:
    """Every case scored by both implementations, paired up by name."""
    rows = list(PENALTY_CASES.values())
    answer = run_bridge({
        "config": live_config,
        "penalty_rows": [as_published(row) for row in rows],
    })
    mine = [score.penalties(row, live_config["penalties"]) for row in rows]
    return dict(zip(PENALTY_CASES, zip(mine, answer["penalties"])))


class TestPenaltiesAgainstPython:
    """The one place the two are not a transcription.

    score.py parses the earnings and expiry dates; score.js compares them as
    text, because both are YYYY-MM-DD and that sorts correctly without a parser
    or a timezone to get wrong. Worth checking directly, and it also reaches the
    branches no published row happens to carry.
    """

    @pytest.mark.parametrize("case", list(PENALTY_CASES))
    def test_the_two_agree(self, both, case):
        mine, theirs = both[case]
        assert [p["points"] for p in theirs] == [p["points"] for p in mine]
        assert [p["reason"] for p in theirs] == [p["reason"] for p in mine]

    def test_the_cases_reach_every_penalty(self, both):
        """A case set that quietly stopped covering a branch would still pass
        every comparison above, so the last case fires all five at once."""
        mine, theirs = both["everything at once"]
        assert len(mine) == 5
        assert len(theirs) == 5


# Rows that span the three components the buy and long rankings add. The
# fixture predates all of them, so nothing in the published file exercises
# these paths -- and a ramp that runs the other way in JavaScript would move
# the list she is actually shown.
def quarters(*figures):
    return [{"quarter": "2026-%02d-01" % (i + 1), "revenue": float(v)}
            for i, v in enumerate(figures)]


RANKING_CASES = [
    # The chart she asked for: everything stacked, the cross three weeks old.
    {"tech": {"above_ema200": True, "golden_cross": True, "full_stack": True,
              "golden_cross_days_ago": 21, "pct_below_52w_high": 0.30},
     "fund": {"revenue_history": quarters(100, 110, 120, 130, 140),
              "revenue_yoy": 0.40}},
    # Above the 200-day, but the 50 is still under it: a bounce in a downtrend,
    # which is the case that stops trend_structure being one fact four times.
    {"tech": {"above_ema200": True, "golden_cross": False, "full_stack": False,
              "golden_cross_days_ago": None, "pct_below_52w_high": 0.20},
     "fund": {"revenue_history": quarters(100, 90, 95, 92, 99),
              "revenue_yoy": -0.01}},
    # The RBLX shape. room_to_run must read this as no upside at all.
    {"tech": {"above_ema200": False, "golden_cross": False, "full_stack": False,
              "golden_cross_days_ago": None, "pct_below_52w_high": 0.74},
     "fund": {"revenue_history": quarters(100, 101, 99, 98, 97),
              "revenue_yoy": -0.03}},
    # An old cross: golden_cross true, days_ago past the frame, so freshness is
    # withheld without the name being punished for it.
    {"tech": {"above_ema200": True, "golden_cross": True, "full_stack": False,
              "golden_cross_days_ago": None, "pct_below_52w_high": 0.42},
     "fund": {"revenue_history": quarters(100, 105, 104, 112, 118),
              "revenue_yoy": 0.18}},
    # Right at the far ramp's hinge, where the two branches meet.
    {"tech": {"above_ema200": True, "golden_cross": True, "full_stack": True,
              "golden_cross_days_ago": 0, "pct_below_52w_high": 0.35},
     "fund": {"revenue_history": quarters(100, 100, 100, 100, 100),
              "revenue_yoy": 0.0}},
    # No fundamentals at all, and no distance measured: both unknown paths.
    {"tech": {"above_ema200": None, "golden_cross": None, "full_stack": None,
              "golden_cross_days_ago": None, "pct_below_52w_high": None},
     "fund": None},
]


@pytest.fixture(scope="module", params=["buy", "long"])
def ranked(request, published, live_config):
    """Each case scored by both implementations, on one ranking."""
    profile = request.param
    rows = []
    for i, case in enumerate(RANKING_CASES):
        row = json.loads(json.dumps(published["names"][i % len(published["names"])]))
        row["technicals"].update(case["tech"])
        row["fundamentals"] = case["fund"]
        row["symbol"] = f"{row['symbol']}-{profile}-{i}"
        rows.append(row)
    answer = run_bridge({"config": live_config, "rows": rows, "profile": profile})
    mine = [score.score(as_internal(r), live_config, profile) for r in rows]
    return profile, list(zip(rows, mine, answer["scored"]))


class TestTheOtherRankings:
    """Same equality, asked of the two lists that rank what she would own.

    The put's parity is checked against the published file, which is the
    stronger test but can only cover the components that existed when the file
    was written. These rows cover the three that came after."""

    def test_the_cases_actually_span_the_components(self, ranked):
        _, pairs = ranked
        for key in ("trend_structure", "room_to_run", "revenue_expanding"):
            raws = {p[1]["components"][key]["raw"] for p in pairs}
            assert len(raws) >= 4, f"{key} barely moves across these cases"
            assert min(raws) == 0.0 or min(raws) < 0.3
            assert max(raws) > 0.8

    def test_a_collapse_is_not_upside_in_either_implementation(self, ranked):
        """The name her mother flagged, 74% down with the 50 under the 200."""
        _, pairs = ranked
        _, mine, theirs = pairs[2]
        assert mine["components"]["room_to_run"]["raw"] == 0.0
        assert theirs["components"]["room_to_run"]["raw"] == 0.0

    def test_both_agree_on_every_component_and_the_total(self, ranked):
        profile, pairs = ranked
        for row, mine, theirs in pairs:
            assert theirs["score"] == mine["score"], f"{row['symbol']} on {profile}"
            for key, part in mine["components"].items():
                assert theirs["components"][key] == part, f"{row['symbol']} {key}"

    def test_the_weight_block_decides_which_components_are_scored(self, ranked, live_config):
        """A profile is a weight block and nothing more. Scoring the whole
        registry and multiplying the rest by zero would give the same total
        and a breakdown full of empty rows."""
        profile, pairs = ranked
        expected = set(live_config["weights_" + profile])
        for _, mine, theirs in pairs:
            assert set(mine["components"]) == expected
            assert set(theirs["components"]) == expected

    def test_the_seller_pays_for_earnings_and_the_owner_does_not(self, live_config):
        """An expiry is what makes an earnings date expensive. Both copies have
        to agree about that, or a row carries a red flag in one and not the
        other."""
        row = as_published({
            "symbol": "EARN",
            "tech": {"close": 50.0, "change_5d": 0.01},
            "fund": {"next_earnings": "2026-10-15"},
            "trade": {"expiry": "2026-10-16", "strike": 45.0},
        })
        job = {"config": live_config, "penalty_rows": [row]}
        for profile in ("put", "buy", "long"):
            merged = score.penalty_config(live_config, profile)
            mine = score.penalties(as_internal(row), merged, profile)
            theirs = run_bridge({**job, "penalty_profile": profile})["penalties"][0]
            assert [p["reason"] for p in mine] == [p["reason"] for p in theirs]
            assert bool(mine) is (profile == "put")

    def test_the_long_ranking_charges_more_for_a_broken_chart(self, live_config):
        """Published as an override rather than folded in, so the page can show
        what a six-month horizon charges extra for."""
        assert (score.penalty_config(live_config, "long")["downtrend_confirmed"]
                > live_config["penalties"]["downtrend_confirmed"])


# Rows that span the two components only the call ranking scores. Neither
# exists in the fixture: one reads a contract no published file carried until
# now, and the other is the seller's premium reading turned around, which is
# the whole reason the two lists can disagree about the same name.
def contract(spread, oi, dte, expiry="2026-11-20"):
    return {"id": f"{expiry}@45", "expiry": expiry, "dte": dte, "strike": 45.0,
            "spread_pct": spread, "open_interest": oi, "cost": 8.0,
            "breakeven": 53.0}


CALL_CASES = [
    # Rich premium on a deep, tight, long-dated contract: the best contract on
    # the board and the worst vol to pay for it.
    {"iv_hv": 1.60, "iv_percentile": 92, "call": contract(0.01, 5000, 130, "2026-12-18")},
    # The mirror. Cheap vol, and a contract that is barely there.
    {"iv_hv": 0.88, "iv_percentile": 4, "call": contract(0.11, 60, 62, "2026-10-30")},
    {"iv_hv": 1.15, "iv_percentile": 48, "call": contract(0.05, 900, 90)},
    # No implied-vol reading at all. Inverting a zero would hand a name nobody
    # could measure full credit for being cheap, and this is the branch that
    # has to refuse to.
    {"iv_hv": None, "iv_percentile": None, "call": contract(0.03, 1200, 100)},
    # Measured against realized vol but with no percentile yet -- the shape
    # every file carried before three months of history existed.
    {"iv_hv": 1.42, "iv_percentile": None, "call": contract(0.08, 300, 75)},
    # Past the far end of all three contract ramps at once, and under the near
    # end of the vol one.
    {"iv_hv": 0.40, "iv_percentile": 0, "call": contract(0.20, 20000, 200)},
]


@pytest.fixture(scope="module")
def called(published, live_config):
    """Each call case scored by both implementations, on a real row's fields."""
    rows = []
    for i, case in enumerate(CALL_CASES):
        row = json.loads(json.dumps(published["names"][i % len(published["names"])]))
        row["symbol"] = f"{row['symbol']}-call-{i}"
        row["iv_hv"] = case["iv_hv"]
        row["iv_percentile"] = case["iv_percentile"]
        # The published shape: the contract rides inside the ranking block.
        row["call"] = {"contract": case["call"]}
        rows.append(row)
    answer = run_bridge({"config": live_config, "rows": rows, "profile": "call"})
    mine = [score.score(as_internal(r), live_config, "call") for r in rows]
    return list(zip(rows, mine, answer["scored"]))


class TestTheCallRanking:
    """The fourth list, and the first whose components read the contract out of
    the ranking block rather than off the top of the row."""

    def test_the_cases_actually_span_the_components(self, called):
        for key in ("iv_cheapness", "contract_quality"):
            raws = {p[1]["components"][key]["raw"] for p in called}
            assert len(raws) >= 4, f"{key} barely moves across these cases"
            assert min(raws) < 0.3, key
            assert max(raws) > 0.8, key

    def test_both_agree_on_every_component_and_the_total(self, called):
        for row, mine, theirs in called:
            assert theirs["score"] == mine["score"], row["symbol"]
            for key, part in mine["components"].items():
                assert theirs["components"][key] == part, (row["symbol"], key)

    def test_the_weight_block_decides_which_components_are_scored(self, called, live_config):
        expected = set(live_config["weights_call"])
        for _, mine, theirs in called:
            assert set(mine["components"]) == expected
            assert set(theirs["components"]) == expected

    def test_an_unmeasured_name_is_not_called_cheap(self, called):
        """Not rewarded, not treated as a failure -- the 0.4 the fundamentals
        use for the same reason. Both copies have to withhold it alike."""
        _, mine, theirs = called[3]
        assert mine["components"]["iv_cheapness"]["raw"] == 0.4
        assert theirs["components"]["iv_cheapness"]["raw"] == 0.4

    def test_cheap_is_exactly_the_sellers_reading_turned_around(self, called, live_config):
        """The same two numbers read from the other side of the trade. If the
        two ever measured vol differently, the toggle would hide the
        disagreement it exists to show."""
        row = called[0][0]
        job = {"config": live_config, "rows": [row]}
        rich = run_bridge({**job, "profile": "put"})["scored"][0]
        cheap = run_bridge({**job, "profile": "call"})["scored"][0]
        assert cheap["components"]["iv_cheapness"]["raw"] == pytest.approx(
            1 - rich["components"]["premium_richness"]["raw"], abs=1e-4)

    def test_a_second_turn_of_the_slider_scores_the_same_call(self, called, live_config):
        """score.js on its own, not parity. The page swaps in the whole block
        rescore returns, so a block that dropped the contract on the way out
        would score the next nudge of a slider as a name with no call at all.
        """
        row = called[0][0]
        once = run_bridge({"config": live_config, "rows": [row],
                           "profile": "call"})["scored"][0]
        twice = run_bridge({"config": live_config, "rows": [{**row, "call": once}],
                            "profile": "call"})["scored"][0]
        assert twice["components"]["contract_quality"] == once["components"]["contract_quality"]
        assert twice["score"] == once["score"]

    def test_each_ranking_reads_its_own_expiry(self, live_config):
        """A print after the put expires but before the call does. The seller
        is clear and the buyer is not, from one row, in both copies."""
        row = as_published({
            "symbol": "EARN",
            "tech": {"close": 50.0, "change_5d": 0.01},
            "fund": {"next_earnings": "2026-11-10"},
            "trade": {"expiry": "2026-10-16", "strike": 45.0},
            "call": {"contract": {"expiry": "2026-11-20", "strike": 45.0}},
        })
        job = {"config": live_config, "penalty_rows": [row]}
        for profile, charged in (("put", False), ("call", True)):
            merged = score.penalty_config(live_config, profile)
            mine = score.penalties(as_internal(row), merged, profile)
            theirs = run_bridge({**job, "penalty_profile": profile})["penalties"][0]
            assert [p["reason"] for p in mine] == [p["reason"] for p in theirs]
            assert bool(mine) is charged, profile
            if charged:
                assert "2026-11-20 expiry" in mine[0]["reason"]

    def test_the_call_pays_more_for_the_print_than_the_put(self, live_config):
        """A seller who is assigned owns a stock she was willing to own. A call
        is a wasting asset, and the gap goes against it with the clock already
        running."""
        assert (score.penalty_config(live_config, "call")["earnings_before_expiry"]
                > live_config["penalties"]["earnings_before_expiry"])
