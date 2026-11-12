"""Calendrier economique de MetaTrader 5.

Le calendrier integre au terminal est la seule source gratuite du projet qui
fournit a la fois l'heure exacte, la prevision, la valeur publiee et la valeur
precedente revisee. Il n'est lisible que depuis le terminal : le script
mt5/Scripts/AMB_ExportCalendar.mq5 l'exporte en CSV, et ce module importe ce CSV.

Format attendu (separateur ';', UTF-8, une ligne d'en-tete) :
value_id;event_id;time_utc;currency;importance;name;actual;forecast;previous;revised_previous;unit;time_server

`time_server` (heure du serveur de trading) est conserve pour pouvoir recontroler
la conversion en UTC, notamment autour des changements d'heure.

Ce lecteur n'a pas encore ete valide sur un export reel : le script MQL5 ne peut
etre compile et lance que sur votre poste.
"""
from __future__ import annotations

import csv
import hashlib
from datetime import date
from pathlib import Path

from ..errors import ParseError
from ..store import Event
from ..timeutil import iso, now_utc, parse_iso
from .base import CheckResult, CollectReport, Connector

COLUMNS = ["value_id", "event_id", "time_utc", "currency", "importance", "name",
           "actual", "forecast", "previous", "revised_previous", "unit", "time_server"]
IMPORTANCE = {"3": "high", "2": "medium", "1": "low", "0": "low"}


def parse_calendar_csv(path: Path) -> list[Event]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        if reader.fieldnames != COLUMNS:
            raise ParseError(f"colonnes inattendues dans {path.name} : {reader.fieldnames}")
        events = []
        for row in reader:
            when = parse_iso(row["time_utc"].replace(" ", "T").replace(".", "-"))
            unit = f" {row['unit']}" if row["unit"] else ""
            events.append(Event(
                event_key=f"mt5|{row['value_id']}", source="mt5_calendar", kind="data_release",
                title=row["name"], scheduled_at=iso(when), time_basis="published",
                importance=IMPORTANCE.get(row["importance"], "low"), currency=row["currency"] or None,
                actual=(row["actual"] + unit) if row["actual"] else None,
                forecast=(row["forecast"] + unit) if row["forecast"] else None,
                previous=(row["previous"] + unit) if row["previous"] else None,
                revised_previous=(row["revised_previous"] + unit) if row["revised_previous"] else None))
    return events


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
            except (ParseError, KeyError, ValueError) as exc:
                report.errors.append(f"{path.name}: {exc}")
                continue
            target = self.settings.raw_dir / "mt5_calendar" / now.strftime("%Y/%m/%d") / f"{digest[:12]}_{path.name}"
            target.parent.mkdir(parents=True, exist_ok=True)
            path.replace(target)
            self.store.log_fetch(source="mt5_calendar", url=f"depot manuel : {path.name}", fetched_at=iso(now),
                                 status=200, sha256=digest, raw_path=str(target), error=None)
            report.events_upserted += self.store.upsert_events(events, iso(now))
        if not report.events_upserted and not report.errors:
            report.notes.append("aucun export a importer")
        return report
