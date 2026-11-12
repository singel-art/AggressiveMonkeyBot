"""Tests specifiques contre le look-ahead bias, sur des donnees reelles.

Regle verifiee : rien de ce qui est publie, revise ou annonce apres un instant t
ne doit etre visible par une lecture faite a l'instant t.
"""
from datetime import timedelta

import pytest

from market_intelligence.context import build_context
from market_intelligence.store import Event
from market_intelligence.timeutil import iso, parse_iso


def _second_before(text: str) -> str:
    return iso(parse_iso(text) - timedelta(seconds=1))


POLICY_COLUMNS = {"balanced": "available_at_balanced", "strict": "available_at_strict",
                  "estimate": "available_at"}


@pytest.mark.parametrize("policy", ["balanced", "strict", "estimate"])
def test_no_row_is_visible_before_its_availability(unrate_store, policy):
    """Balayage exhaustif, pour chaque politique : une lecture faite une seconde avant
    l'instant de visibilite d'une version ne la contient pas ; a cet instant, elle la contient."""
    col = POLICY_COLUMNS[policy]
    rows = unrate_store.db.execute("SELECT * FROM observations WHERE series_id='UNRATE'").fetchall()
    assert len(rows) > 40
    for row in rows:
        key = (row["obs_date"], row["vintage_date"])
        before = {(r["obs_date"], r["vintage_date"])
                  for r in unrate_store.asof("UNRATE", _second_before(row[col]), policy=policy)}
        at = {(r["obs_date"], r["vintage_date"]) for r in unrate_store.asof("UNRATE", row[col], policy=policy)}
        assert key not in before
        assert key in at


@pytest.mark.parametrize("policy", ["balanced", "strict", "estimate"])
def test_asof_never_returns_rows_from_the_future(unrate_store, policy):
    for asof in ("2025-01-15T00:00:00Z", "2025-06-30T12:00:00Z", "2025-11-25T00:00:00Z",
                 "2026-01-09T13:29:00Z", "2026-01-09T13:40:00Z", "2026-07-04T00:00:00Z"):
        assert all(r[POLICY_COLUMNS[policy]] <= asof for r in unrate_store.asof("UNRATE", asof, policy=policy))


def test_policies_are_ordered_from_earliest_to_latest(unrate_store):
    rows = unrate_store.db.execute("SELECT * FROM observations").fetchall()
    assert all(r["available_at"] <= r["available_at_balanced"] <= r["available_at_strict"] for r in rows)


def test_balanced_policy_applies_the_15_minute_margin(unrate_store):
    """Decision du projet. Le 9 janvier 2026, novembre 2025 est revise de 4,6 a 4,5, a une
    heure estimee de 8 h 30 a New York (13:30 UTC). La revision devient visible a 13:45 UTC."""
    def november(asof, policy="balanced"):
        return {r["obs_date"]: r["value"] for r in unrate_store.asof("UNRATE", asof, policy=policy)}["2025-11-01"]
    assert november("2026-01-09T13:44:59Z") == 4.6
    assert november("2026-01-09T13:45:00Z") == 4.5
    assert unrate_store.asof("UNRATE", "2026-01-09T13:45:00Z") == \
        unrate_store.asof("UNRATE", "2026-01-09T13:45:00Z", policy="balanced")      # balanced est le defaut


def test_strict_policy_waits_for_the_end_of_the_publication_day(unrate_store):
    def november(asof, policy):
        return {r["obs_date"]: r["value"] for r in unrate_store.asof("UNRATE", asof, policy=policy)}["2025-11-01"]
    assert november("2026-01-09T20:00:00Z", "strict") == 4.6
    assert november("2026-01-10T05:00:00Z", "strict") == 4.5
    assert november("2026-01-09T13:30:00Z", "estimate") == 4.5
    assert november("2026-01-09T13:29:59Z", "estimate") == 4.6


def test_unknown_policy_is_refused(unrate_store):
    with pytest.raises(ValueError, match="politique temporelle inconnue"):
        unrate_store.asof("UNRATE", "2026-01-09T13:45:00Z", policy="optimiste")


def test_context_respects_the_margin_on_release_day(unrate_store):
    """Le 16 decembre 2025, le chomage de novembre parait (heure estimee 13:30 UTC)."""
    def rate(asof, policy="balanced"):
        labor = build_context(unrate_store, parse_iso(asof), historical=True, policy=policy)["labor"]["unemployment_rate"]
        return labor["obs_date"], labor["value"]
    assert rate("2025-12-16T13:40:00Z") == ("2025-09-01", 4.4)        # dans la marge : ancien chiffre
    assert rate("2025-12-16T13:45:00Z") == ("2025-11-01", 4.6)
    assert rate("2025-12-16T20:00:00Z", "strict") == ("2025-09-01", 4.4)
    assert rate("2025-12-17T05:00:00Z", "strict") == ("2025-11-01", 4.6)


def test_context_declares_policy_and_how_reliable_each_timestamp_is(unrate_store):
    ctx = build_context(unrate_store, parse_iso("2025-12-17T15:00:00Z"), historical=True)
    labor = ctx["labor"]["unemployment_rate"]
    assert ctx["temporal_policy"] == "balanced"
    assert labor["time_quality"] == "estimated" and labor["release_kind"] == "initial"
    assert labor["available_at"] < labor["available_at_balanced"] < labor["available_at_strict"]


def test_observed_instants_get_no_margin(store):
    from market_intelligence.store import Observation
    store.add_observations([Observation("X", "2026-11-10", "2026-11-12", 1.0, "2026-11-12T09:00:00Z",
                                        "observed", "fred", time_quality="observed")])
    row = store.db.execute("SELECT * FROM observations").fetchone()
    assert row["available_at"] == row["available_at_balanced"] == row["available_at_strict"]


def test_year_on_year_uses_both_figures_as_known_then(cpi_store):
    """Glissement annuel du CPI de decembre 2023, calcule avant et apres la revision
    du 9 fevrier 2024 : les deux termes doivent venir de la version connue a la date lue."""
    before = build_context(cpi_store, parse_iso("2024-02-09T04:00:00Z"), historical=True, policy="strict")["inflation"]["cpi_yoy"]
    after = build_context(cpi_store, parse_iso("2024-02-10T06:00:00Z"), historical=True, policy="strict")["inflation"]["cpi_yoy"]
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
