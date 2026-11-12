"""Heures reelles de publication tirees du calendrier MT5, jours feries et doublons.

Les lignes de calendrier sont ecrites a la main au format de l'export, avec les
identifiants et les horaires constates dans l'export reel du 2026-11-12 (non
versionne) : chomage de novembre 2025 publie le 16 decembre 2025 a 13:30 UTC ;
PCE publie le 5 decembre 2025 a 10 h a New York, soit 15:00 UTC.
"""
from datetime import date

from conftest import FakeHttp
from market_intelligence import catalog
from market_intelligence.connectors import fred as fred_module
from market_intelligence.connectors.fred import FredConnector, market_rule_time
from market_intelligence.connectors.mt5_calendar import COLUMNS, Mt5CalendarConnector
from market_intelligence.context import build_context
from market_intelligence.release_times import apply_published_times, consistency_report
from market_intelligence.store import Event, Observation
from market_intelligence.timeutil import add_business_days, parse_iso

HEADER = ";".join(COLUMNS)
EXACT = "CALENDAR_TIMEMODE_DATETIME"
NONE = "CALENDAR_UNIT_NONE/CALENDAR_MULTIPLIER_NONE;CALENDAR_IMPACT_NA"


def line(value_id, event_id, utc, name, importance=3, mode=EXACT, actual=""):
    day, clock = utc.split(" ")
    hour = int(clock[:2]) + 2
    return (f"{value_id};{event_id};{utc};{day} {hour:02d}{clock[2:]};7200;{mode};USD;{importance};{name};"
            f"{actual};;;;{NONE}")


NFP_DEC = line(2001, "840030016", "2025.12.16 13:30:00", "Salaires non-agricoles")
NFP_JAN = line(2002, "840030016", "2026.01.09 13:30:00", "Salaires non-agricoles")
NFP_EARLY = line(2003, "840030016", "2025.11.20 12:30:00", "Salaires non-agricoles")      # 7 h 30 a New York
UNEMPLOYMENT = line(2004, "840030015", "2025.12.16 13:30:00", "Taux de Chômage", actual="4.6")
THANKSGIVING = line(2005, "840031009", "2026.11.26 00:00:00", "Jour de Thanksgiving", importance=0,
                    mode="CALENDAR_TIMEMODE_DATE")


def import_calendar(store, settings, *lines):
    inbox = settings.inbox_dir / "mt5_calendar"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "amb_calendar_USD.csv").write_text("\n".join([HEADER, *lines]) + "\n", encoding="utf-8")
    report = Mt5CalendarConnector(None, store, settings).collect(date(2015, 1, 1))
    assert report.ok, report.errors
    return report


def test_published_time_replaces_the_estimate_and_removes_the_margin(unrate_store, settings):
    before = unrate_store.revisions("UNRATE", "2025-11-01")[0]
    assert (before["time_quality"], before["available_at_balanced"]) == ("estimated", "2025-12-16T13:45:00Z")
    report = import_calendar(unrate_store, settings, NFP_DEC, NFP_JAN, UNEMPLOYMENT)
    assert any("heures reelles appliquees" in n for n in report.notes)
    row = unrate_store.revisions("UNRATE", "2025-11-01")[0]
    assert row["time_quality"] == "published" and row["value"] == 4.6           # la valeur ne change pas
    assert row["available_at"] == row["available_at_balanced"] == row["available_at_strict"] == "2025-12-16T13:30:00Z"
    # Visible a l'heure reelle, dans les trois politiques, et pas une seconde avant.
    for policy in ("balanced", "strict", "estimate"):
        assert "2025-11-01" not in {r["obs_date"] for r in unrate_store.asof("UNRATE", "2025-12-16T13:29:59Z", policy=policy)}
        assert "2025-11-01" in {r["obs_date"] for r in unrate_store.asof("UNRATE", "2025-12-16T13:30:00Z", policy=policy)}
    # Une version sans evenement MT5 ce jour-la reste estimee.
    other = unrate_store.db.execute(
        "SELECT DISTINCT time_quality FROM observations WHERE vintage_date='2025-09-05'").fetchall()
    assert [r[0] for r in other] == ["estimated"]


def test_later_real_time_delays_visibility(store, settings):
    """PCE du 5 decembre 2025 : estime a 8 h 30 (13:30 UTC), reellement publie a 10 h (15:00 UTC)."""
    store.add_observations([Observation("PCEPI", "2025-09-01", "2025-12-05", 1.0, "2025-12-05T13:30:00Z",
                                        "vintage", "alfred", release_kind="initial")])
    assert store.asof("PCEPI", "2025-12-05T13:45:00Z")                          # vu trop tot sans calendrier
    import_calendar(store, settings, line(3001, "840010003", "2025.12.05 15:00:00", "Indice des prix PCE m/m"))
    assert store.asof("PCEPI", "2025-12-05T14:59:59Z") == []
    assert store.asof("PCEPI", "2025-12-05T15:00:00Z")[0]["time_quality"] == "published"


