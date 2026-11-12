"""Federal Reserve Board (www.federalreserve.gov), acces public sans cle.

- Calendrier du FOMC          : reunions passees et a venir (page HTML officielle).
- Communiques                 : flux JSON, avec l'heure reelle de publication.
- Discours                    : flux JSON, avec orateur et heure.
- H.15 (rendements Treasury)  : CSV du Data Download Program, source d'origine
                                des series DGS de FRED, utile si FRED est en panne.
"""
from __future__ import annotations

import csv
import html as htmllib
import io
import json
import re
from datetime import date, datetime, time

from ..errors import ParseError, SourceUnavailable
from ..store import Event, Observation
from ..timeutil import ET, add_business_days, iso, local_to_utc
from .base import CheckResult, CollectReport, Connector
from .fred import availability

BASE = "https://www.federalreserve.gov"
FOMC_CALENDAR = f"{BASE}/monetarypolicy/fomccalendars.htm"
PRESS_JSON = f"{BASE}/json/ne-press.json"
SPEECHES_JSON = f"{BASE}/json/ne-speeches.json"
H15_CSV = f"{BASE}/datadownload/Output.aspx"
H15_PARAMS = {"rel": "H15", "series": "bf17364827e38702b42a58cf8eaa3f78", "from": "", "to": "",
              "filetype": "csv", "label": "include", "layout": "seriescolumn"}
H15_MAP = {"RIFLGFCY02_N.B": "FED_H15_Y2", "RIFLGFCY10_N.B": "FED_H15_Y10"}
# Heure usuelle du communique du FOMC, le dernier jour de la reunion (heure de New York).
STATEMENT_TIME_ET = time(14, 0)
# "Chair", "Chairman" ou "Chairwoman", mais pas "Vice Chair".
CHAIR_TITLE = re.compile(r"Chair(man|woman)?\s")
MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], start=1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})


# ----------------------------------------------------------------------- parsing
def parse_fomc_calendar(page: str) -> list[dict]:
    """Extrait les reunions du FOMC. Renvoie une liste de dictionnaires :
    {year, start, end, projections, note} ou start et end sont des dates ISO."""
    meetings = []
    panels = re.split(r'<h4><a id="\d+">(\d{4}) FOMC Meetings</a></h4>', page)
    if len(panels) < 3:
        raise ParseError("structure du calendrier FOMC non reconnue")
    row_re = re.compile(
        r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>\s*</div>\s*'
        r'<div class="fomc-meeting__date[^>]*>([^<]+)</div>', re.S)
    for year_text, body in zip(panels[1::2], panels[2::2]):
        year = int(year_text)
        for month_text, date_text in row_re.findall(body):
            months = [MONTHS[m.strip()] for m in month_text.split("/") if m.strip() in MONTHS]
            days = [int(d) for d in re.findall(r"\d{1,2}", date_text.split("(")[0])]
            if not months or not days:
                continue
            start = date(year, months[0], days[0])
            end = date(year, months[-1], days[-1])
            note = (re.search(r"\(([^)]+)\)", date_text) or [None, ""])[1]
            meetings.append({"year": year, "start": start.isoformat(), "end": end.isoformat(),
                             "projections": "*" in date_text, "note": note.strip()})
    if not meetings:
        raise ParseError("aucune reunion FOMC extraite")
    return meetings


def _fed_datetime(text: str) -> datetime | None:
    """'10/28/2026 2:00:00 PM' (heure de New York) -> UTC.
    Les entrees anciennes ne portent qu'une date : sans heure, elles sont ecartees
    plutot que datees arbitrairement."""
    try:
        naive = datetime.strptime(text.strip(), "%m/%d/%Y %I:%M:%S %p")
    except ValueError:
        return None
    return local_to_utc(naive.date(), naive.time(), ET)


def _load_feed(text: str) -> list[dict]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"flux JSON de la Fed illisible : {exc}") from exc
    return [x for x in data if isinstance(x, dict) and x.get("d") and x.get("t")]


def classify_press(title: str) -> tuple[str, str] | None:
    t = title.lower()
    if "fomc statement" in t:
        return "fomc_statement", "high"
    if t.startswith("minutes of the federal open market committee"):
        return "fomc_minutes", "medium"
    if "economic projections" in t:
        return "fomc_projections", "medium"
    return None


def parse_press(text: str, since: date) -> list[Event]:
    events = []
    for item in _load_feed(text):
        if item.get("pt") != "Monetary Policy":
            continue
        kind = classify_press(htmllib.unescape(item["t"]))
        when = _fed_datetime(item["d"])
        if kind is None or when is None or when.date() < since:
            continue
        link = item.get("l") or ""
        # Un communique sorti a l'heure habituelle du FOMC correspond a une reunion du
        # calendrier, connue a l'avance. A toute autre heure (decision d'urgence), rien
        # ne permet de dire qu'il etait attendu : il n'est visible qu'une fois paru.
        usual = when.astimezone(ET).time() == STATEMENT_TIME_ET
        notice = "scheduled" if kind[0] != "fomc_statement" or usual else "unscheduled"
        events.append(Event(event_key=f"fed_press|{link or iso(when)}", source="fed", kind=kind[0],
                            title=htmllib.unescape(item["t"]), scheduled_at=iso(when),
                            time_basis="published", importance=kind[1], advance_notice=notice,
                            url=BASE + link if link else None))
    return events


