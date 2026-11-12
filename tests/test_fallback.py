"""Bascule vers les rendements H.15 de la Federal Reserve quand FRED fait defaut."""
from datetime import date

from conftest import FakeHttp
from market_intelligence import catalog
from market_intelligence.connectors import fred as fred_module
from market_intelligence.connectors.fed import FedConnector
from market_intelligence.connectors.fred import FredConnector
from market_intelligence.context import build_context, flat_rows
from market_intelligence.timeutil import parse_iso

ASOF = parse_iso("2026-11-12T09:00:00Z")
FED_ROUTES = [("fomccalendars", "fed_fomc_calendar.html"), ("ne-press.json", "fed_press.json"),
              ("ne-speeches.json", "fed_speeches.json"), ("Output.aspx", "fed_h15.csv")]


def _collect_fed(store, settings):
    assert FedConnector(FakeHttp(store, FED_ROUTES), store, settings).collect(date(2026, 10, 1)).ok


def _collect_fred_dgs10(store, settings, monkeypatch, fixture="fredgraph_DGS10.csv"):
    monkeypatch.setattr(fred_module, "FRED_SERIES",
                        tuple(s for s in catalog.FRED_SERIES if s.series_id == "DGS10"))
    FredConnector(FakeHttp(store, [("/fredgraph.csv", fixture)]), store, settings).collect_market(
        catalog.SERIES_BY_ID["DGS10"], date(2026, 10, 26))


def test_fred_is_used_when_it_is_available_and_up_to_date(store, settings, monkeypatch):
    _collect_fed(store, settings)
    _collect_fred_dgs10(store, settings, monkeypatch)
    us10y = build_context(store, ASOF)["rates"]["us10y"]
    assert us10y["series_id"] == "DGS10" and us10y["source"] == "fred" and us10y["value"] == 4.88
    assert us10y["fallback"] == {"series_id": "FED_H15_Y10", "used": False, "status": "ok"}


def test_fed_yields_replace_fred_when_fred_has_no_data(store, settings):
    """FRED en panne des la premiere collecte : rien n'est stocke pour DGS10."""
    _collect_fed(store, settings)
    report = FredConnector(FakeHttp(store, [("stlouisfed.org", None)]), store, settings).collect(date(2026, 10, 1))
    assert not report.ok
    ctx = build_context(store, ASOF)
    us10y, us2y = ctx["rates"]["us10y"], ctx["rates"]["us2y"]
    assert (us10y["series_id"], us10y["source"], us10y["value"], us10y["obs_date"]) == \
        ("FED_H15_Y10", "fed_h15", 4.88, "2026-11-09")
    assert us10y["fallback"] == {"used": True, "replaces": "DGS10", "reason": "serie principale unavailable"}
    assert us10y["title"] == catalog.SERIES_BY_ID["DGS10"].title and us10y["unit"] == "%"
    assert us2y["value"] == 4.56 and us2y["fallback"]["used"]
    flat = {k: (v, s, src) for k, v, s, src in flat_rows(ctx)}
    assert flat["us10y_value"] == ("4.88", "ok", "fed_h15")           # l'EA voit la source reelle


def test_fed_yields_replace_fred_when_fred_is_stale(store, settings, monkeypatch):
    _collect_fed(store, settings)
    _collect_fred_dgs10(store, settings, monkeypatch)
    # Plus de 6 jours apres la derniere valeur FRED, sans nouvelle collecte FRED : la serie
    # est perimee. La Fed, elle, n'a pas de valeur plus recente dans cet enregistrement :
    # les deux sont perimees, aucune valeur n'est presentee comme exploitable.
    late = build_context(store, parse_iso("2026-11-20T09:00:00Z"))["rates"]["us10y"]
    assert late["status"] == "stale" and late["fallback"]["used"] is False


def test_fed_yields_are_preferred_when_fred_lags_behind(store, settings, monkeypatch, tmp_path):
    """FRED repond, mais avec un jour de retard sur la Fed : la valeur la plus recente gagne."""
    import conftest
    text = (conftest.FIXTURES / "fredgraph_DGS10.csv").read_text()
    lagging = tmp_path / "lagging.csv"
    lagging.write_text("".join(line for line in text.splitlines(keepends=True) if "2026-11-09" not in line))
    _collect_fed(store, settings)
    _collect_fred_dgs10(store, settings, monkeypatch, fixture=str(lagging))
    us10y = build_context(store, ASOF)["rates"]["us10y"]
    assert us10y["series_id"] == "FED_H15_Y10" and us10y["obs_date"] == "2026-11-09"
    assert us10y["fallback"] == {"used": True, "replaces": "DGS10",
                                 "reason": "serie principale en retard (2026-11-06)"}


def test_no_value_is_invented_when_both_sources_fail(store):
    us10y = build_context(store, ASOF)["rates"]["us10y"]
    assert us10y["status"] == "unavailable" and "value" not in us10y
    assert us10y["fallback"] == {"series_id": "FED_H15_Y10", "used": False, "status": "unavailable"}


def test_fallback_respects_point_in_time(store, settings):
    _collect_fed(store, settings)
    # La valeur H.15 du 9 novembre a ete vue par notre collecteur le 12 novembre a 09:00 UTC.
    before = build_context(store, parse_iso("2026-11-12T08:59:59Z"), historical=True)["rates"]["us10y"]
    assert before.get("obs_date") != "2026-11-09"
