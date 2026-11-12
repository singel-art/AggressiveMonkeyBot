"""Protection contre le look-ahead, verifiee sur de vraies revisions."""
from market_intelligence.store import Event


def test_asof_returns_first_release_before_the_revision(unrate_store):
    rows = unrate_store.asof("UNRATE", "2025-12-17T15:00:00Z")
    assert rows[-1]["obs_date"] == "2025-11-01" and rows[-1]["value"] == 4.6


def test_asof_returns_revised_value_after_the_revision(unrate_store):
    rows = {r["obs_date"]: r["value"] for r in unrate_store.asof("UNRATE", "2026-01-10T00:00:00Z")}
    assert rows["2025-11-01"] == 4.5 and rows["2025-12-01"] == 4.4


def test_release_is_invisible_one_second_before_publication(unrate_store):
    before = unrate_store.asof("UNRATE", "2025-12-16T13:29:59Z")
    after = unrate_store.asof("UNRATE", "2025-12-16T13:30:00Z")
    assert "2025-11-01" not in {r["obs_date"] for r in before}
    assert "2025-11-01" in {r["obs_date"] for r in after}


def test_asof_never_returns_future_information(unrate_store):
    for asof in ("2025-01-15T00:00:00Z", "2025-06-30T12:00:00Z", "2025-11-25T00:00:00Z", "2026-01-09T13:29:00Z"):
        assert all(r["available_at"] <= asof for r in unrate_store.asof("UNRATE", asof))


def test_first_releases_and_revision_history(unrate_store):
    first = {r["obs_date"]: r["value"] for r in unrate_store.first_releases("UNRATE")}
    assert first["2025-11-01"] == 4.6
    history = [(r["vintage_date"], r["value"]) for r in unrate_store.revisions("UNRATE", "2025-11-01")]
    assert history == [("2025-12-16", 4.6), ("2026-01-09", 4.5)]


def test_stored_versions_are_immutable(unrate_store):
    from market_intelligence.store import Observation
    added = unrate_store.add_observations([Observation(
        "UNRATE", "2025-11-01", "2025-12-16", 9.9, "2025-12-16T13:30:00Z", "vintage", "alfred")])
    assert added == 0
    assert unrate_store.asof("UNRATE", "2025-12-17T00:00:00Z")[-1]["value"] == 4.6


def test_event_result_is_hidden_until_scheduled_time(store):
    # Valeur reelle : emploi salarie d'octobre 2026 publie par le BLS le 6 novembre 2026.
    store.upsert_events([Event("t|nfp", "bls", "data_release", "Employment Situation",
                               "2026-11-06T13:30:00Z", "convention", "high", actual="159293")],
                        "2026-11-12T09:00:00Z")
    hidden = store.events_between("2026-11-01T00:00:00Z", "2026-11-30T00:00:00Z", asof="2026-11-06T13:00:00Z")
    shown = store.events_between("2026-11-01T00:00:00Z", "2026-11-30T00:00:00Z", asof="2026-11-06T14:00:00Z")
    assert hidden[0]["actual"] is None and shown[0]["actual"] == "159293"


def test_first_seen_is_never_overwritten(store):
    e = Event("t|x", "fed", "fomc_decision", "Decision du FOMC", "2026-12-09T19:00:00Z", "convention", "high")
    store.upsert_events([e], "2026-11-12T09:00:00Z")
    store.upsert_events([e], "2026-11-20T09:00:00Z")
    row = store.db.execute("SELECT first_seen_at, updated_at FROM events").fetchone()
    assert row["first_seen_at"] == "2026-11-12T09:00:00Z" and row["updated_at"] == "2026-11-20T09:00:00Z"
