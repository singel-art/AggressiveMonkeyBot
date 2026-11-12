"""Lecteur des classeurs World Gold Council.

Les conditions d'utilisation du WGC interdisent la redistribution : AUCUN extrait
reel n'est embarque ici. Les classeurs de ce fichier sont FABRIQUES par le test, a
la mise en page des vrais fichiers (constatee sur l'archive du 12 novembre 2026),
avec des valeurs volontairement fictives : pays « TESTLAND », fonds « TEST FUND ».
Ils valident la lecture du format, pas des chiffres.

Un test facultatif lit les vrais classeurs si la variable AMB_WGC_REAL_DIR pointe
vers l'archive extraite. Il est ignore ailleurs, notamment en integration continue.
"""
import os
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest

from market_intelligence.connectors.wgc import WgcConnector, read_workbook
from market_intelligence.context import build_context
from market_intelligence.errors import ParseError
from market_intelligence.timeutil import parse_iso

EDITION = datetime(2026, 11, 3, 15, 0, 0)


def _save(wb, path, modified=EDITION):
    """Enregistre le classeur puis fixe sa date de modification interne (openpyxl la
    remplace par l'heure courante a l'enregistrement)."""
    import re
    import zipfile
    wb.save(path)
    with zipfile.ZipFile(path) as src:
        parts = {name: src.read(name) for name in src.namelist()}
    core = parts["docProps/core.xml"].decode("utf-8")
    core, n = re.subn(r"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)",
                      rf"\g<1>{modified:%Y-%m-%dT%H:%M:%SZ}\g<2>", core)
    assert n == 1
    parts["docProps/core.xml"] = core.encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as dst:
        for name, content in parts.items():
            dst.writestr(name, content)
    return path


def make_etf(path, months=("2026-08-31", "2026-09-30", "2026-10-31"), holdings=(100.0, 110.0, 105.0),
             fund_a=(60.0, 66.0, 63.0), fund_b=(40.0, 44.0, 42.0),
             header=("Date", "Gold, US$/oz", "Ounces", "Tonnes", "Value (USD)"), modified=EDITION):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    wb.create_sheet("Disclaimer")
    per_sheet = {"Holdings by month": (fund_a, fund_b),
                 "Demand by month": ((1.0, 6.0, -3.0), (0.5, 4.0, -2.0)),
                 "Fund flows by month": ((10.0, 60.0, -30.0), (5.0, None, -20.0))}
    for name, (a, b) in per_sheet.items():
        ws = wb.create_sheet(name)
        ws.append(["ticker", "All units fictitious", None, None, None, "testa xx equity", "testb xx equity"])
        ws.append(["Active", None, None, None, None, "Active", "Inactive"])
        ws.append(["Fund Type", None, None, None, None, "ETF", "ETF"])
        ws.append(["Region", None, None, None, None, "Testregion", "Testregion"])
        ws.append(["Country", None, None, None, None, "TL", "TL"])
        ws.append([*header, "TEST FUND A", "TEST FUND B"])
        for i, month in enumerate(months):
            ws.append([datetime.fromisoformat(month), 1000.0, holdings[i] * 32150.7, holdings[i],
                       holdings[i] * 1e6, a[i], b[i]])
        ws.append([None, None])
        ws.append([None, "Column : fictitious footer"])
    return _save(wb, path, modified)


def make_cb(path, months=("2026-07-01", "2026-08-01", "2026-09-01"), header=("Country", "Comments")):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name in ("Main", "Disclaimer"):
        wb.create_sheet(name)
    ws = wb.create_sheet("Monthly")
    ws.append([*header, *[datetime.fromisoformat(m) for m in months]])
    ws.append(["Testland", None, 2.0, 3.0, 4.0])
    ws.append(["Republic of Example", "fictitious comment", -1.0, 0, ""])   # '' = pas de donnee
    ws.append(["Nowhere, Rep. of", None, "", "", ""])
    ws.append([None, None, None, None, None])
    wb.create_sheet("Annual")
    return _save(wb, path, datetime(2026, 10, 30, 11, 0, 0))


def make_premiums(path, china_title="Chinese Premium/Discount (US$/oz) - 5 day moving average"):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    wb.create_sheet("Disclaimer")
    for name, title, start in (("Chinese premiums-discounts", china_title, 1.0),
                               ("Indian premiums-discounts", "Indian Premium/Discount (US$/oz) - 5 day moving average", -2.0)):
        ws = wb.create_sheet(name)
        ws.append(["Gold market premium/discount data"])
        ws.append(["For more details (fictitious)"])
        ws.append([None])
        ws.append([title])
        ws.append(["Source: fictitious"])
        ws.append(["2026-11-04", start])                       # date en texte, comme les lignes anciennes
        ws.append([datetime(2026, 11, 5), start + 0.5])        # date en cellule, comme les lignes recentes
        ws.append([datetime(2026, 11, 6), None])               # valeur absente : ignoree
    return _save(wb, path, datetime(2026, 11, 7, 20, 0, 0))


