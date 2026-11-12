"""FRED et ALFRED (Federal Reserve Bank of St. Louis), sans cle API.

- fredgraph.csv   : derniere version d'une serie (acces public, CSV).
- ALFRED          : toutes les versions passees d'une serie ("vintages"), datees.
                    C'est la base du point-in-time. Une date de version ALFRED est
                    le jour ou FRED a enregistre la version : elle n'est PAS
                    assimilee d'office a une publication economique. Une version
                    n'est classee "publication" que si elle apporte une periode
                    nouvelle ; sinon c'est une revision.
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
from ..store import Event, Observation, release_kind
from ..timeutil import CT, ET, add_business_days, iso, local_to_utc, now_utc
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


def availability(rule: datetime, fetched_at: datetime) -> tuple[str, str, str]:
    """(instant, base, qualite de l'heure) pour une valeur sans date de version.
    Vue par le collecteur peu apres sa date theorique : instant reel d'observation.
    Sinon (historique charge apres coup) : la regle, dont l'heure est une estimation."""
    if fetched_at <= rule + LIVE_WINDOW:
        return iso(fetched_at), "observed", "observed"
    return iso(rule), "rule", "estimated"


def market_availability(series: Series, obs_date: str, fetched_at: datetime) -> tuple[str, str, str]:
    """Instant a partir duquel une valeur quotidienne est tenue pour connue."""
    return availability(market_rule_time(series, obs_date), fetched_at)


def vintage_times(series: Series, vintage: str) -> tuple[str, str]:
    """(heure estimee, borne sure) d'une version ALFRED.
    L'heure usuelle de publication est une estimation non verifiee. La borne sure
    est la fin du jour de la version a New York : avant elle, un backtest ne voit
    pas la valeur."""
    d = date.fromisoformat(vintage)
    estimated = local_to_utc(d, series.release_time_et, ET)
    safe = local_to_utc(d + timedelta(days=1), time(0, 0), ET)
    return iso(estimated), iso(safe)


def compact_vintages(series: Series, by_vintage: dict[str, list[tuple[str, float]]],
                     known: dict[str, float], obs_start: str, fetch_id: int,
                     first_load: bool | None = None) -> list[Observation]:
    """Ne garde une ligne que lorsqu'un chiffre apparait ou change d'une version a la suivante.

    Chaque ligne est classee : 'initial' (periode nouvelle), 'revision' (valeur
    modifiee) ou 'backfill' (contenu de la toute premiere version chargee, dont on
    ne peut pas dire s'il est initial ou deja revise)."""
    if first_load is None:
        first_load = not known
    rows = []
    for vintage in sorted(by_vintage):
        estimated, safe = vintage_times(series, vintage)
        for obs_date, value in by_vintage[vintage]:
            if obs_date < obs_start or known.get(obs_date) == value:
                continue
            kind = release_kind(known, obs_date, first_load)
            known[obs_date] = value
            rows.append(Observation(series.series_id, obs_date, vintage, value, estimated,
                                    "vintage", "alfred", fetch_id, release_kind=kind,
                                    time_quality="estimated", available_at_safe=safe))
        first_load = False
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
        first_load = not known
        rows = []
        for obs_date, value in parse_fredgraph(res.text):
            if obs_date not in known:
                available, basis, quality = market_availability(series, obs_date, res.fetched_at)
                # Une valeur d'historique recuperee apres coup peut avoir ete revisee
                # depuis sa parution : elle n'est pas presentee comme initiale.
                kind = "initial" if basis == "observed" and not first_load else "backfill"
                rows.append(Observation(series.series_id, obs_date, available[:10], value,
                                        available, basis, "fred", res.fetch_id,
                                        release_kind=kind, time_quality=quality))
            elif known[obs_date] != value:
                # Revision detectee : nouvelle version, connue seulement a partir de maintenant.
                now = iso(res.fetched_at)
                rows.append(Observation(series.series_id, obs_date, now[:10], value, now,
                                        "observed", "fred", res.fetch_id,
                                        release_kind="revision", time_quality="observed"))
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
        first_load = not known
        added = 0
        for i in range(0, len(todo), VINTAGE_BATCH):
            batch = todo[i:i + VINTAGE_BATCH]
            res = self.http.get("alfred", ALFREDGRAPH, params={
                "id": ",".join([series.series_id] * len(batch)),
                "vintage_date": ",".join(batch), "cosd": obs_start}, ext="csv")
            by_vintage = parse_alfredgraph(res.text, series.series_id)
            added += self.store.add_observations(
                compact_vintages(series, by_vintage, known, obs_start, res.fetch_id, first_load))
            first_load = False
        return added

    # -- historique des publications et des revisions
    def collect_release_history(self, since: date) -> int:
        """Reconstitue les evenements passes a partir des versions ALFRED deja stockees
        d'une serie representative par publication.

        Une version n'est pas une publication par nature. Elle est classee d'apres
        son contenu :
        - elle apporte au moins une periode nouvelle -> 'data_release' ;
        - elle ne fait que modifier des periodes deja publiees -> 'data_revision' ;
        - c'est la premiere version chargee -> ignoree, sa nature est inconnue.
        L'heure est l'heure usuelle de la publication, marquee 'estimated'."""
        events, seen_at = [], iso(now_utc())
        for rid, series_id in RELEASE_SERIES.items():
            series = next(s for s in FRED_SERIES if s.series_id == series_id)
            label, importance = FRED_RELEASES[rid]
            url = f"https://alfred.stlouisfed.org/series?seid={series_id}"
            for v in self.store.vintages(series_id):
                day = v["vintage_date"]
                if day < since.isoformat() or not (v["n_initial"] or v["n_revision"]):
                    continue
                if v["n_initial"]:
                    events.append(Event(
                        event_key=f"fred_release|{rid}|{day}", source="alfred", kind="data_release",
                        title=label, scheduled_at=v["available_at"], time_basis="estimated",
                        importance=importance, url=url, fetch_id=v["fetch_id"]))
                else:
                    events.append(Event(
                        event_key=f"alfred_revision|{series_id}|{day}", source="alfred",
                        kind="data_revision", title=f"Revision sans periode nouvelle : {label}",
                        scheduled_at=v["available_at"], time_basis="estimated", importance="low",
                        url=url, fetch_id=v["fetch_id"]))
        return self.store.upsert_events(events, seen_at) if events else 0

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
                    # confirmee par l'organisme emetteur : elle reste marquee comme estimee.
                    time_basis="estimated", importance=importance,
                    url=f"https://fred.stlouisfed.org/release?rid={rid}", fetch_id=res.fetch_id))
        return self.store.upsert_events(events, seen_at) if events else 0
