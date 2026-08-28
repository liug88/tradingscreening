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
    return merged


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
