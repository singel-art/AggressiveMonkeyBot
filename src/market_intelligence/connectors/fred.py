"""FRED et ALFRED (Federal Reserve Bank of St. Louis), sans cle API.

- fredgraph.csv   : derniere version d'une serie (acces public, CSV).
- ALFRED          : toutes les versions passees d'une serie ("vintages"), avec la
                    date de chaque publication. C'est la base du point-in-time.
- releases/calendar : dates de publication passees et a venir d'une "release".

Constat de l'audit du 2026-11-12 : fred.stlouisfed.org coupe les requetes dont
l'en-tete User-Agent est personnalise. Ce connecteur laisse donc l'en-tete par defaut.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, time, timedelta

from ..catalog import FRED_RELEASES, FRED_SERIES, Series
from ..errors import ParseError, SourceUnavailable
from ..store import Event, Observation
from ..timeutil import CT, ET, add_business_days, iso, local_to_utc
from .base import CheckResult, CollectReport, Connector

FREDGRAPH = "https://fred.stlouisfed.org/graph/fredgraph.csv"
ALFREDGRAPH = "https://alfred.stlouisfed.org/graph/alfredgraph.csv"
ALFRED_VINTAGES = "https://alfred.stlouisfed.org/series/downloaddata"
RELEASE_CALENDAR = "https://fred.stlouisfed.org/releases/calendar"
# Serie representative de chaque publication suivie (identifiant de "release" FRED).
RELEASE_SERIES = {10: "CPIAUCSL", 50: "PAYEMS", 54: "PCEPI"}
VINTAGE_BATCH = 12
# Une valeur vue par le collecteur moins de 3 jours apres sa date theorique de
# disponibilite est une collecte "en direct" : on retient l'instant reel d'observation.
LIVE_WINDOW = timedelta(days=3)


# ----------------------------------------------------------------------- parsing
def parse_fredgraph(text: str) -> list[tuple[str, float]]:
    """CSV 'observation_date,SERIE' -> [(date, valeur)]. Les jours sans cotation sont ignores."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) != 2 or header[0] != "observation_date":
        raise ParseError(f"en-tete fredgraph inattendu : {header}")
    out = []
    for row in reader:
        if len(row) == 2 and row[1] not in ("", "."):
            out.append((row[0], float(row[1])))
    return out


def parse_vintage_dates(html: str) -> list[str]:
    dates = sorted(set(re.findall(r'<option value="(\d{4}-\d{2}-\d{2})">', html)))
    if not dates:
        raise ParseError("aucune date de version trouvee dans la page ALFRED")
    return dates


