"""Methodes `collect` des connecteurs, rejouees hors reseau sur des reponses
reelles enregistrees le 2026-11-12."""
from datetime import date

import pytest

from conftest import FakeHttp
from market_intelligence import catalog
from market_intelligence.connectors import fred as fred_module
from market_intelligence.connectors.cftc import CftcConnector
from market_intelligence.connectors.fed import FedConnector
from market_intelligence.connectors.fred import FredConnector
from market_intelligence.connectors.wgc import WgcConnector

FRED_ROUTES = [
    ("/fredgraph.csv", "fredgraph_DGS10.csv"),
    ("downloaddata?seid=UNRATE", "alfred_vintages_UNRATE.html"),
    ("vintage_date=2025-01-10", "alfredgraph_UNRATE_batch1.csv"),
    ("vintage_date=2026-02-11", "alfredgraph_UNRATE_batch2.csv"),
    ("releases/calendar", "fred_release_calendar_employment.html"),
]


@pytest.fixture
def small_fred(monkeypatch):
    """Reduit le catalogue a une serie quotidienne et une serie revisee."""
    series = tuple(s for s in catalog.FRED_SERIES if s.series_id in ("DGS10", "UNRATE"))
    monkeypatch.setattr(fred_module, "FRED_SERIES", series)
    monkeypatch.setattr(fred_module, "RELEASE_SERIES", {50: "UNRATE"})
    monkeypatch.setattr(fred_module, "FRED_RELEASES", {50: catalog.FRED_RELEASES[50]})


def test_fred_collect_backfill_uses_rules_and_never_claims_initial(store, settings, small_fred):
    http = FakeHttp(store, FRED_ROUTES, now="2026-11-12T09:00:00Z")
    report = FredConnector(http, store, settings).collect(date(2025, 1, 1))
    assert report.ok, report.errors
    rows = {r["obs_date"]: r for r in store.asof("DGS10", "2026-11-12T09:00:00Z")}
    old = rows["2026-10-26"]                 # vu 17 jours apres : historique charge apres coup
    assert old["available_basis"] == "rule" and old["time_quality"] == "estimated"
    assert old["available_at"] == "2026-10-27T22:00:00Z"            # jour ouvre suivant, 18 h New York
    assert old["release_kind"] == "backfill"
    recent = rows["2026-11-09"]              # vu peu apres sa date theorique : instant observe
    assert recent["available_basis"] == "observed" and recent["available_at"] == "2026-11-12T09:00:00Z"
    assert recent["release_kind"] == "backfill"                     # premier chargement de la serie


def test_fred_collect_is_idempotent_and_detects_live_releases_and_revisions(store, settings, small_fred, tmp_path):
    connector = FredConnector(FakeHttp(store, FRED_ROUTES, now="2026-11-06T22:30:00Z"), store, settings)
    connector.collect_market(catalog.SERIES_BY_ID["DGS10"], date(2026, 10, 26))
    before = len(store.asof("DGS10", "2099-01-01T00:00:00Z"))
    assert connector.collect_market(catalog.SERIES_BY_ID["DGS10"], date(2026, 10, 26)) == 0
    # Meme source le lendemain, avec une valeur modifiee et un jour de plus (fichier derive
    # de la reponse reelle, uniquement pour declencher les deux chemins).
    import conftest
    text = (conftest.FIXTURES / "fredgraph_DGS10.csv").read_text()
    changed = tmp_path / "changed.csv"
    changed.write_text(text.replace("2026-11-05,4.84", "2026-11-05,4.85") + "2026-11-10,4.90\n")
    http = FakeHttp(store, [("/fredgraph.csv", str(changed))], now="2026-11-11T22:30:00Z")
    conftest_fixtures = conftest.FIXTURES
    assert (conftest_fixtures / str(changed)).exists()              # chemin absolu accepte
    FredConnector(http, store, settings).collect_market(catalog.SERIES_BY_ID["DGS10"], date(2026, 10, 26))
    history = store.revisions("DGS10", "2026-11-05")
    assert [(r["value"], r["release_kind"]) for r in history] == [(4.84, "backfill"), (4.85, "revision")]
    assert history[1]["available_at"] == "2026-11-11T22:30:00Z"     # connue seulement a la detection
    new = store.revisions("DGS10", "2026-11-10")[0]
    assert new["release_kind"] == "initial" and new["available_basis"] == "observed"
    assert len(store.asof("DGS10", "2099-01-01T00:00:00Z")) == before + 1
    # La revision n'est pas visible avant sa detection.
    assert store.asof("DGS10", "2026-11-11T22:29:59Z")[-1]["obs_date"] == "2026-11-09"
    assert {r["obs_date"]: r["value"] for r in store.asof("DGS10", "2026-11-11T22:29:59Z")}["2026-11-05"] == 4.84


