"""Calendrier economique de MetaTrader 5.

Le calendrier integre au terminal est la seule source gratuite du projet qui
fournit a la fois l'heure exacte, la prevision, la valeur publiee et la valeur
precedente revisee. Il n'est lisible que depuis le terminal : le script
mt5/Scripts/AMB_ExportCalendar.mq5 l'exporte en CSV, et ce module importe ce CSV.

Format attendu (separateur ';', UTF-8, une ligne d'en-tete, voir COLUMNS) :
value_id;event_id;time_utc;time_server;utc_offset_s;time_mode;currency;importance;
name;actual;forecast;previous;revised_previous;unit;impact

Regles de lecture :
- `time_utc` est calcule par le script avec l'ecart serveur-UTC du jour de l'export.
  Il peut etre faux d'une heure pour les dates d'une autre saison. `collect` recoupe
  donc les decisions de la Fed avec les communiques dates de la Fed et signale tout
  decalage au lieu de le corriger en silence.
- Une heure n'est tenue pour publiee que si `time_mode` vaut
  CALENDAR_TIMEMODE_DATETIME. Sinon elle est marquee 'estimated'.
- Une valeur absente reste absente : aucun champ vide n'est converti en zero.

Etat : le script MQL5 n'a pas ete compile et aucun export reel n'a ete lu. Les
tests de ce lecteur portent sur le format, pas sur un fichier produit par MT5.
"""
from __future__ import annotations

import csv
import hashlib
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from ..catalog import MT5_FOMC_STATEMENT
from ..errors import ParseError
from ..release_times import apply_cftc_published_times, apply_published_times
from ..store import Event
from ..timeutil import UTC, iso, now_utc, parse_iso
from .base import CheckResult, CollectReport, Connector

COLUMNS = ["value_id", "event_id", "time_utc", "time_server", "utc_offset_s", "time_mode",
           "currency", "importance", "name", "actual", "forecast", "previous",
           "revised_previous", "unit", "impact"]
IMPORTANCE = {"3": "high", "2": "medium", "1": "low", "0": "low"}
EXACT_TIME = "CALENDAR_TIMEMODE_DATETIME"
TIME_MODES = {EXACT_TIME, "CALENDAR_TIMEMODE_DATE", "CALENDAR_TIMEMODE_NOTIME",
              "CALENDAR_TIMEMODE_TENTATIVE"}


