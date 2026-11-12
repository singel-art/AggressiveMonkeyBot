"""Comportement en cas de donnee absente, perimee ou de source en panne."""
import pytest
import requests

from market_intelligence.context import build_context, flat_rows, read_flat, write_context
from market_intelligence.errors import SourceUnavailable
from market_intelligence.http import HttpClient, redact
from market_intelligence.timeutil import iso, parse_iso


def test_empty_store_yields_unavailable_never_numbers(store):
    ctx = build_context(store, parse_iso("2026-11-12T09:00:00Z"))
    assert ctx["status"] == "unavailable"
    assert ctx["rates"]["us10y"]["status"] == "unavailable" and "value" not in ctx["rates"]["us10y"]
    assert ctx["gold_macro_bias"]["label"] == "unavailable" and ctx["gold_macro_bias"]["score"] is None
    assert ctx["events"]["announcement_risk"]["level"] == "unknown"
    flat = {k: (v, s) for k, v, s, _ in flat_rows(ctx)}
    assert flat["us10y_value"] == ("", "unavailable")
    assert flat["risk_level"][0] == "unknown"


def test_old_data_is_flagged_stale_and_excluded(unrate_store):
    ctx = build_context(unrate_store, parse_iso("2027-06-01T00:00:00Z"), historical=True)
    assert ctx["labor"]["unemployment_rate"]["status"] == "stale"


def test_context_uses_only_what_was_known(unrate_store):
    before = build_context(unrate_store, parse_iso("2025-12-17T15:00:00Z"), historical=True)
    after = build_context(unrate_store, parse_iso("2026-01-10T00:00:00Z"), historical=True)
    assert before["labor"]["unemployment_rate"]["value"] == 4.6
    assert before["labor"]["unemployment_rate"]["obs_date"] == "2025-11-01"
    assert after["labor"]["unemployment_rate"]["value"] == 4.4
    assert after["labor"]["unemployment_rate"]["obs_date"] == "2025-12-01"


def test_context_files_are_written_whole(unrate_store, tmp_path):
    ctx = build_context(unrate_store, parse_iso("2026-01-10T00:00:00Z"), historical=True)
    paths = write_context(ctx, tmp_path)
    assert not list(tmp_path.glob("*.tmp"))
    flat = read_flat(paths["flat"])
    assert int(flat["valid_until_epoch"]["value"]) > int(flat["generated_at_epoch"]["value"])
    assert flat["gold_macro_bias_validated"]["value"] == "0"


def test_secrets_are_redacted():
    url = "https://api.stlouisfed.org/fred/series?series_id=DGS10&api_key=abcdef123&file_type=json"
    assert "abcdef123" not in redact(url) and "series_id=DGS10" in redact(url)
    assert "s3cret" not in redact("https://api.bls.gov/x?registrationkey=s3cret")


class _DeadSession:
    def request(self, *args, **kwargs):
        raise requests.ConnectionError("source en panne")


def test_source_failure_raises_and_is_logged(store, tmp_path):
    http = HttpClient(store, tmp_path, retries=1, min_interval=0, session=_DeadSession())
    with pytest.raises(SourceUnavailable):
        http.get("fred", "https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": "DGS10"})
    row = store.last_fetch("fred")
    assert row["status"] == 0 and row["error"] == "ConnectionError" and row["raw_path"] is None
    assert not list(tmp_path.rglob("*.*"))        # rien n'est archive, rien n'est invente


def test_naive_datetimes_are_refused():
    from datetime import datetime
    with pytest.raises(ValueError):
        iso(datetime(2026, 1, 1))
