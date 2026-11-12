"""Stockage point-in-time, verifie sur de vraies revisions (taux de chomage)."""
from market_intelligence.store import Event, Observation


def test_asof_returns_first_release_before_the_revision(unrate_store):
    rows = unrate_store.asof("UNRATE", "2025-12-17T15:00:00Z")
    assert rows[-1]["obs_date"] == "2025-11-01" and rows[-1]["value"] == 4.6
    assert rows[-1]["release_kind"] == "initial"


def test_asof_returns_revised_value_after_the_revision(unrate_store):
    rows = {r["obs_date"]: r for r in unrate_store.asof("UNRATE", "2026-01-10T06:00:00Z")}
    assert rows["2025-11-01"]["value"] == 4.5 and rows["2025-11-01"]["release_kind"] == "revision"
    assert rows["2025-12-01"]["value"] == 4.4 and rows["2025-12-01"]["release_kind"] == "initial"


def test_revision_history_keeps_initial_value_and_each_revision(unrate_store):
    history = [(r["vintage_date"], r["value"], r["release_kind"])
               for r in unrate_store.revisions("UNRATE", "2025-11-01")]
    assert history[:2] == [("2025-12-16", 4.6, "initial"), ("2026-01-09", 4.5, "revision")]


def test_first_releases_only_contains_established_initial_values(unrate_store):
    first = {r["obs_date"]: r["value"] for r in unrate_store.first_releases("UNRATE")}
    assert first["2025-11-01"] == 4.6            # et non la valeur revisee 4.5
    # Les periodes deja publiees lors de la premiere version chargee (2025-01-10) ont une
    # valeur initiale inconnue : elles ne sont pas presentees comme premieres publications.
    assert "2024-06-01" not in first
    backfill = unrate_store.db.execute(
        "SELECT DISTINCT vintage_date FROM observations WHERE release_kind='backfill'").fetchall()
    assert [r[0] for r in backfill] == ["2025-01-10"]


def test_period_reference_is_distinct_from_version_and_availability(unrate_store):
    row = unrate_store.revisions("UNRATE", "2025-11-01")[0]
    assert row["obs_date"] == "2025-11-01"                  # periode mesuree
    assert row["vintage_date"] == "2025-12-16"              # version dans la source
    assert row["available_at"] == "2025-12-16T13:30:00Z"    # heure estimee (8 h 30 a New York)
    assert row["available_at_balanced"] == "2025-12-16T13:45:00Z"  # politique du projet : + 15 minutes
    assert row["available_at_strict"] == "2025-12-17T05:00:00Z"    # fin du jour a New York
    assert row["time_quality"] == "estimated" and row["available_basis"] == "vintage"


def test_stored_versions_are_immutable(unrate_store):
    added = unrate_store.add_observations([Observation(
        "UNRATE", "2025-11-01", "2025-12-16", 9.9, "2025-12-16T13:30:00Z", "vintage", "alfred")])
    assert added == 0
    assert unrate_store.asof("UNRATE", "2025-12-17T06:00:00Z")[-1]["value"] == 4.6


def test_safe_bound_cannot_precede_the_estimate(store):
    import sqlite3

    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        store.add_observations([Observation("X", "2026-01-01", "2026-02-01", 1.0, "2026-02-01T13:30:00Z",
                                            "vintage", "alfred", available_at_balanced="2026-02-01T00:00:00Z")])


def test_event_result_is_hidden_until_scheduled_time(store):
    # Valeur reelle : emploi salarie d'octobre 2026 publie par le BLS le 6 novembre 2026.
    store.upsert_events([Event("t|nfp", "bls", "data_release", "Employment Situation",
                               "2026-11-06T13:30:00Z", "estimated", "high", actual="159293")],
                        "2026-11-12T09:00:00Z")
    hidden = store.events_between("2026-11-01T00:00:00Z", "2026-11-30T00:00:00Z", asof="2026-11-06T13:00:00Z")
    shown = store.events_between("2026-11-01T00:00:00Z", "2026-11-30T00:00:00Z", asof="2026-11-06T14:00:00Z")
    assert hidden[0]["actual"] is None and shown[0]["actual"] == "159293"


def test_first_seen_is_never_overwritten(store):
    e = Event("t|x", "fed", "fomc_decision", "Decision du FOMC", "2026-12-09T19:00:00Z", "estimated", "high")
    store.upsert_events([e], "2026-11-12T09:00:00Z")
    store.upsert_events([e], "2026-11-20T09:00:00Z")
    row = store.db.execute("SELECT first_seen_at, updated_at FROM events").fetchone()
    assert row["first_seen_at"] == "2026-11-12T09:00:00Z" and row["updated_at"] == "2026-11-20T09:00:00Z"


def test_published_time_is_not_replaced_by_an_estimate(store):
    key = "t|same-event"
    store.upsert_events([Event(key, "mt5_calendar", "data_release", "Nonfarm Payrolls",
                               "2026-11-06T13:30:00Z", "published", "high")], "2026-11-12T09:00:00Z")
    store.upsert_events([Event(key, "alfred", "data_release", "Employment Situation",
                               "2026-11-06T12:30:00Z", "estimated", "high")], "2026-11-12T10:00:00Z")
    row = store.db.execute("SELECT scheduled_at, time_basis FROM events").fetchone()
    assert tuple(row) == ("2026-11-06T13:30:00Z", "published")


def test_old_database_schema_is_refused(tmp_path):
    import sqlite3

    import pytest

    from market_intelligence.store import SchemaMismatch, Store
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE observations (series_id TEXT)")   # base d'avant le schema 2
    db.commit()
    db.close()
    with pytest.raises(SchemaMismatch):
        Store(path)
