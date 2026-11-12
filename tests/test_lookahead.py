"""Tests specifiques contre le look-ahead bias, sur des donnees reelles.

Regle verifiee : rien de ce qui est publie, revise ou annonce apres un instant t
ne doit etre visible par une lecture faite a l'instant t.
"""
from datetime import timedelta

from market_intelligence.context import build_context
from market_intelligence.store import Event
from market_intelligence.timeutil import iso, parse_iso


def _second_before(text: str) -> str:
    return iso(parse_iso(text) - timedelta(seconds=1))


def test_no_row_is_visible_before_its_safe_bound(unrate_store):
    """Balayage exhaustif : pour chaque version stockee, une lecture faite une seconde
    avant sa borne sure ne la contient pas, et une lecture a la borne la contient."""
    rows = unrate_store.db.execute("SELECT * FROM observations WHERE series_id='UNRATE'").fetchall()
    assert len(rows) > 40
    for row in rows:
        key = (row["obs_date"], row["vintage_date"])
        before = {(r["obs_date"], r["vintage_date"])
                  for r in unrate_store.asof("UNRATE", _second_before(row["available_at_safe"]))}
        at = {(r["obs_date"], r["vintage_date"]) for r in unrate_store.asof("UNRATE", row["available_at_safe"])}
        assert key not in before
        assert key in at


def test_asof_never_returns_rows_from_the_future(unrate_store):
    for asof in ("2025-01-15T00:00:00Z", "2025-06-30T12:00:00Z", "2025-11-25T00:00:00Z",
                 "2026-01-09T13:29:00Z", "2026-07-04T00:00:00Z"):
        assert all(r["available_at_safe"] <= asof for r in unrate_store.asof("UNRATE", asof))
        assert all(r["available_at"] <= asof for r in unrate_store.asof("UNRATE", asof, safe=False))


def test_revised_value_is_invisible_on_its_publication_day_by_default(unrate_store):
    """Le 9 janvier 2026, novembre 2025 est revise de 4,6 a 4,5. L'heure (8 h 30) n'etant
    qu'estimee, une lecture prudente garde 4,6 jusqu'a la fin du jour a New York."""
    during_day = "2026-01-09T20:00:00Z"
    safe = {r["obs_date"]: r["value"] for r in unrate_store.asof("UNRATE", during_day)}
    estimate = {r["obs_date"]: r["value"] for r in unrate_store.asof("UNRATE", during_day, safe=False)}
    assert safe["2025-11-01"] == 4.6 and "2025-12-01" not in safe
    assert estimate["2025-11-01"] == 4.5 and estimate["2025-12-01"] == 4.4


def test_estimated_mode_respects_the_estimated_time(unrate_store):
    before = unrate_store.asof("UNRATE", "2025-12-16T13:29:59Z", safe=False)
    after = unrate_store.asof("UNRATE", "2025-12-16T13:30:00Z", safe=False)
    assert "2025-11-01" not in {r["obs_date"] for r in before}
    assert "2025-11-01" in {r["obs_date"] for r in after}


def test_context_on_release_day_does_not_use_the_new_figure(unrate_store):
    ctx = build_context(unrate_store, parse_iso("2025-12-16T15:00:00Z"), historical=True)
    labor = ctx["labor"]["unemployment_rate"]
    assert labor["obs_date"] == "2025-09-01" and labor["value"] == 4.4
    next_day = build_context(unrate_store, parse_iso("2025-12-17T05:00:00Z"), historical=True)
    assert next_day["labor"]["unemployment_rate"]["value"] == 4.6


def test_context_declares_how_reliable_each_timestamp_is(unrate_store):
    labor = build_context(unrate_store, parse_iso("2025-12-17T15:00:00Z"), historical=True)["labor"]["unemployment_rate"]
    assert labor["time_quality"] == "estimated" and labor["release_kind"] == "initial"
    assert labor["available_at_safe"] > labor["available_at"]