def test_fred_collect_macro_classifies_versions_and_builds_events(store, settings, small_fred):
    http = FakeHttp(store, FRED_ROUTES)
    connector = FredConnector(http, store, settings)
    report = connector.collect(date(2025, 1, 1))
    assert report.ok, report.errors
    kinds = dict(store.db.execute(
        "SELECT release_kind, COUNT(*) FROM observations WHERE series_id='UNRATE' GROUP BY 1").fetchall())
    assert kinds["backfill"] > 0 and kinds["initial"] >= 20 and kinds["revision"] > 0
    assert [(r["value"], r["release_kind"]) for r in store.revisions("UNRATE", "2025-11-01")][:2] == \
        [(4.6, "initial"), (4.5, "revision")]
    # Deux lots ALFRED demandes, chacun de 12 versions au plus.
    batches = [c for c in http.calls if "alfredgraph" in c["url"]]
    assert len(batches) == 2 and all(c["url"].count("UNRATE,") <= 11 for c in batches)
    events = store.events_between("2025-01-01T00:00:00Z", "2027-12-31T00:00:00Z")
    past = [e for e in events if e["source"] == "alfred"]
    assert past and all(e["time_basis"] == "estimated" for e in past)
    assert "2025-01-10" not in {e["scheduled_at"][:10] for e in past}     # premiere version : nature inconnue
    future = [e for e in events if e["scheduled_at"] > "2026-11-12"]
    assert future and all(e["kind"] == "data_release" and e["time_basis"] == "estimated" for e in future)
    # Second passage : aucune nouvelle version a telecharger, aucune ligne ajoutee.
    http2 = FakeHttp(store, FRED_ROUTES)
    assert FredConnector(http2, store, settings).collect(date(2025, 1, 1)).rows_added == 0
    assert not [c for c in http2.calls if "alfredgraph" in c["url"]]


def test_fred_collect_reports_a_failing_series_and_continues(store, settings, small_fred):
    routes = [("/fredgraph.csv", None)] + FRED_ROUTES[1:]
    report = FredConnector(FakeHttp(store, routes), store, settings).collect(date(2025, 1, 1))
    assert len(report.errors) == 1 and "DGS10" in report.errors[0]
    assert store.asof("DGS10", "2099-01-01T00:00:00Z") == []        # rien d'invente pour la serie en panne
    assert store.asof("UNRATE", "2099-01-01T00:00:00Z")             # l'autre serie est collectee
    assert store.last_fetch("fred") is not None


FED_ROUTES = [("fomccalendars", "fed_fomc_calendar.html"), ("ne-press.json", "fed_press.json"),
              ("ne-speeches.json", "fed_speeches.json"), ("Output.aspx", "fed_h15.csv")]


