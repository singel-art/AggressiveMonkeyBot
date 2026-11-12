"""Bureau of Labor Statistics : API publique version 2 (api.bls.gov).

Role dans le projet : recouper a la source les derniers chiffres lus sur FRED
(CPI, emploi, chomage). L'API ne donne ni la date de publication ni les versions
passees : elle ne sert donc pas aux backtests, seulement a la verification.

Cle : variable d'environnement BLS_API_KEY (gratuite, sur inscription). Elle est
envoyee dans le corps de la requete, jamais dans l'URL. Sans cle, l'API repond
mais son quota journalier est tres vite atteint (constate le 2026-11-12).

Reponses reelles observees et traitees :
- quota atteint  : status REQUEST_NOT_PROCESSED, "daily threshold ... has been reached" ;
- cle invalide   : status REQUEST_NOT_PROCESSED, "The key:... provided by the User is
                   invalid" (l'API recopie la cle : elle est masquee partout) ;
- serie inconnue : status REQUEST_SUCCEEDED avec un message "Invalid Series for Series X".
Aucune requete n'a pu etre faite avec une cle valide : ce cas n'est verifie que
par des tests hors reseau.

Les pages www.bls.gov et download.bls.gov refusent les robots (HTTP 403 constate
le 2026-11-12) : ce connecteur ne les utilise pas.
"""
from __future__ import annotations

import json
from datetime import date

from ..catalog import BLS_CROSSCHECK
from ..errors import ParseError, SourceRefused, SourceUnavailable
from ..store import Observation, release_kind
from ..timeutil import iso
from .base import CheckResult, CollectReport, Connector

API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"


def _mask(text: str, key: str | None) -> str:
    return text.replace(key, "***") if key else text


def parse_bls(text: str, key: str | None = None) -> tuple[dict[str, list[tuple[str, float, bool]]], list[str]]:
    """Reponse JSON -> ({serie: [(mois 'YYYY-MM-01', valeur, provisoire)]}, messages de l'API).

    Leve SourceRefused si l'API a refuse la requete, ParseError si la reponse n'a
    pas la forme attendue. Aucune valeur n'est jamais deduite ou completee."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"reponse BLS illisible : {exc}") from exc
    if not isinstance(data, dict) or "status" not in data:
        raise ParseError("reponse BLS sans champ 'status'")
    messages = [_mask(str(m), key) for m in (data.get("message") or [])]
    if data["status"] != "REQUEST_SUCCEEDED":
        joined = " ; ".join(messages) or str(data["status"])
        low = joined.lower()
        if "threshold" in low:
            code = "quota"
        elif "key" in low and "invalid" in low:
            code = "invalid_key"
        else:
            code = "refused"
        raise SourceRefused("bls", code, joined)
    results = data.get("Results")
    if not isinstance(results, dict) or not isinstance(results.get("series"), list):
        raise ParseError("reponse BLS sans liste 'Results.series'")
    out: dict[str, list[tuple[str, float, bool]]] = {}
    for series in results["series"]:
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
    return out, messages


class BlsConnector(Connector):
    name = "bls"

    def _fetch(self, series_ids: list[str], start_year: int, end_year: int):
        key = self.settings.bls_api_key
        payload = {"seriesid": series_ids, "startyear": str(start_year), "endyear": str(end_year)}
        if key:
            payload["registrationkey"] = key
        res = self.http.post_json("bls", API, payload, secrets=(key,) if key else ())
        parsed, messages = parse_bls(res.text, key)
        return res, parsed, messages

    def check(self) -> CheckResult:
        mode = "avec cle" if self.settings.bls_api_key else "sans cle"
        try:
            year = date.today().year
            _, parsed, _ = self._fetch(["LNS14000000"], year, year)
            points = parsed["LNS14000000"]
            return CheckResult("bls", True, f"API v2 {mode}, dernier taux de chomage : "
                               f"{points[-1][0][:7]} = {points[-1][1]}")
        except SourceRefused as exc:
            hint = {"quota": "quota journalier atteint" + ("" if self.settings.bls_api_key
                                                           else " ; definir BLS_API_KEY"),
                    "invalid_key": "la cle BLS_API_KEY est refusee par le BLS"}.get(exc.code, exc.message)
            return CheckResult("bls", False, f"API v2 {mode} : {hint}",
                               needs_key=not self.settings.bls_api_key)
        except (SourceUnavailable, ParseError, KeyError, IndexError) as exc:
            return CheckResult("bls", False, str(exc))

    def collect(self, since: date) -> CollectReport:
        """Ne recupere que les deux dernieres annees : c'est un controle, pas un historique."""
        report = CollectReport("bls")
        year = date.today().year
        try:
            res, parsed, messages = self._fetch(list(BLS_CROSSCHECK), year - 1, year)
        except (SourceUnavailable, SourceRefused, ParseError) as exc:
            report.errors.append(str(exc))
            return report
        report.notes.extend(f"message du BLS : {m}" for m in messages)
        for series_id in BLS_CROSSCHECK:
            if not parsed.get(series_id):
                report.errors.append(f"{series_id} : aucune donnee renvoyee")
        seen = iso(res.fetched_at)
        rows = []
        for series_id, points in parsed.items():
            known = self.store.latest_values(f"BLS:{series_id}")
            first_load = not known
            for month, value, _preliminary in points:
                if known.get(month) != value:
                    # Date de disponibilite = instant de notre collecte : cette serie ne peut
                    # donc jamais faire entrer une information trop tot dans un backtest.
                    rows.append(Observation(f"BLS:{series_id}", month, seen[:10], value, seen,
                                            "observed", "bls", res.fetch_id,
                                            release_kind=release_kind(known, month, first_load),
                                            time_quality="observed"))
        report.rows_added = self.store.add_observations(rows)
        return report
