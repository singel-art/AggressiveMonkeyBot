"""Connecteur BLS : chemin avec cle, refus, quotas, reponses incorrectes.

Aucun de ces tests ne prouve qu'une cle reelle fonctionne : ils verifient ce que
le connecteur envoie et la facon dont il traite les reponses, a partir de
reponses reelles enregistrees le 2026-11-12.
"""
import json
from dataclasses import replace
from datetime import date

import pytest
import requests

from conftest import FakeHttp
from market_intelligence.connectors.bls import BlsConnector, parse_bls
from market_intelligence.errors import ParseError, SourceRefused
from market_intelligence.http import HttpClient

KEY = "KEY_SENT_IN_TEST"


def test_key_is_sent_in_the_request_body_when_configured(store, settings):
    http = FakeHttp(store, [("api.bls.gov", "bls_v2.json")])
    BlsConnector(http, store, replace(settings, bls_api_key=KEY)).collect(date(2025, 1, 1))
    call = http.calls[0]
    assert call["method"] == "POST" and call["payload"]["registrationkey"] == KEY
    assert KEY not in call["url"]                       # jamais dans l'URL
    assert set(call["payload"]["seriesid"]) == {"CUSR0000SA0", "CES0000000001", "LNS14000000"}


def test_no_key_field_is_sent_without_configuration(store, settings):
    http = FakeHttp(store, [("api.bls.gov", "bls_v2.json")])
    BlsConnector(http, store, settings).collect(date(2025, 1, 1))
    assert "registrationkey" not in http.calls[0]["payload"]


def test_collect_stores_values_with_observed_availability(store, settings):
    http = FakeHttp(store, [("api.bls.gov", "bls_v2.json")], now="2026-11-12T09:00:00Z")
    report = BlsConnector(http, store, settings).collect(date(2025, 1, 1))
    assert report.ok and report.rows_added > 60
    row = store.asof("BLS:LNS14000000", "2026-11-12T09:00:00Z")[-1]
    assert (row["obs_date"], row["value"]) == ("2026-10-01", 4.1)
    assert row["available_at"] == "2026-11-12T09:00:00Z" and row["time_quality"] == "observed"
    assert row["release_kind"] == "backfill"            # premier chargement : nature inconnue
    # Jamais visible avant l'instant de la collecte : inutilisable en look-ahead.
    assert store.asof("BLS:LNS14000000", "2026-11-12T08:59:59Z") == []
    # Rejouer la meme reponse n'ajoute rien.
    assert BlsConnector(http, store, settings).collect(date(2025, 1, 1)).rows_added == 0


def test_quota_refusal_is_reported_and_stores_nothing(store, settings):
    http = FakeHttp(store, [("api.bls.gov", "bls_v2_quota_reached.json")])
    connector = BlsConnector(http, store, settings)
    report = connector.collect(date(2025, 1, 1))
    assert not report.ok and "quota" in report.errors[0] and report.rows_added == 0
    assert store.series_summary() == []
    check = connector.check()
    assert not check.ok and "quota" in check.detail and check.needs_key


def test_invalid_key_is_reported_without_leaking_the_key(store, settings, fixture_text):
    with pytest.raises(SourceRefused) as err:
        parse_bls(fixture_text("bls_v2_invalid_key.json"), KEY)
    assert err.value.code == "invalid_key" and KEY not in str(err.value)
    http = FakeHttp(store, [("api.bls.gov", "bls_v2_invalid_key.json")])
    connector = BlsConnector(http, store, replace(settings, bls_api_key=KEY))
    report = connector.collect(date(2025, 1, 1))
    assert not report.ok and KEY not in " ".join(report.errors)
    check = connector.check()
    assert not check.ok and "refusee" in check.detail and KEY not in check.detail


def test_unknown_series_message_is_surfaced(fixture_text):
    parsed, messages = parse_bls(fixture_text("bls_v2_invalid_series.json"))
    assert messages == ["Invalid Series for Series ZZZINVALID"]
    assert parsed["LNS14000000"] and parsed["ZZZINVALID"] == []      # serie inconnue : vide, jamais completee


def test_missing_series_in_a_successful_answer_is_an_error(store, settings):
    # La reponse ne contient que le chomage : les deux autres series attendues manquent.
    http = FakeHttp(store, [("api.bls.gov", "bls_v2_invalid_series.json")])
    report = BlsConnector(http, store, settings).collect(date(2025, 1, 1))
    assert len(report.errors) == 2 and any("Invalid Series" in n for n in report.notes)


@pytest.mark.parametrize("body", ["", "<html>Access Denied</html>", "[]", '{"Results": {}}',
                                  '{"status": "REQUEST_SUCCEEDED"}',
                                  '{"status": "REQUEST_SUCCEEDED", "Results": {"series": "x"}}'])
def test_malformed_answers_raise_parse_error(body):
    with pytest.raises(ParseError):
        parse_bls(body)


def test_non_numeric_values_are_skipped_not_replaced():
    body = json.dumps({"status": "REQUEST_SUCCEEDED", "message": [], "Results": {"series": [
        {"seriesID": "S", "data": [{"year": "2025", "period": "M10", "value": "-", "footnotes": [{}]},
                                    {"year": "2025", "period": "M13", "value": "4.0", "footnotes": [{}]}]}]}})
    parsed, _ = parse_bls(body)
    assert parsed == {"S": []}


class _EchoSession:
    """Simule une source qui recopie la cle dans sa reponse, comme le fait le BLS."""

    def request(self, method, url, **kwargs):
        resp = requests.Response()
        resp.status_code = 200
        resp._content = json.dumps({"echo": kwargs["json"]["registrationkey"]}).encode()
        return resp


def test_key_echoed_by_the_source_is_masked_in_the_archive(store, tmp_path):
    http = HttpClient(store, tmp_path, retries=1, min_interval=0, session=_EchoSession())
    res = http.post_json("bls", "https://api.bls.gov/publicAPI/v2/timeseries/data/",
                         {"registrationkey": KEY}, secrets=(KEY,))
    assert KEY not in res.text and "***" in res.text
    assert KEY.encode() not in res.raw_path.read_bytes()
    assert KEY not in store.last_fetch("bls")["url"]
