"""Lecteur des exports du calendrier MT5.

IMPORTANT : aucun export reel de MetaTrader n'etait disponible. Les lignes
ci-dessous reprennent des valeurs reelles affichees sur la page publique du
calendrier MQL5 le 2026-11-12 (CPI americain du 10 novembre 2026, decision de la
Fed du 28 octobre 2026), mises a la main au format de notre script d'export. Les
identifiants `value_id` et `event_id` sont arbitraires. Ces tests valident le
lecteur et ses controles, pas le script MQL5.
"""
from datetime import date

import pytest

from market_intelligence.connectors.mt5_calendar import (COLUMNS, Mt5CalendarConnector,
                                                         check_times_against_fed, parse_calendar_csv)
from market_intelligence.errors import ParseError
from market_intelligence.store import Event

HEADER = ";".join(COLUMNS)
PCT = "CALENDAR_UNIT_PERCENT/CALENDAR_MULTIPLIER_NONE"
EXACT = "CALENDAR_TIMEMODE_DATETIME"
# Serveur a UTC+2 : 15:30 heure serveur = 13:30 UTC.
CPI_MM = f"1001;840010001;2026.11.10 13:30:00;2026.11.10 15:30:00;7200;{EXACT};USD;3;CPI m/m;0.4;0.5;0.6;;{PCT};CALENDAR_IMPACT_NEGATIVE"
CORE_CPI = f"1002;840010002;2026.11.10 13:30:00;2026.11.10 15:30:00;7200;{EXACT};USD;3;Core CPI m/m;0.3;0.2;0.3;;{PCT};CALENDAR_IMPACT_POSITIVE"
CPI_YY_NO_ACTUAL = f"1003;840010003;2026.11.10 13:30:00;2026.11.10 15:30:00;7200;{EXACT};USD;3;CPI y/y;;4.2;3.7;;{PCT};CALENDAR_IMPACT_NA"
FED = f"1004;840050001;2026.10.28 18:00:00;2026.10.28 20:00:00;7200;{EXACT};USD;3;Fed Interest Rate Decision;4.00;4.00;4.00;;{PCT};CALENDAR_IMPACT_NA"


def write(tmp_path, *lines, header=HEADER, name="amb_calendar_USD.csv"):
    path = tmp_path / name
    path.write_text("\n".join([header, *lines]) + "\n", encoding="utf-8")
    return path


def test_reads_values_times_and_importance(tmp_path):
    events = parse_calendar_csv(write(tmp_path, CPI_MM, CORE_CPI, CPI_YY_NO_ACTUAL))
    cpi = events[0]
    assert cpi.event_key == "mt5|1001" and cpi.title == "CPI m/m" and cpi.importance == "high"
    assert cpi.scheduled_at == "2026-11-10T13:30:00Z" and cpi.time_basis == "published"
    assert (cpi.actual, cpi.forecast, cpi.previous) == ("0.4 [percent]", "0.5 [percent]", "0.6 [percent]")
    assert cpi.revised_previous is None


def test_missing_actual_stays_missing(tmp_path):
    event = parse_calendar_csv(write(tmp_path, CPI_YY_NO_ACTUAL))[0]
    assert event.actual is None and event.forecast == "4.2 [percent]"


def test_non_exact_time_mode_is_marked_estimated(tmp_path):
    line = CPI_MM.replace(EXACT, "CALENDAR_TIMEMODE_TENTATIVE")
    assert parse_calendar_csv(write(tmp_path, line))[0].time_basis == "estimated"


@pytest.mark.parametrize("bad, reason", [
    (CPI_MM.replace(";0.4;", ";abc;"), "non numerique"),
    (CPI_MM.replace("2026.11.10 13:30:00;", "10/11/2026 13:30;", 1), "date illisible"),
    (CPI_MM.replace(";7200;", ";3600;"), "incoherents"),
    (CPI_MM.replace(EXACT, "EXACT"), "time_mode inconnu"),
    (CPI_MM.replace(";USD;3;", ";USD;9;"), "importance inconnue"),
    (CPI_MM.rsplit(";", 2)[0], "nombre de colonnes"),
    (CPI_MM.replace("1001;", "x1;", 1), "value_id invalide"),
])
def test_malformed_lines_are_rejected(tmp_path, bad, reason):
    with pytest.raises(ParseError, match=reason):
        parse_calendar_csv(write(tmp_path, bad))