def test_real_time_earlier_than_the_estimate_is_not_applied(unrate_store, settings):
    """Emploi du 20 novembre 2025, indique a 7 h 30 a New York dans l'export : douteux.
    L'heure la plus tardive est gardee, ce qui ne peut pas creer de look-ahead."""
    import_calendar(unrate_store, settings, NFP_EARLY)
    result = apply_published_times(unrate_store)
    assert [e["day"] for e in result["earlier_than_estimate"]] == ["2025-11-20"]
    row = unrate_store.db.execute("SELECT * FROM observations WHERE vintage_date='2025-11-20'").fetchone()
    assert row["time_quality"] == "estimated" and row["available_at"] == "2025-11-20T13:30:00Z"
    # L'evenement de synthese n'est pas cree non plus a cette heure douteuse.
    assert unrate_store.db.execute("SELECT COUNT(*) FROM events WHERE event_key LIKE 'fred_release|%'").fetchone()[0] == 0


def test_observed_and_already_published_rows_are_never_rewritten(store):
    store.add_observations([Observation("PAYEMS", "2026-10-01", "2026-11-06", 1.0, "2026-11-06T14:02:11Z",
                                        "observed", "fred", time_quality="observed")])
    assert store.refine_availability("PAYEMS", "2026-11-06", "2026-11-06T13:30:00Z") == 0
    assert store.db.execute("SELECT available_at FROM observations").fetchone()[0] == "2026-11-06T14:02:11Z"


def test_release_event_gets_the_published_time_and_duplicates_are_hidden(unrate_store, settings):
    unrate_store.upsert_events([Event("fred_release|50|2025-12-16", "alfred", "data_release",
                                      "Employment Situation (NFP, chomage)", "2025-12-16T13:30:00Z",
                                      "estimated", "high")], "2025-12-01T00:00:00Z")
    import_calendar(unrate_store, settings, NFP_DEC, UNEMPLOYMENT)
    row = unrate_store.db.execute("SELECT * FROM events WHERE event_key='fred_release|50|2025-12-16'").fetchone()
    assert row["time_basis"] == "published" and row["first_seen_at"] == "2025-12-01T00:00:00Z"
    upcoming = build_context(unrate_store, parse_iso("2025-12-15T12:00:00Z"), historical=True)["events"]["upcoming_7d"]
    titles = [e["title"] for e in upcoming]
    assert "Salaires non-agricoles" in titles and "Taux de Chômage" in titles
    assert "Employment Situation (NFP, chomage)" not in titles                  # ligne de synthese ecartee


def test_holidays_come_from_the_calendar_and_shift_the_business_day_rule(store, settings):
    assert add_business_days(date(2026, 11, 25), 1) == date(2026, 11, 26)       # sans calendrier : ferie ignore
    import_calendar(store, settings, THANKSGIVING)
    assert store.holidays() == frozenset({"2026-11-26"})
    assert add_business_days(date(2026, 11, 25), 1, store.holidays()) == date(2026, 11, 27)
    dgs10 = catalog.SERIES_BY_ID["DGS10"]
    # Rendement du mercredi 25 novembre : disponible le vendredi 27, pas le jeudi ferie.
    assert market_rule_time(dgs10, "2026-11-25", store.holidays()).date() == date(2026, 11, 27)
    # Un jour ferie n'est pas un evenement de marche : il n'apparait pas dans le calendrier du contexte.
    ctx = build_context(store, parse_iso("2026-11-24T12:00:00Z"), historical=True)
    assert ctx["events"]["upcoming_7d"] == []


def test_fred_collect_applies_real_times_when_the_calendar_was_imported_first(store, settings, monkeypatch):
    import_calendar(store, settings, NFP_DEC, NFP_JAN)
    series = tuple(s for s in catalog.FRED_SERIES if s.series_id == "UNRATE")
    monkeypatch.setattr(fred_module, "FRED_SERIES", series)
    monkeypatch.setattr(fred_module, "RELEASE_SERIES", {50: "UNRATE"})
    monkeypatch.setattr(fred_module, "FRED_RELEASES", {50: catalog.FRED_RELEASES[50]})
    routes = [("downloaddata?seid=UNRATE", "alfred_vintages_UNRATE.html"),
              ("vintage_date=2025-01-10", "alfredgraph_UNRATE_batch1.csv"),
              ("vintage_date=2026-02-11", "alfredgraph_UNRATE_batch2.csv"),
              ("releases/calendar", "fred_release_calendar_employment.html")]
    report = FredConnector(FakeHttp(store, routes), store, settings).collect(date(2025, 1, 1))
    assert report.ok and any("heures reelles du calendrier MT5" in n for n in report.notes)
    qualities = dict(store.db.execute(
        "SELECT vintage_date, time_quality FROM observations WHERE vintage_date IN ('2025-12-16','2025-08-01') GROUP BY 1"))
    assert qualities == {"2025-12-16": "published", "2025-08-01": "estimated"}