def parse_alfredgraph(text: str, series_id: str) -> dict[str, list[tuple[str, float]]]:
    """CSV 'observation_date,SERIE_YYYYMMDD,...' -> {vintage: [(date, valeur)]}."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or header[0] != "observation_date":
        raise ParseError(f"en-tete alfredgraph inattendu : {header}")
    vintages = []
    for col in header[1:]:
        m = re.fullmatch(rf"{re.escape(series_id)}_(\d{{4}})(\d{{2}})(\d{{2}})", col)
        if not m:
            raise ParseError(f"colonne ALFRED inattendue : {col}")
        vintages.append("-".join(m.groups()))
    out: dict[str, list[tuple[str, float]]] = {v: [] for v in vintages}
    for row in reader:
        for v, cell in zip(vintages, row[1:]):
            if cell not in ("", "."):
                out[v].append((row[0], float(cell)))
    return out


def parse_release_calendar(html: str) -> list[tuple[str, str, int]]:
    """Page du calendrier FRED -> [(date ISO, heure 'H:MM am/pm' en heure de Chicago, id release)]."""
    pattern = re.compile(
        r'font-weight: bold;">\s*\w+ (\w+ \d{1,2}, \d{4})\s*</span>'
        r'|text-align:right">\s*(\d{1,2}:\d{2} [ap]m)\s*</td>\s*<td[^>]*>\s*<a href="/release\?rid=(\d+)">')
    out, current = [], None
    for m in pattern.finditer(html):
        if m.group(1):
            current = datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
        elif current:
            out.append((current, m.group(2), int(m.group(3))))
    return out


# ------------------------------------------------------- regles de disponibilite
def market_rule_time(series: Series, obs_date: str) -> datetime:
    d = add_business_days(date.fromisoformat(obs_date), series.lag_business_days)
    return local_to_utc(d, series.available_time_et, ET)


def market_availability(series: Series, obs_date: str, fetched_at: datetime) -> tuple[str, str]:
    """Date a partir de laquelle une valeur quotidienne est tenue pour connue."""
    rule = market_rule_time(series, obs_date)
    if fetched_at <= rule + LIVE_WINDOW:
        return iso(fetched_at), "observed"
    return iso(rule), "rule"


def compact_vintages(series: Series, by_vintage: dict[str, list[tuple[str, float]]],
                     known: dict[str, float], obs_start: str, fetch_id: int) -> list[Observation]:
    """Ne garde une ligne que lorsqu'un chiffre apparait ou change d'une version a la suivante."""
    rows = []
    for vintage in sorted(by_vintage):
        available = iso(local_to_utc(date.fromisoformat(vintage), series.release_time_et, ET))
        for obs_date, value in by_vintage[vintage]:
            if obs_date < obs_start or known.get(obs_date) == value:
                continue
            known[obs_date] = value
            rows.append(Observation(series.series_id, obs_date, vintage, value, available,
                                    "vintage", "alfred", fetch_id))
    return rows


class FredConnector(Connector):
    name = "fred"

    def check(self) -> CheckResult:
        try:
            res = self.http.get("fred", FREDGRAPH, params={"id": "DGS10", "cosd": "2026-01-01"}, ext="csv")
            obs = parse_fredgraph(res.text)
            return CheckResult("fred", True, f"fredgraph.csv sans cle, derniere DGS10 : {obs[-1][0]} = {obs[-1][1]}")
        except (SourceUnavailable, ParseError, IndexError) as exc:
            return CheckResult("fred", False, str(exc))

    def collect(self, since: date) -> CollectReport:
        report = CollectReport("fred")
        for series in FRED_SERIES:
            try:
                if series.kind == "market":
                    report.rows_added += self.collect_market(series, since)
                else:
                    report.rows_added += self.collect_macro(series, since)
            except (SourceUnavailable, ParseError) as exc:
                report.errors.append(f"{series.series_id}: {exc}")
        try:
            report.events_upserted += self.collect_release_history(since)
            report.events_upserted += self.collect_release_calendar()
        except (SourceUnavailable, ParseError) as exc:
            report.errors.append(f"calendrier: {exc}")
        return report

    # -- series quotidiennes
    def collect_market(self, series: Series, since: date) -> int:
        res = self.http.get("fred", FREDGRAPH, params={"id": series.series_id, "cosd": since.isoformat()}, ext="csv")
        known = self.store.latest_values(series.series_id)
        rows = []
        for obs_date, value in parse_fredgraph(res.text):
            if obs_date not in known:
                available, basis = market_availability(series, obs_date, res.fetched_at)
                rows.append(Observation(series.series_id, obs_date, available[:10], value,
                                        available, basis, "fred", res.fetch_id))
            elif known[obs_date] != value:
                # Revision detectee : nouvelle version, connue seulement a partir de maintenant.
                now = iso(res.fetched_at)
                rows.append(Observation(series.series_id, obs_date, now[:10], value, now,
                                        "observed", "fred", res.fetch_id))
        return self.store.add_observations(rows)

    # -- series revisees
    def vintage_dates(self, series_id: str) -> list[str]:
        res = self.http.get("alfred", ALFRED_VINTAGES, params={"seid": series_id}, ext="html")
        return parse_vintage_dates(res.text)

    def collect_macro(self, series: Series, since: date) -> int:
        # Deux ans de recul sur les observations : de quoi calculer des variations annuelles
        # des la premiere date de version demandee.
        obs_start = date(since.year - 2, 1, 1).isoformat()
        stored = {r["vintage_date"] for r in self.store.db.execute(
            "SELECT DISTINCT vintage_date FROM observations WHERE series_id=?", (series.series_id,))}
        wanted = [v for v in self.vintage_dates(series.series_id) if v >= since.isoformat()]
        last_stored = max(stored) if stored else ""
        todo = [v for v in wanted if v > last_stored]
        if not todo:
            return 0
        known = self.store.latest_values(series.series_id)
        added = 0
        for i in range(0, len(todo), VINTAGE_BATCH):
            batch = todo[i:i + VINTAGE_BATCH]
            res = self.http.get("alfred", ALFREDGRAPH, params={
                "id": ",".join([series.series_id] * len(batch)),
                "vintage_date": ",".join(batch), "cosd": obs_start}, ext="csv")
            by_vintage = parse_alfredgraph(res.text, series.series_id)
            added += self.store.add_observations(
                compact_vintages(series, by_vintage, known, obs_start, res.fetch_id))
        return added

    # -- historique des dates de publication
    def collect_release_history(self, since: date) -> int:
        """Reconstitue les dates de publication passees a partir des dates de version
        ALFRED d'une serie representative par publication. Sert aux backtests : on sait
        quels jours le CPI, l'emploi ou le PCE sont sortis. L'heure est l'heure usuelle
        (8 h 30 a New York), marquee 'convention'. Une date de version peut aussi
        correspondre a une revision annuelle, pas seulement a la publication mensuelle."""
        events, res = [], None
        for rid, series_id in RELEASE_SERIES.items():
            series = next(s for s in FRED_SERIES if s.series_id == series_id)
            label, importance = FRED_RELEASES[rid]
            res = self.http.get("alfred", ALFRED_VINTAGES, params={"seid": series_id}, ext="html")
            for day in parse_vintage_dates(res.text):
                if day < since.isoformat():
                    continue
                when = local_to_utc(date.fromisoformat(day), series.release_time_et, ET)
                events.append(Event(
                    event_key=f"fred_release|{rid}|{day}", source="alfred", kind="data_release",
                    title=label, scheduled_at=iso(when), time_basis="convention", importance=importance,
                    url=f"https://alfred.stlouisfed.org/series?seid={series_id}", fetch_id=res.fetch_id))
        return self.store.upsert_events(events, iso(res.fetched_at)) if events else 0

    # -- calendrier des publications
    def collect_release_calendar(self, days_back: int = 30, days_ahead: int = 120) -> int:
        today = date.today()
        events, seen_at = [], None
        for rid, (label, importance) in FRED_RELEASES.items():
            res = self.http.get("fred", RELEASE_CALENDAR, params={
                "rid": rid, "vs": (today - timedelta(days=days_back)).isoformat(),
                "ve": (today + timedelta(days=days_ahead)).isoformat(), "view": "year"}, ext="html")
            seen_at = iso(res.fetched_at)
            for day, clock, found_rid in parse_release_calendar(res.text):
                if found_rid != rid:
                    continue
                t = datetime.strptime(clock, "%I:%M %p").time()
                events.append(Event(
                    event_key=f"fred_release|{rid}|{day}", source="fred", kind="data_release",
                    title=label, scheduled_at=iso(local_to_utc(date.fromisoformat(day), t, CT)),
                    # Heure affichee par le calendrier FRED (heure de Chicago). Elle n'est pas
                    # confirmee par l'organisme emetteur : on la marque comme une convention.
                    time_basis="convention", importance=importance,
                    url=f"https://fred.stlouisfed.org/release?rid={rid}", fetch_id=res.fetch_id))
        return self.store.upsert_events(events, seen_at) if events else 0
