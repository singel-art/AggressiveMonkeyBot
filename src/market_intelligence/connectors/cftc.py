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
from .base import CheckResult, CollectReport, Connector
from .fred import LIVE_WINDOW

API = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
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


class CftcConnector(Connector):
    name = "cftc"

    def _fetch(self, since: date, limit: int = 5000):
        return self.http.get("cftc", API, params={
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
        for item in reports:
            d = date.fromisoformat(item["report_date"])
            # Mardi -> vendredi 15 h 30. Marge d'un jour pour les semaines a jour ferie.
            rule = local_to_utc(d + timedelta(days=4), RELEASE_TIME_ET, ET)
            if res.fetched_at <= rule + LIVE_WINDOW:
                available, basis = iso(res.fetched_at), "observed"
            else:
                available, basis = iso(rule), "rule"
            for series_id in known:
                if item["report_date"] not in known[series_id]:
                    rows.append(Observation(series_id, item["report_date"], available[:10],
                                            item[series_id], available, basis, "cftc", res.fetch_id))
        report.rows_added = self.store.add_observations(rows)
        return report
