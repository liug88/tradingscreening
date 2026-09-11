"""What the company does, and what it earned, out of a quoteSummary payload.

Three modules ride in one request, and the parsing is the part that can go
quietly wrong: a description that stops at "Inc." publishes a name and nothing
else, a cache entry written before these fields existed would keep its old
shape for a quarter, and Yahoo wraps every number as {raw, fmt}.
"""

from datetime import date, datetime, timezone

import pytest

from screener import fundamentals as f
from screener.yahoo import YahooError


def quote_summary(**modules):
    """A quoteSummary response carrying the given modules."""
    return {"quoteSummary": {"result": [modules], "error": None}}


def stamp(iso):
    """Yahoo's shape for a date: seconds since the epoch, in a raw/fmt pair."""
    when = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
    return {"raw": int(when.timestamp()), "fmt": iso}


INTEL = ("Intel Corporation designs, develops, manufactures, markets, and sells "
         "computing and related products and services worldwide. It operates "
         "through Client Computing Group and Data Center and AI segments.")

MICRON = ("Micron Technology, Inc. designs, develops, manufactures, and sells "
          "memory and storage products worldwide. The company operates through four "
          "segments.")

ASML = ("ASML Holding N.V. develops, produces, markets, sells, and services advanced "
        "semiconductor equipment systems. It offers lithography systems.")


class TestFirstSentence:
    def test_stops_at_the_first_full_stop(self):
        assert f._first_sentence(INTEL) == (
            "Intel Corporation designs, develops, manufactures, markets, and sells "
            "computing and related products and services worldwide.")

    def test_a_legal_suffix_does_not_end_the_sentence(self):
        """The shape that would otherwise publish "Micron Technology, Inc." as
        the whole description of the company."""
        assert f._first_sentence(MICRON).endswith("storage products worldwide.")

    def test_a_dotted_name_does_not_end_the_sentence(self):
        assert f._first_sentence(ASML).endswith("semiconductor equipment systems.")

    def test_a_single_letter_does_not_end_the_sentence(self):
        text = "The A. B. Widget Company makes widgets. It also sells them."
        assert f._first_sentence(text) == "The A. B. Widget Company makes widgets."

    def test_a_long_sentence_is_cut_at_a_word(self):
        text = "Acme " + "makes things and " * 30 + "more."
        got = f._first_sentence(text)
        assert len(got) <= f.DESCRIPTION_CHARS + 1
        assert got.endswith("…")
        assert not got[:-1].endswith(" ")

    @pytest.mark.parametrize("text", [None, "", "   "])
    def test_nothing_in_gives_none(self, text):
        assert f._first_sentence(text) is None


class TestLastEarnings:
    def test_takes_the_most_recent_quarter(self):
        payload = quote_summary(earningsHistory={"history": [
            {"quarter": stamp("2026-03-31"), "epsActual": {"raw": 0.13}, "epsEstimate": {"raw": 0.01}},
            {"quarter": stamp("2026-06-30"), "epsActual": {"raw": 0.42}, "epsEstimate": {"raw": 0.21584}},
            {"quarter": stamp("2025-12-31"), "epsActual": {"raw": -0.03}, "epsEstimate": {"raw": 0.02}},
        ]})
        assert f._last_earnings(payload) == {
            "quarter": "2026-06-30", "eps_actual": 0.42, "eps_estimate": 0.21584}

    def test_a_missing_estimate_is_none_not_a_skip(self):
        payload = quote_summary(earningsHistory={"history": [
            {"quarter": stamp("2026-06-30"), "epsActual": {"raw": 0.42}, "epsEstimate": {}},
        ]})
        assert f._last_earnings(payload)["eps_estimate"] is None

    def test_a_quarter_with_no_actual_is_skipped(self):
        """Yahoo lists the quarter in flight with an empty actual."""
        payload = quote_summary(earningsHistory={"history": [
            {"quarter": stamp("2026-06-30"), "epsActual": {"raw": 0.42}, "epsEstimate": {"raw": 0.2}},
            {"quarter": stamp("2026-09-30"), "epsActual": {}, "epsEstimate": {"raw": 0.3}},
        ]})
        assert f._last_earnings(payload)["quarter"] == "2026-06-30"

    @pytest.mark.parametrize("payload", [
        {}, quote_summary(), quote_summary(earningsHistory={}),
        quote_summary(earningsHistory={"history": []}),
        quote_summary(earningsHistory={"history": ["junk", None]}),
    ])
    def test_nothing_in_gives_none(self, payload):
        assert f._last_earnings(payload) is None