def test_duplicate_ids_and_wrong_header_are_rejected(tmp_path):
    with pytest.raises(ParseError, match="en double"):
        parse_calendar_csv(write(tmp_path, CPI_MM, CPI_MM))
    with pytest.raises(ParseError, match="colonnes inattendues"):
        parse_calendar_csv(write(tmp_path, CPI_MM, header="a;b;c"))


def _with_fed_statement(store):
    # Communique reel du FOMC du 28 octobre 2026, 14 h a New York (flux de la Fed).
    store.upsert_events([Event("fed_press|/newsevents/pressreleases/monetary20261028a.htm", "fed",
                               "fomc_statement", "Federal Reserve issues FOMC statement",
                               "2026-10-28T18:00:00Z", "published", "high")], "2026-11-12T09:00:00Z")
    return store


def test_times_are_cross_checked_with_fed_statements(store, tmp_path):
    _with_fed_statement(store)
    good = parse_calendar_csv(write(tmp_path, FED))
    assert check_times_against_fed(good, store) == {"compared": 1, "offsets_minutes": {0: 1}}
    shifted = FED.replace("2026.10.28 18:00:00;2026.10.28 20:00:00;7200", "2026.10.28 17:00:00;2026.10.28 20:00:00;10800")
    bad = parse_calendar_csv(write(tmp_path, shifted))
    assert check_times_against_fed(bad, store) == {"compared": 1, "offsets_minutes": {-60: 1}}


def test_collect_imports_a_consistent_export(store, settings):
    _with_fed_statement(store)
    inbox = settings.inbox_dir / "mt5_calendar"
    inbox.mkdir(parents=True)
    path = write(inbox, CPI_MM, CPI_YY_NO_ACTUAL, FED)
    report = Mt5CalendarConnector(None, store, settings).collect(date(2026, 1, 1))
    assert report.ok and report.events_upserted == 3
    assert any("recoupees" in n for n in report.notes)
    assert not path.exists()                                         # deplace dans l'archive brute
    assert len(list((settings.raw_dir / "mt5_calendar").rglob("*.csv"))) == 1
    # Avant l'heure de publication : la prevision est visible, pas le resultat.
    window = ("2026-11-10T00:00:00Z", "2026-11-11T00:00:00Z")
    before = {e["title"]: e for e in store.events_between(*window, asof="2026-11-10T13:29:59Z")}
    after = {e["title"]: e for e in store.events_between(*window, asof="2026-11-10T13:30:00Z")}
    assert before["CPI m/m"]["forecast"] == "0.5 [percent]" and before["CPI m/m"]["actual"] is None
    assert after["CPI m/m"]["actual"] == "0.4 [percent]"


def test_collect_refuses_an_export_with_shifted_times(store, settings):
    _with_fed_statement(store)
    inbox = settings.inbox_dir / "mt5_calendar"
    inbox.mkdir(parents=True)
    shifted = FED.replace("2026.10.28 18:00:00;2026.10.28 20:00:00;7200", "2026.10.28 17:00:00;2026.10.28 20:00:00;10800")
    path = write(inbox, CPI_MM, shifted)
    report = Mt5CalendarConnector(None, store, settings).collect(date(2026, 1, 1))
    assert not report.ok and "decalees" in report.errors[0] and report.events_upserted == 0
    assert path.exists()                                             # rien n'est importe ni deplace
    assert store.db.execute("SELECT COUNT(*) FROM events WHERE source='mt5_calendar'").fetchone()[0] == 0