def parse_speeches(text: str, since: date) -> list[Event]:
    events = []
    for item in _load_feed(text):
        when = _fed_datetime(item["d"])
        if when is None or when.date() < since:
            continue
        speaker = htmllib.unescape(item.get("s") or "").strip()
        # Le president de la Fed est repere par son titre, pas par son nom.
        importance = "high" if CHAIR_TITLE.match(speaker) else "medium"
        link = item.get("l") or item.get("stub") or ""
        events.append(Event(# Un meme texte peut etre prononce deux fois le meme jour : l'heure
                            # fait partie de la cle pour ne perdre aucune intervention.
                            event_key=f"fed_speech|{link or speaker}|{iso(when)}", source="fed",
                            kind="fed_speech", title=f"{speaker} : {htmllib.unescape(item['t'])}",
                            scheduled_at=iso(when), time_basis="published", importance=importance,
                            # Le flux ne liste un discours qu'une fois prononce : on ignore
                            # depuis quand il etait annonce.
                            advance_notice="unscheduled", url=BASE + link if link else None))
    return events


def parse_h15(text: str) -> dict[str, list[tuple[str, float]]]:
    out: dict[str, list[tuple[str, float]]] = {sid: [] for sid in H15_MAP.values()}
    columns: list[str] | None = None
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        if row[0] == "Time Period":
            columns = row
            continue
        if columns and re.fullmatch(r"\d{4}-\d{2}-\d{2}", row[0]):
            for code, cell in zip(columns[1:], row[1:]):
                if code in H15_MAP and cell not in ("", "ND", "NC"):
                    out[H15_MAP[code]].append((row[0], float(cell)))
    if columns is None:
        raise ParseError("en-tete 'Time Period' absent du CSV H.15")
    return out


class FedConnector(Connector):
    name = "fed"

    def check(self) -> CheckResult:
        try:
            res = self.http.get("fed", FOMC_CALENDAR, ext="html")
            meetings = parse_fomc_calendar(res.text)
            nxt = next((m for m in meetings if m["end"] >= date.today().isoformat()), None)
            return CheckResult("fed", True, f"{len(meetings)} reunions FOMC lues, prochaine fin de reunion : "
                               f"{nxt['end'] if nxt else 'non publiee'}")
        except (SourceUnavailable, ParseError) as exc:
            return CheckResult("fed", False, str(exc))

    def collect(self, since: date) -> CollectReport:
        report = CollectReport("fed")
        for step in (self.collect_calendar, self.collect_press, self.collect_speeches):
            try:
                report.events_upserted += step(since)
            except (SourceUnavailable, ParseError) as exc:
                report.errors.append(f"{step.__name__}: {exc}")
        try:
            report.rows_added += self.collect_h15(since)
        except (SourceUnavailable, ParseError) as exc:
            report.errors.append(f"collect_h15: {exc}")
        return report

    def collect_calendar(self, since: date) -> int:
        res = self.http.get("fed", FOMC_CALENDAR, ext="html")
        events = []
        for m in parse_fomc_calendar(res.text):
            if m["end"] < since.isoformat() or "notation" in m["note"].lower():
                continue
            when = local_to_utc(date.fromisoformat(m["end"]), STATEMENT_TIME_ET, ET)
            title = "Decision du FOMC" + (" avec projections economiques" if m["projections"] else "")
            if m["note"]:
                title += f" ({m['note']})"
            events.append(Event(event_key=f"fomc_meeting|{m['end']}", source="fed", kind="fomc_decision",
                                title=title, scheduled_at=iso(when), time_basis="estimated",
                                importance="high", url=FOMC_CALENDAR, fetch_id=res.fetch_id))
        return self.store.upsert_events(events, iso(res.fetched_at))

    def collect_press(self, since: date) -> int:
        res = self.http.get("fed", PRESS_JSON, ext="json")
        return self.store.upsert_events(parse_press(res.text, since), iso(res.fetched_at))

    def collect_speeches(self, since: date) -> int:
        res = self.http.get("fed", SPEECHES_JSON, ext="json")
        return self.store.upsert_events(parse_speeches(res.text, since), iso(res.fetched_at))

    def collect_h15(self, since: date) -> int:
        res = self.http.get("fed", H15_CSV, params={**H15_PARAMS, "from": since.strftime("%m/%d/%Y"),
                                                    "to": date.today().strftime("%m/%d/%Y")}, ext="csv")
        rows = []
        for series_id, points in parse_h15(res.text).items():
            known = self.store.latest_values(series_id)
            first_load = not known
            for obs_date, value in points:
                if obs_date in known:
                    continue
                # Le H.15 publie la valeur d'un jour le jour ouvre suivant vers 16 h 15 (New York).
                rule = local_to_utc(add_business_days(date.fromisoformat(obs_date), 1), time(18, 0), ET)
                available, basis, quality = availability(rule, res.fetched_at)
                kind = "initial" if basis == "observed" and not first_load else "backfill"
                rows.append(Observation(series_id, obs_date, available[:10], value, available,
                                        basis, "fed_h15", res.fetch_id, release_kind=kind,
                                        time_quality=quality))
        return self.store.add_observations(rows)
