from datetime import date

import pytest

from market_intelligence.connectors import bls, cftc, fed, fred
from market_intelligence.errors import ParseError, SourceRefused


def test_fredgraph_reads_real_yields(fixture_text):
    obs = dict(fred.parse_fredgraph(fixture_text("fredgraph_DGS10.csv")))
    assert obs["2026-11-09"] == 4.88
    assert obs["2026-11-05"] == 4.84


def test_fredgraph_rejects_unexpected_format():
    with pytest.raises(ParseError):
        fred.parse_fredgraph("<html>erreur</html>")


def test_alfred_vintage_dates(fixture_text):
    dates = fred.parse_vintage_dates(fixture_text("alfred_vintages_CPIAUCSL.html"))
    assert dates == sorted(dates)
    assert "2026-11-10" in dates and "2026-10-14" in dates
    assert len(dates) > 600


def test_alfred_keeps_each_version_separate(fixture_text):
    by_vintage = fred.parse_alfredgraph(fixture_text("alfredgraph_UNRATE_batch1.csv"), "UNRATE")
    assert dict(by_vintage["2025-12-16"])["2025-11-01"] == 4.6   # premiere publication
    assert dict(by_vintage["2026-01-09"])["2025-11-01"] == 4.5   # revision
    assert "2025-10-01" not in dict(by_vintage["2026-01-09"])    # mois jamais publie : pas de valeur


def test_release_calendar_times_are_converted_from_chicago(fixture_text):
    rows = fred.parse_release_calendar(fixture_text("fred_release_calendar_cpi.html"))
    assert ("2026-12-10", "7:30 am", 10) in rows


def test_fomc_calendar(fixture_text):
    meetings = fed.parse_fomc_calendar(fixture_text("fed_fomc_calendar.html"))
    by_end = {m["end"]: m for m in meetings}
    assert by_end["2026-01-28"]["start"] == "2026-01-27"
    assert "2026-12-09" in by_end
    assert all(m["start"] <= m["end"] for m in meetings)
    # Les reunions a cheval sur deux mois gardent le bon mois de fin.
    assert all((date.fromisoformat(m["end"]) - date.fromisoformat(m["start"])).days <= 2 for m in meetings)


def test_fed_press_uses_published_time_and_skips_undated_entries(fixture_text):
    events = fed.parse_press(fixture_text("fed_press.json"), date(2000, 1, 1))
    statement = next(e for e in events if e.event_key.endswith("monetary20261028a.htm"))
    assert statement.kind == "fomc_statement" and statement.importance == "high"
    assert statement.scheduled_at == "2026-10-28T18:00:00Z"      # 14 h a New York
    assert statement.time_basis == "published"
    assert all(e.scheduled_at >= "2025" for e in events)          # l'entree de 2006 sans heure est ecartee


def test_fed_chair_is_recognised_by_title(fixture_text):
    events = fed.parse_speeches(fixture_text("fed_speeches.json"), date(2000, 1, 1))
    high = [e for e in events if e.importance == "high"]
    assert high and all(e.title.startswith(("Chair ", "Chairman ", "Chairwoman ")) for e in high)
    assert all(e.importance == "medium" for e in events if e.title.startswith("Vice Chair"))


def test_h15(fixture_text):
    parsed = fed.parse_h15(fixture_text("fed_h15.csv"))
    assert dict(parsed["FED_H15_Y10"])["2026-11-09"] == 4.88
    assert dict(parsed["FED_H15_Y2"])["2026-11-09"] == 4.56


def test_bls(fixture_text):
    parsed, messages = bls.parse_bls(fixture_text("bls_v2.json"))
    assert messages == []
    assert dict((m, v) for m, v, _ in parsed["LNS14000000"])["2026-10-01"] == 4.1
    assert dict((m, v) for m, v, _ in parsed["CES0000000001"])["2026-10-01"] == 159293


def test_bls_refusal_is_an_error_not_data(fixture_text):
    with pytest.raises(SourceRefused):
        bls.parse_bls(fixture_text("bls_v2_quota_reached.json"))


def test_cot_gold(fixture_text):
    rows = cftc.parse_cot(fixture_text("cftc_gold_disaggregated.json"))
    last = rows[-1]
    assert last["report_date"] == "2026-11-03"
    assert last["COT_GOLD_MM_NET"] == 122283 - 18669
    assert last["COT_GOLD_OPEN_INTEREST"] == 399169