def test_consistency_report_compares_alfred_and_calendar(unrate_store, settings):
    assert consistency_report(unrate_store)["releases"]["Employment Situation (NFP, chomage)"] == {"status": "unavailable"}
    import_calendar(unrate_store, settings, NFP_DEC, NFP_JAN)
    report = consistency_report(unrate_store)
    assert report["rows_by_basis_and_time_quality"]["vintage/published"] > 0
    assert report["holidays_known"] == 0


# ----------------------------------------------------------------- CFTC
def cot_line(value_id, utc, actual):
    day, clock = utc.split(" ")
    return (f"{value_id};840520003;{utc};{day} {int(clock[:2]) + 2:02d}{clock[2:]};7200;{EXACT};USD;1;"
            f"Positions nettes non commerciales de CFTC Gold;{actual};;;;"
            "CALENDAR_UNIT_NONE/CALENDAR_MULTIPLIER_THOUSANDS;CALENDAR_IMPACT_NA")


def _collect_cot(store, settings, now):
    from market_intelligence.connectors.cftc import CftcConnector
    http = FakeHttp(store, [("72hh-3qpy", "cftc_gold_since_2026-09.json"),
                            ("6dca-aqww", "cftc_gold_legacy_since_2026-09.json")], now=now)
    return CftcConnector(http, store, settings).collect(date(2026, 9, 1))


def test_cot_reports_are_dated_by_their_real_publication(store, settings):
    """Valeurs et horaires reels : le rapport du mardi 27 octobre 2026 (position nette non
    commerciale 186 451) parait le vendredi 30 a 19:30 UTC ; celui du 3 novembre (179 548)
    le vendredi 6 a 20:30 UTC, apres le changement d'heure."""
    import_calendar(store, settings, cot_line(4001, "2026.10.30 19:30:00", "186.5"),
                    cot_line(4002, "2026.11.06 20:30:00", "179.5"))
    report = _collect_cot(store, settings, now="2026-12-15T09:00:00Z")      # historique charge apres coup
    assert report.ok and any("2 rapports dates" in n for n in report.notes)
    row = store.revisions("COT_GOLD_MM_NET", "2026-10-27")[0]
    assert row["time_quality"] == "published" and row["available_at"] == "2026-10-30T19:30:00Z"
    assert store.revisions("COT_GOLD_MM_NET", "2026-11-03")[0]["available_at"] == "2026-11-06T20:30:00Z"
    # Sans publication correspondante dans le calendrier : la regle (samedi) est conservee.
    other = store.revisions("COT_GOLD_MM_NET", "2026-10-20")[0]
    assert other["time_quality"] == "estimated" and other["available_at"] == "2026-10-24T19:30:00Z"
    # Les positions du mardi ne sont pas visibles avant leur publication.
    assert "2026-11-03" not in {r["obs_date"] for r in store.asof("COT_GOLD_MM_NET", "2026-11-06T20:29:59Z")}
    assert "2026-11-03" in {r["obs_date"] for r in store.asof("COT_GOLD_MM_NET", "2026-11-06T20:30:00Z")}


def test_delayed_cot_report_is_matched_by_value_not_by_calendar_position(store, settings):
    """Un rapport publie en retard ne doit pas etre date par la premiere publication venue :
    seule une publication de meme valeur convient."""
    import_calendar(store, settings, cot_line(4003, "2026.10.30 19:30:00", "999.9"),   # autre valeur
                    cot_line(4004, "2026.11.20 20:30:00", "186.5"))                    # la bonne, tardive
    _collect_cot(store, settings, now="2026-12-15T09:00:00Z")
    row = store.revisions("COT_GOLD_MM_NET", "2026-10-27")[0]
    assert row["available_at"] == "2026-11-20T20:30:00Z" and row["time_quality"] == "published"


def test_cot_collection_survives_a_missing_legacy_report(store, settings):
    from market_intelligence.connectors.cftc import CftcConnector
    http = FakeHttp(store, [("72hh-3qpy", "cftc_gold_since_2026-09.json"), ("6dca-aqww", None)])
    report = CftcConnector(http, store, settings).collect(date(2026, 9, 1))
    assert report.ok and report.rows_added == 40 and any("legacy indisponible" in n for n in report.notes)