def test_etf_workbook(tmp_path):
    ds = read_workbook(make_etf(tmp_path / "anything.xlsx"))
    assert ds.kind == "etf_flows" and ds.edition_date == "2026-11-03"
    assert ds.series["WGC_ETF_HOLDINGS_TONNES"] == [("2026-08-31", 100.0), ("2026-09-30", 110.0), ("2026-10-31", 105.0)]
    assert ds.series["WGC_ETF_HOLDINGS_USD"][-1] == ("2026-10-31", 105e6)
    assert ds.series["WGC_ETF_DEMAND_TONNES_SUM"] == [("2026-08-31", 1.5), ("2026-09-30", 10.0), ("2026-10-31", -5.0)]
    # Une cellule vide n'est pas comptee comme zero : la somme porte sur les fonds renseignes.
    assert ds.series["WGC_ETF_FLOWS_USD_MN_SUM"] == [("2026-08-31", 15.0), ("2026-09-30", 60.0), ("2026-10-31", -50.0)]


def test_central_bank_workbook(tmp_path):
    ds = read_workbook(make_cb(tmp_path / "cb.xlsx"))
    assert ds.kind == "cb_changes" and ds.edition_date == "2026-10-30"
    assert ds.series["WGC_CB_NET_TONNES_SUM"] == [("2026-07-01", 1.0), ("2026-08-01", 3.0), ("2026-09-01", 4.0)]
    assert ds.series["WGC_CB_REPORTING_COUNT"] == [("2026-07-01", 2.0), ("2026-08-01", 2.0), ("2026-09-01", 1.0)]


def test_premiums_workbook_accepts_text_and_cell_dates(tmp_path):
    ds = read_workbook(make_premiums(tmp_path / "p.xlsx"))
    assert ds.kind == "gold_premiums"
    assert ds.series["WGC_PREMIUM_CHINA_USD_OZ"] == [("2026-11-04", 1.0), ("2026-11-05", 1.5)]
    assert ds.series["WGC_PREMIUM_INDIA_USD_OZ"] == [("2026-11-04", -2.0), ("2026-11-05", -1.5)]


def test_recognition_does_not_depend_on_the_file_name(tmp_path):
    assert read_workbook(make_cb(tmp_path / "renamed (1).xlsx")).kind == "cb_changes"


def test_unsupported_files_return_none(tmp_path):
    other = openpyxl.Workbook()
    other.active.title = "Mine production data"
    assert read_workbook(_save(other, tmp_path / "other.xlsx")) is None
    pdf = tmp_path / "note.pdf"
    pdf.write_bytes(b"%PDF-1.4 fictitious")
    assert read_workbook(pdf) is None


@pytest.mark.parametrize("maker, kwargs, reason", [
    (make_etf, {"header": ("Date", "Gold", "Ounces", "Tonnes", "Value")}, "en-tete 6 inattendue"),
    (make_cb, {"header": ("Pays", "Comments")}, "en-tete inattendu"),
    (make_premiums, {"china_title": "Another series"}, "titre de serie inattendu"),
    (make_cb, {"months": ("2026-07-01", "2026-07-01", "2026-09-01")}, "dates en double"),
])
def test_changed_layout_is_refused(tmp_path, maker, kwargs, reason):
    with pytest.raises(ParseError, match=reason):
        read_workbook(maker(tmp_path / "x.xlsx", **kwargs))


def test_corrupt_workbook_is_refused(tmp_path):
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(b"not a zip file")
    with pytest.raises(ParseError, match="illisible"):
        read_workbook(bad)


def _connector(store, settings):
    (settings.inbox_dir / "wgc").mkdir(parents=True, exist_ok=True)
    return WgcConnector(None, store, settings), settings.inbox_dir / "wgc"


def test_collect_imports_with_reception_time_and_never_claims_a_publication_date(store, settings):
    connector, inbox = _connector(store, settings)
    make_etf(inbox / "etf.xlsx")
    report = connector.collect(date(2015, 1, 1))
    assert report.ok and report.rows_added == 12
    rows = store.db.execute("SELECT * FROM observations WHERE series_id='WGC_ETF_HOLDINGS_TONNES' ORDER BY obs_date").fetchall()
    received = rows[0]["available_at"]
    assert all(r["available_at"] == received == r["available_at_balanced"] == r["available_at_strict"] for r in rows)
    assert all(r["time_quality"] == "observed" and r["available_basis"] == "observed" for r in rows)
    assert all(r["release_kind"] == "backfill" for r in rows)       # instantane : nature inconnue
    assert all(r["vintage_date"] == "2026-11-03" for r in rows)     # date d'edition du classeur
    assert received >= "2026-11-12"
    # Aucune valeur historique n'est visible avant la reception du fichier, quelle que soit la politique.
    for policy in ("balanced", "strict", "estimate"):
        assert store.asof("WGC_ETF_HOLDINGS_TONNES", "2026-11-04T00:00:00Z", policy=policy) == []
    assert not list(inbox.iterdir())
    assert len(list((settings.raw_dir / "wgc").rglob("*etf.xlsx"))) == 1