def test_year_on_year_uses_both_figures_as_known_then(cpi_store):
    """Glissement annuel du CPI de decembre 2023, calcule avant et apres la revision
    du 9 fevrier 2024 : les deux termes doivent venir de la version connue a la date lue."""
    before = build_context(cpi_store, parse_iso("2024-02-09T04:00:00Z"), historical=True)["inflation"]["cpi_yoy"]
    after = build_context(cpi_store, parse_iso("2024-02-10T06:00:00Z"), historical=True)["inflation"]["cpi_yoy"]
    assert before["obs_date"] == after["obs_date"] == "2023-12-01"
    assert before["vintage_date"] == "2024-01-11" and before["release_kind"] == "initial"
    assert after["vintage_date"] == "2024-02-09" and after["release_kind"] == "revision"
    assert before["value"] != after["value"]


def test_alfred_version_without_new_period_is_a_revision_not_a_release(cpi_store, settings):
    """La version ALFRED du 9 fevrier 2024 ne publie aucun mois nouveau : elle revise
    les mois passes. Elle ne doit pas devenir un evenement 'publication du CPI'."""
    from market_intelligence.connectors.fred import FredConnector
    from datetime import date
    summary = {v["vintage_date"]: (v["n_initial"], v["n_revision"], v["n_backfill"])
               for v in cpi_store.vintages("CPIAUCSL")}
    assert summary["2024-02-09"][0] == 0 and summary["2024-02-09"][1] > 0
    assert summary["2024-01-11"][0] == 1 and summary["2024-02-13"][0] == 1
    assert summary["2023-12-12"] == (0, 0, summary["2023-12-12"][2])       # premiere version chargee
    FredConnector(None, cpi_store, settings).collect_release_history(date(2023, 1, 1))
    events = {e["scheduled_at"][:10]: e for e in cpi_store.events_between("2023-01-01T00:00:00Z", "2025-01-01T00:00:00Z")}
    assert events["2024-01-11"]["kind"] == "data_release" and events["2024-01-11"]["importance"] == "high"
    assert events["2024-02-13"]["kind"] == "data_release"
    assert events["2024-02-09"]["kind"] == "data_revision" and events["2024-02-09"]["importance"] == "low"
    assert "2023-12-12" not in events                                       # nature inconnue : aucun evenement
    assert all(e["time_basis"] == "estimated" for e in events.values())


def test_unannounced_events_are_invisible_before_they_happen(store):
    store.upsert_events([
        Event("s|1", "fed", "fed_speech", "Chairman : discours", "2026-11-10T23:30:00Z", "published", "high",
              advance_notice="unscheduled"),
        Event("m|1", "fed", "fomc_decision", "Decision du FOMC", "2026-12-09T19:00:00Z", "estimated", "high"),
    ], "2026-11-12T09:00:00Z")
    window = ("2026-11-01T00:00:00Z", "2026-12-31T00:00:00Z")
    before = [e["event_key"] for e in store.events_between(*window, asof="2026-11-10T12:00:00Z")]
    after = [e["event_key"] for e in store.events_between(*window, asof="2026-11-11T00:00:00Z")]
    assert before == ["m|1"]                 # la reunion planifiee est connue, le discours non
    assert after == ["s|1", "m|1"]


def test_emergency_fomc_statement_is_not_treated_as_scheduled():
    import json
    from datetime import date

    from market_intelligence.connectors.fed import parse_press
    # Deux vraies entrees du flux de la Fed : reunion ordinaire (14 h) et decision
    # d'urgence du dimanche 15 mars 2020 (17 h).
    feed = json.dumps([
        {"d": "10/28/2026 2:00:00 PM", "t": "Federal Reserve issues FOMC statement",
         "pt": "Monetary Policy", "l": "/newsevents/pressreleases/monetary20261028a.htm"},
        {"d": "3/15/2020 5:00:00 PM", "t": "Federal Reserve issues FOMC statement",
         "pt": "Monetary Policy", "l": "/newsevents/pressreleases/monetary20200315a.htm"}])
    by_day = {e.scheduled_at[:10]: e.advance_notice for e in parse_press(feed, date(2000, 1, 1))}
    assert by_day == {"2026-10-28": "scheduled", "2020-03-15": "unscheduled"}
