"""Bureau of Labor Statistics : API publique (api.bls.gov).

Role dans le projet : recouper a la source les derniers chiffres lus sur FRED
(CPI, emploi, chomage). L'API ne donne ni la date de publication ni les versions
passees : elle ne sert donc pas aux backtests, seulement a la verification.

Sans cle : version 2 interrogeable, avec des quotas journaliers reduits.
Avec une cle gratuite (variable d'environnement BLS_API_KEY) : quotas plus larges.
Les pages www.bls.gov et download.bls.gov refusent les robots (HTTP 403 constate
le 2026-11-12) : ce connecteur ne les utilise pas.
"""
from __future__ import annotations

import json
from datetime import date

from ..catalog import BLS_CROSSCHECK
from ..errors import ParseError, SourceUnavailable
from ..store import Observation
from ..timeutil import iso
from .base import CheckResult, CollectReport, Connector

API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"


def parse_bls(text: str) -> dict[str, list[tuple[str, float, bool]]]:
    """Reponse JSON -> {serie: [(mois 'YYYY-MM-01', valeur, provisoire)]}."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"reponse BLS illisible : {exc}") from exc
    if data.get("status") != "REQUEST_SUCCEEDED":
        raise ParseError(f"BLS a refuse la requete : {data.get('status')} {data.get('message')}")
    out: dict[str, list[tuple[str, float, bool]]] = {}
    for series in data.get("Results", {}).get("series", []):
        points = []
        for item in series.get("data", []):
            period = item.get("period", "")
            if not (period.startswith("M") and period != "M13"):
                continue
            try:
                value = float(item["value"])
            except (KeyError, ValueError):
                continue  # valeur manquante ("-") : on ne remplace pas par un nombre
            preliminary = any(f.get("code") == "P" for f in item.get("footnotes", []) if f)
            points.append((f"{item['year']}-{period[1:]}-01", value, preliminary))
        out[series["seriesID"]] = sorted(points)
    return out


class BlsConnector(Connector):
    name = "bls"

    def _fetch(self, series_ids: list[str], start_year: int, end_year: int):
        payload = {"seriesid": series_ids, "startyear": str(start_year), "endyear": str(end_year)}
        if self.settings.bls_api_key:
            payload["registrationkey"] = self.settings.bls_api_key
        return self.http.post_json("bls", API, payload)

    def check(self) -> CheckResult:
        try:
            year = date.today().year
            res = self._fetch(["LNS14000000"], year, year)
            points = parse_bls(res.text)["LNS14000000"]
            return CheckResult("bls", True, f"API v2 {'avec' if self.settings.bls_api_key else 'sans'} cle, "
                               f"dernier taux de chomage : {points[-1][0][:7]} = {points[-1][1]}")
        except (SourceUnavailable, ParseError, KeyError, IndexError) as exc:
            return CheckResult("bls", False, str(exc))

    def collect(self, since: date) -> CollectReport:
        """Ne recupere que les deux dernieres annees : c'est un controle, pas un historique."""
        report = CollectReport("bls")
        year = date.today().year
        try:
            res = self._fetch(list(BLS_CROSSCHECK), year - 1, year)
            parsed = parse_bls(res.text)
        except (SourceUnavailable, ParseError) as exc:
            report.errors.append(str(exc))
            return report
        seen = iso(res.fetched_at)
        rows = []
        for series_id, points in parsed.items():
            known = self.store.latest_values(f"BLS:{series_id}")
            for month, value, _preliminary in points:
                if known.get(month) != value:
                    # Date de disponibilite = instant de notre collecte : cette serie ne peut
                    # donc jamais faire entrer une information trop tot dans un backtest.
                    rows.append(Observation(f"BLS:{series_id}", month, seen[:10], value, seen,
                                            "observed", "bls", res.fetch_id))
        report.rows_added = self.store.add_observations(rows)
        return report
