"""CFTC : rapport hebdomadaire Commitments of Traders, via l'API publique
(publicreporting.cftc.gov, sans cle).

Jeu de donnees : rapport "disaggregated", contrats a terme seuls, or du COMEX
(code de marche 088691). Les positions sont arretees le mardi et publiees en
general le vendredi a 15 h 30 (New York), plus tard en cas de jour ferie.
"""
from __future__ import annotations

import json
from datetime import date, time, timedelta

from ..errors import ParseError, SourceUnavailable
from ..store import Observation
from ..timeutil import ET, iso, local_to_utc
from ..release_times import apply_cftc_published_times
from .base import CheckResult, CollectReport, Connector
from .fred import availability

API = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
# Rapport "legacy" : sa position nette non commerciale est la valeur que publie le
# calendrier MT5. Elle sert a retrouver l'heure reelle de publication de chaque rapport.
LEGACY_API = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
NONCOMM_NET = "COT_GOLD_NONCOMM_NET"
GOLD_CODE = "088691"
RELEASE_TIME_ET = time(15, 30)
FIELDS = {
    "COT_GOLD_OPEN_INTEREST": "open_interest_all",
    "COT_GOLD_MM_LONG": "m_money_positions_long_all",
    "COT_GOLD_MM_SHORT": "m_money_positions_short_all",
}


def parse_cot(text: str) -> list[dict]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"reponse CFTC illisible : {exc}") from exc
    if not isinstance(data, list):
        raise ParseError(f"reponse CFTC inattendue : {str(data)[:120]}")
    out = []
    for row in data:
        try:
            item = {"report_date": row["report_date_as_yyyy_mm_dd"][:10]}
            for series_id, field in FIELDS.items():
                item[series_id] = float(row[field])
        except (KeyError, ValueError):
            continue  # ligne incomplete : ignoree, jamais completee
        item["COT_GOLD_MM_NET"] = item["COT_GOLD_MM_LONG"] - item["COT_GOLD_MM_SHORT"]
        out.append(item)
    return sorted(out, key=lambda r: r["report_date"])


def parse_legacy(text: str) -> list[tuple[str, float]]:
    """[(date du rapport, position nette non commerciale)] du rapport "legacy"."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"reponse CFTC illisible : {exc}") from exc
    if not isinstance(data, list):
        raise ParseError(f"reponse CFTC inattendue : {str(data)[:120]}")
    out = []
    for row in data:
        try:
            out.append((row["report_date_as_yyyy_mm_dd"][:10],
                        float(row["noncomm_positions_long_all"]) - float(row["noncomm_positions_short_all"])))
        except (KeyError, ValueError):
            continue
    return sorted(out)


class CftcConnector(Connector):
    name = "cftc"

    def _fetch(self, since: date, limit: int = 5000, api: str = API):
        return self.http.get("cftc", api, params={
            "cftc_contract_market_code": GOLD_CODE,
            "$where": f"report_date_as_yyyy_mm_dd >= '{since.isoformat()}T00:00:00.000'",
            "$order": "report_date_as_yyyy_mm_dd ASC", "$limit": limit}, ext="json")

    def check(self) -> CheckResult:
        try:
            rows = parse_cot(self._fetch(date.today() - timedelta(days=30)).text)
            last = rows[-1]
            return CheckResult("cftc", True, f"rapport du {last['report_date']}, position nette des "
                               f"gerants sur l'or : {last['COT_GOLD_MM_NET']:.0f} contrats")
        except (SourceUnavailable, ParseError, IndexError) as exc:
            return CheckResult("cftc", False, str(exc))

    def collect(self, since: date) -> CollectReport:
        report = CollectReport("cftc")
        try:
            res = self._fetch(since)
            reports = parse_cot(res.text)
        except (SourceUnavailable, ParseError) as exc:
            report.errors.append(str(exc))
            return report
        rows = []
        known = {sid: self.store.latest_values(sid) for sid in [*FIELDS, "COT_GOLD_MM_NET"]}
        first_load = {sid: not values for sid, values in known.items()}
        for item in reports:
            d = date.fromisoformat(item["report_date"])
            # Mardi -> vendredi 15 h 30. Marge d'un jour pour les semaines a jour ferie.
            rule = local_to_utc(d + timedelta(days=4), RELEASE_TIME_ET, ET)
            available, basis, quality = availability(rule, res.fetched_at)
            for series_id in known:
                if item["report_date"] not in known[series_id]:
                    kind = "initial" if basis == "observed" and not first_load[series_id] else "backfill"
                    rows.append(Observation(series_id, item["report_date"], available[:10],
                                            item[series_id], available, basis, "cftc", res.fetch_id,
                                            release_kind=kind, time_quality=quality))
        report.rows_added = self.store.add_observations(rows)
        report.rows_added += self._collect_legacy(since, report)
        refined = apply_cftc_published_times(self.store)
        if refined["matched"]:
            report.notes.append(f"heures reelles du calendrier MT5 : {refined['matched']} rapports dates, "
                                f"{refined['unmatched']} sans correspondance (regle conservee)")
        return report

    def _collect_legacy(self, since: date, report: CollectReport) -> int:
        try:
            res = self._fetch(since, api=LEGACY_API)
            points = parse_legacy(res.text)
        except (SourceUnavailable, ParseError) as exc:
            report.notes.append(f"rapport legacy indisponible : {exc}")
            return 0
        known = self.store.latest_values(NONCOMM_NET)
        first_load = not known
        rows = []
        for report_date, value in points:
            if report_date in known:
                continue
            rule = local_to_utc(date.fromisoformat(report_date) + timedelta(days=4), RELEASE_TIME_ET, ET)
            available, basis, quality = availability(rule, res.fetched_at)
            kind = "initial" if basis == "observed" and not first_load else "backfill"
            rows.append(Observation(NONCOMM_NET, report_date, available[:10], value, available, basis,
                                    "cftc", res.fetch_id, release_kind=kind, time_quality=quality))
        return self.store.add_observations(rows)