def test_second_edition_adds_new_periods_and_revisions_without_overwriting(store, settings):
    connector, inbox = _connector(store, settings)
    make_etf(inbox / "etf.xlsx")
    connector.collect(date(2015, 1, 1))
    # Edition suivante : octobre revise (105 -> 104), novembre nouveau.
    make_etf(inbox / "etf_next.xlsx", months=("2026-09-30", "2026-10-31", "2026-11-30"),
             holdings=(110.0, 104.0, 108.0), modified=datetime(2026, 12, 2, 9, 0, 0))
    report = connector.collect(date(2015, 1, 1))
    assert report.ok
    october = [(r["vintage_date"], r["value"], r["release_kind"])
               for r in store.revisions("WGC_ETF_HOLDINGS_TONNES", "2026-10-31")]
    assert october == [("2026-11-03", 105.0, "backfill"), ("2026-12-02", 104.0, "revision")]
    november = store.revisions("WGC_ETF_HOLDINGS_TONNES", "2026-11-30")
    assert [(r["value"], r["release_kind"]) for r in november] == [(108.0, "initial")]
    assert len(list((settings.raw_dir / "wgc").rglob("*.xlsx"))) == 2   # les deux versions brutes sont gardees


def test_collect_keeps_a_refused_workbook_and_archives_unread_files(store, settings):
    connector, inbox = _connector(store, settings)
    make_cb(inbox / "broken.xlsx", header=("Pays", "Comments"))
    (inbox / "methodology.pdf").write_bytes(b"%PDF-1.4 fictitious")
    report = connector.collect(date(2015, 1, 1))
    assert len(report.errors) == 1 and "broken.xlsx" in report.errors[0]
    assert (inbox / "broken.xlsx").exists() and not (inbox / "methodology.pdf").exists()
    assert "archive sans lecture : methodology.pdf" in report.notes
    assert store.series_summary() == []


def test_connector_never_touches_the_network(store, settings):
    """Le connecteur recoit http=None : tout appel reseau leverait une erreur."""
    connector, inbox = _connector(store, settings)
    make_premiums(inbox / "p.xlsx")
    assert connector.check().ok and "sans acces reseau" in connector.check().detail
    assert connector.collect(date(2015, 1, 1)).ok
    source = Path(__import__("market_intelligence.connectors.wgc", fromlist=["x"]).__file__).read_text(encoding="utf-8")
    assert "self.http" not in source and "requests" not in source


def test_context_exposes_gold_market_series_with_unknown_publication_time(store, settings):
    connector, inbox = _connector(store, settings)
    make_etf(inbox / "etf.xlsx")
    make_cb(inbox / "cb.xlsx")
    connector.collect(date(2015, 1, 1))
    received = store.db.execute("SELECT MAX(available_at) FROM observations").fetchone()[0]
    gold = build_context(store, parse_iso(received), historical=True)["gold_market"]
    holdings = gold["etf_holdings_tonnes"]
    assert holdings["value"] == 105.0 and holdings["unit"] == "tonnes" and holdings["source"] == "wgc"
    assert holdings["publication_time"] == "unknown" and holdings["change_1obs"] == -5.0
    assert gold["premium_china_usd_oz"]["status"] == "unavailable"      # classeur non depose : rien d'invente
    ctx = build_context(store, parse_iso(received), historical=True)
    assert "wgc" not in str(ctx["gold_macro_bias"])                     # hors du biais macro


@pytest.mark.skipif(not os.environ.get("AMB_WGC_REAL_DIR"), reason="archive WGC reelle non fournie (AMB_WGC_REAL_DIR)")
def test_real_archive_is_read_without_error():
    root = Path(os.environ["AMB_WGC_REAL_DIR"])
    kinds = {}
    for path in sorted(root.rglob("*.xlsx")):
        ds = read_workbook(path)
        if ds is not None:
            kinds[ds.kind] = ds
    assert set(kinds) == {"etf_flows", "cb_changes", "gold_premiums"}
    for ds in kinds.values():
        assert ds.edition_date and all(len(points) > 100 for points in ds.series.values())
    # Controle de coherence interne : la somme des variations par fonds egale la variation du total.
    h = dict(kinds["etf_flows"].series["WGC_ETF_HOLDINGS_TONNES"])
    d = dict(kinds["etf_flows"].series["WGC_ETF_DEMAND_TONNES_SUM"])
    months = sorted(h)[-12:]
    assert all(abs((h[b] - h[a]) - d[b]) < 0.01 for a, b in zip(months, months[1:]))