def _mt5_time(text: str, line: int) -> datetime:
    try:
        return datetime.strptime(text.strip(), "%Y.%m.%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ParseError(f"ligne {line} : date illisible {text!r}") from exc


def _number(text: str, line: int, field: str) -> str | None:
    text = text.strip()
    if not text:
        return None
    try:
        float(text)
    except ValueError as exc:
        raise ParseError(f"ligne {line} : {field} non numerique {text!r}") from exc
    return text


def parse_calendar_csv(path: Path) -> list[Event]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        if reader.fieldnames != COLUMNS:
            raise ParseError(f"colonnes inattendues dans {path.name} : {reader.fieldnames}")
        events, seen = [], set()
        for line, row in enumerate(reader, start=2):
            if None in row or None in row.values():
                raise ParseError(f"ligne {line} : nombre de colonnes incorrect")
            if not row["value_id"].isdigit():
                raise ParseError(f"ligne {line} : value_id invalide {row['value_id']!r}")
            if row["value_id"] in seen:
                raise ParseError(f"ligne {line} : value_id en double {row['value_id']}")
            seen.add(row["value_id"])
            if row["time_mode"] not in TIME_MODES:
                raise ParseError(f"ligne {line} : time_mode inconnu {row['time_mode']!r}")
            if row["importance"] not in IMPORTANCE:
                raise ParseError(f"ligne {line} : importance inconnue {row['importance']!r}")
            when = _mt5_time(row["time_utc"], line)
            server = _mt5_time(row["time_server"], line)
            try:
                offset = int(row["utc_offset_s"])
            except ValueError as exc:
                raise ParseError(f"ligne {line} : utc_offset_s invalide") from exc
            if (server - when).total_seconds() != offset:
                raise ParseError(f"ligne {line} : time_utc, time_server et utc_offset_s incoherents")
            values = {f: _number(row[f], line, f) for f in ("actual", "forecast", "previous", "revised_previous")}
            unit = row["unit"].split("/")[0].replace("CALENDAR_UNIT_", "").lower()
            suffix = f" [{unit}]" if unit and unit != "none" else ""
            # Jour ferie : journee entiere, sans importance attribuee.
            holiday = row["importance"] == "0" and row["time_mode"] == "CALENDAR_TIMEMODE_DATE"
            events.append(Event(
                # L'identifiant d'evenement, stable d'une langue a l'autre, fait partie de la cle.
                event_key=f"mt5|{row['event_id']}|{row['value_id']}", source="mt5_calendar",
                kind="holiday" if holiday else "data_release",
                title=row["name"].strip(), scheduled_at=iso(when),
                time_basis="published" if row["time_mode"] == EXACT_TIME else "estimated",
                importance=IMPORTANCE[row["importance"]], currency=row["currency"] or None,
                **{k: (v + suffix if v is not None else None) for k, v in values.items()}))
    return events


def check_times_against_fed(events: list[Event], store) -> dict:
    """Recoupe l'heure des communiques du FOMC de l'export avec leur heure reelle deja
    stockee (source : flux de la Fed, heure publiee). L'evenement est reconnu par son
    identifiant MT5, pas par son nom, qui change avec la langue du terminal.

    Renvoie {'compared': n, 'offsets_minutes': {ecart: nombre}}. Un ecart non nul
    (typiquement 60 ou -60) revele une conversion d'heure fausse dans l'export."""
    statements = {r["scheduled_at"][:10]: parse_iso(r["scheduled_at"]) for r in store.db.execute(
        "SELECT scheduled_at FROM events WHERE source='fed' AND kind='fomc_statement' "
        "AND advance_notice='scheduled'")}
    offsets: Counter = Counter()
    for e in events:
        day = e.scheduled_at[:10]
        if e.event_key.startswith(f"mt5|{MT5_FOMC_STATEMENT}|") and day in statements:
            offsets[round((parse_iso(e.scheduled_at) - statements[day]).total_seconds() / 60)] += 1
    return {"compared": sum(offsets.values()), "offsets_minutes": dict(sorted(offsets.items()))}


class Mt5CalendarConnector(Connector):
    name = "mt5_calendar"

    def _inbox(self) -> Path:
        return self.settings.inbox_dir / "mt5_calendar"

    def check(self) -> CheckResult:
        files = list(self._inbox().glob("*.csv")) if self._inbox().exists() else []
        if not files:
            return CheckResult("mt5_calendar", False, "aucun export depose : lancer le script "
                               "AMB_ExportCalendar dans MT5 puis copier le CSV dans data/inbox/mt5_calendar/")
        return CheckResult("mt5_calendar", True, f"{len(files)} export(s) en attente d'import")

    def collect(self, since: date) -> CollectReport:
        report = CollectReport("mt5_calendar")
        self._inbox().mkdir(parents=True, exist_ok=True)
        for path in sorted(self._inbox().glob("*.csv")):
            now = now_utc()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            try:
                events = [e for e in parse_calendar_csv(path) if e.scheduled_at[:10] >= since.isoformat()]
            except ParseError as exc:
                report.errors.append(f"{path.name}: {exc} ; fichier laisse en place, rien n'est importe")
                continue
            control = check_times_against_fed(events, self.store)
            bad = {k: v for k, v in control["offsets_minutes"].items() if k != 0}
            if bad:
                report.errors.append(
                    f"{path.name}: heures decalees par rapport aux communiques de la Fed "
                    f"(ecart en minutes -> nombre : {bad}) ; fichier laisse en place, rien n'est importe")
                continue
            if control["compared"] == 0:
                report.notes.append(f"{path.name}: heures non recoupees (aucun communique du FOMC "
                                    "commun avec le stockage : lancer d'abord la collecte `fed`)")
            else:
                report.notes.append(f"{path.name}: {control['compared']} communiques du FOMC recoupes, "
                                    "heures identiques")
            target = self.settings.raw_dir / "mt5_calendar" / now.strftime("%Y/%m/%d") / f"{digest[:12]}_{path.name}"
            target.parent.mkdir(parents=True, exist_ok=True)
            path.replace(target)
            fetch_id = self.store.log_fetch(source="mt5_calendar", url=f"depot manuel : {path.name}",
                                            fetched_at=iso(now), status=200, sha256=digest,
                                            raw_path=str(target), error=None)
            report.events_upserted += self.store.upsert_events(
                [Event(**{**e.__dict__, "fetch_id": fetch_id}) for e in events], iso(now))
        if not report.events_upserted and not report.errors:
            report.notes.append("aucun export a importer")
        elif report.events_upserted:
            refined = apply_published_times(self.store)
            cot = apply_cftc_published_times(self.store)
            if cot["matched"] or cot["unmatched"]:
                report.notes.append(f"rapports CFTC dates par leur publication reelle : {cot['matched']} ; "
                                    f"sans correspondance : {cot['unmatched']}")
            report.notes.append(f"heures reelles appliquees : {refined['rows_refined']} lignes de series, "
                                f"{refined['releases_matched']} publications ; "
                                f"{len(refined['earlier_than_estimate'])} heure(s) anterieure(s) a "
                                "l'estimation, laissee(s) de cote par prudence")
        return report