def test_fed_collect_events_and_yields(store, settings):
    report = FedConnector(FakeHttp(store, FED_ROUTES), store, settings).collect(date(2025, 1, 1))
    assert report.ok, report.errors
    by_kind = dict(store.db.execute("SELECT kind, COUNT(*) FROM events GROUP BY 1").fetchall())
    assert by_kind["fomc_decision"] >= 20 and by_kind["fomc_statement"] >= 10 and by_kind["fed_speech"] == 40
    decision = store.db.execute("SELECT * FROM events WHERE event_key='fomc_meeting|2026-12-09'").fetchone()
    assert decision["time_basis"] == "estimated" and decision["scheduled_at"] == "2026-12-09T19:00:00Z"
    statement = store.db.execute("SELECT * FROM events WHERE kind='fomc_statement' "
                                 "AND scheduled_at LIKE '2026-10-28%'").fetchone()
    assert statement["time_basis"] == "published" and statement["advance_notice"] == "scheduled"
    speeches = store.db.execute("SELECT DISTINCT advance_notice FROM events WHERE kind='fed_speech'").fetchall()
    assert [r[0] for r in speeches] == ["unscheduled"]
    y10 = store.asof("FED_H15_Y10", "2026-11-12T09:00:00Z")
    assert (y10[-1]["obs_date"], y10[-1]["value"]) == ("2026-11-09", 4.88)


def test_fed_collect_survives_one_failing_feed(store, settings):
    routes = [("fomccalendars", "fed_fomc_calendar.html"), ("ne-press.json", None),
              ("ne-speeches.json", "fed_speeches.json"), ("Output.aspx", "fed_h15.csv")]
    report = FedConnector(FakeHttp(store, routes), store, settings).collect(date(2025, 1, 1))
    assert len(report.errors) == 1 and "collect_press" in report.errors[0]
    assert report.events_upserted > 0 and report.rows_added > 0


def test_cftc_collect_availability_follows_the_friday_release(store, settings):
    http = FakeHttp(store, [("publicreporting.cftc.gov", "cftc_gold_since_2026-09.json")],
                    now="2026-11-12T09:00:00Z")
    report = CftcConnector(http, store, settings).collect(date(2026, 9, 1))
    assert report.ok and report.rows_added == 40                    # 10 rapports x 4 series
    old = store.revisions("COT_GOLD_MM_NET", "2026-09-01")[0]       # positions du mardi 1er septembre
    assert old["available_basis"] == "rule" and old["available_at"] == "2026-09-05T19:30:00Z"
    # Les positions du mardi ne sont pas visibles le jeudi suivant.
    assert store.asof("COT_GOLD_MM_NET", "2026-09-03T12:00:00Z") == []
    last = store.asof("COT_GOLD_MM_NET", "2026-11-12T09:00:00Z")[-1]
    assert (last["obs_date"], last["value"]) == ("2026-11-03", 103614.0)
    assert CftcConnector(http, store, settings).collect(date(2026, 9, 1)).rows_added == 0


def test_cftc_collect_reports_failure_without_data(store, settings):
    report = CftcConnector(FakeHttp(store, [("cftc", None)]), store, settings).collect(date(2026, 9, 1))
    assert not report.ok and store.series_summary() == []


def test_wgc_collect_archives_dropped_files_untouched(store, settings):
    inbox = settings.inbox_dir / "wgc"
    inbox.mkdir(parents=True)
    payload = b"contenu de test, pas un fichier du World Gold Council"
    (inbox / "exemple.xlsx").write_bytes(payload)
    report = WgcConnector(None, store, settings).collect(date(2025, 1, 1))
    assert report.ok and report.notes == ["archive : exemple.xlsx"]
    archived = list((settings.raw_dir / "wgc").rglob("*exemple.xlsx"))
    assert len(archived) == 1 and archived[0].read_bytes() == payload
    assert not list(inbox.iterdir())
    import hashlib
    assert store.last_fetch("wgc")["sha256"] == hashlib.sha256(payload).hexdigest()
    assert store.series_summary() == []                              # aucune valeur n'est extraite