def test_collect_reports_malformed_file_and_keeps_it(store, settings):
    inbox = settings.inbox_dir / "mt5_calendar"
    inbox.mkdir(parents=True)
    path = write(inbox, CPI_MM.replace(";0.4;", ";abc;"))
    report = Mt5CalendarConnector(None, store, settings).collect(date(2026, 1, 1))
    assert not report.ok and path.exists()


def test_export_script_writes_the_columns_the_reader_expects():
    """Garde-fou : l'en-tete ecrit par le script MQL5 doit rester celui du lecteur."""
    from conftest import ROOT
    script = (ROOT / "mt5" / "Scripts" / "AMB_ExportCalendar.mq5").read_text(encoding="utf-8")
    assert f'"{HEADER}\\n"' in script


# --------------------------------------------------------------------------
# Fichier d'exemple au format de l'export et verifications statiques du script
# --------------------------------------------------------------------------
def _script():
    from conftest import ROOT
    return (ROOT / "mt5" / "Scripts" / "AMB_ExportCalendar.mq5").read_text(encoding="utf-8")


def _code_only(text):
    """Retire commentaires, chaines et litteraux caractere pour ne garder que le code."""
    import re
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
    return re.sub(r"'(?:\\.|[^'\\])*'", "''", text)


def test_format_sample_file_is_read(store):
    """Fichier d'exemple fabrique a la main au format de l'export (voir l'en-tete de ce module)."""
    from conftest import FIXTURES
    events = parse_calendar_csv(FIXTURES / "mt5_calendar_format_sample.csv")
    assert len(events) == 5
    speech = events[-1]
    assert speech.time_basis == "estimated" and speech.importance == "medium"
    assert (speech.actual, speech.forecast, speech.previous) == (None, None, None)
    _with_fed_statement(store)
    assert check_times_against_fed(events, store) == {"compared": 1, "offsets_minutes": {0: 1}}


def test_script_static_delimiters_are_balanced():
    code = _code_only(_script())
    for opening, closing in ("{}", "()", "[]"):
        assert code.count(opening) == code.count(closing), f"{opening}{closing} non equilibres"
    depth = 0
    for ch in code:
        depth += (ch == "{") - (ch == "}")
        assert depth >= 0


def test_script_static_uses_only_reviewed_calendar_api():
    """Les fonctions et membres du calendrier utilises sont ceux relus dans la documentation
    MQL5 le 2026-11-12. Ce test ne remplace pas une compilation."""
    import re
    code = _code_only(_script())
    assert "#property strict" not in code                      # directive MQL4
    calendar_calls = set(re.findall(r"\b(Calendar\w+)\s*\(", code))
    assert calendar_calls == {"CalendarValueHistory", "CalendarEventById"}
    members = set(re.findall(r"values\[i\]\.(\w+)", code)) | set(re.findall(r"\bev\.(\w+)", code))
    assert members <= {"time", "event_id", "id", "impact_type", "HasActualValue", "GetActualValue",
                       "HasForecastValue", "GetForecastValue", "HasPreviousValue", "GetPreviousValue",
                       "HasRevisedValue", "GetRevisedValue", "digits", "time_mode", "importance",
                       "name", "unit", "multiplier"}
    functions = set(re.findall(r"(?<![.\w])([A-Z]\w+)\s*\(", code)) - calendar_calls   # hors methodes
    assert functions <= {"OnStart", "ExportRange", "Clean", "StringReplace", "ResetLastError", "GetLastError",
                         "PrintFormat", "Print", "TimeToString", "DoubleToString", "IntegerToString",
                         "EnumToString", "FileOpen", "FileWriteString", "FileClose", "FileDelete",
                         "TimeTradeServer", "TimeGMT", "MathRound", "MathMin", "TerminalInfoInteger",
                         "IsStopped"}, functions


def test_script_static_never_writes_a_default_for_a_missing_value():
    code = _script()
    for field in ("Actual", "Forecast", "Previous", "Revised"):
        assert f'Has{field}Value()' in code and f'Get{field}Value()' in code
    assert code.count(': "";') == 4                           # valeur absente -> champ vide