class TestEarningsDate:
    def test_takes_the_earlier_edge_of_a_range(self):
        payload = quote_summary(calendarEvents={"earnings": {
            "earningsDate": [stamp("2026-10-22"), stamp("2026-10-26")]}})
        assert f._earnings_date(payload) == "2026-10-22"

    def test_none_when_nothing_is_scheduled(self):
        assert f._earnings_date(quote_summary(calendarEvents={"earnings": {}})) is None


class Session:
    """The two calls fetch() makes, canned."""

    def __init__(self, summary=None, fail=False):
        self.summary, self.fail = summary, fail
        self.modules = None

    def fundamentals(self, symbol, metrics, since):
        return {"timeseries": {"result": [
            {"quarterlyTotalRevenue": [
                {"asOfDate": "2026-03-31", "reportedValue": {"raw": 100.0}},
                {"asOfDate": "2026-06-30", "reportedValue": {"raw": 125.0}},
            ]},
        ]}}

    def quote_summary(self, symbol, modules):
        self.modules = list(modules)
        if self.fail:
            raise YahooError("no crumb")
        return self.summary


class TestFetch:
    SUMMARY = quote_summary(
        assetProfile={"sector": "Technology", "industry": "Semiconductors",
                      "longBusinessSummary": INTEL},
        earningsHistory={"history": [
            {"quarter": stamp("2026-06-30"), "epsActual": {"raw": 0.42}, "epsEstimate": {"raw": 0.22}},
        ]},
        calendarEvents={"earnings": {"earningsDate": [stamp("2026-10-22")]}},
    )

    def test_asks_for_all_three_modules_in_one_request(self):
        session = Session(self.SUMMARY)
        f.fetch("INTC", session)
        assert set(session.modules) == {"calendarEvents", "assetProfile", "earningsHistory"}

    def test_carries_what_the_company_is(self):
        got = f.fetch("INTC", Session(self.SUMMARY))
        assert got["sector"] == "Technology"
        assert got["industry"] == "Semiconductors"
        assert got["description"].startswith("Intel Corporation designs")
        assert got["description"].endswith("worldwide.")

    def test_carries_both_earnings(self):
        got = f.fetch("INTC", Session(self.SUMMARY))
        assert got["next_earnings"] == "2026-10-22"
        assert got["last_earnings"] == {"quarter": "2026-06-30", "eps_actual": 0.42, "eps_estimate": 0.22}

    def test_a_failed_summary_costs_those_fields_and_nothing_else(self):
        """The revenue half is the half the score reads. It does not wait on
        the profile."""
        got = f.fetch("INTC", Session(fail=True))
        assert got["revenue_qoq"] == pytest.approx(0.25)
        assert got["industry"] is None and got["description"] is None
        assert got["last_earnings"] is None and got["next_earnings"] is None
        assert "industry" in got, "the key is what marks the entry as current"


class TestIsStale:
    TODAY = date(2026, 9, 11)

    def test_an_entry_from_before_the_profile_is_stale(self):
        """No version stamp on the cache, so the key itself is the stamp. This
        is what backfills every entry on the next run, instead of over a
        quarter of earnings dates."""
        assert f._is_stale({"next_earnings": "2026-10-22"}, self.TODAY)

    def test_a_current_entry_holds_until_it_reports(self):
        assert not f._is_stale({"industry": None, "next_earnings": "2026-10-22"}, self.TODAY)

    def test_stale_once_the_company_has_reported(self):
        assert f._is_stale({"industry": "Semiconductors", "next_earnings": "2026-09-10"}, self.TODAY)

    def test_no_date_at_all_leaves_it_to_the_age_backstop(self):
        assert not f._is_stale({"industry": "Semiconductors", "next_earnings": None}, self.TODAY)
