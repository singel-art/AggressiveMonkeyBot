"""Les tests n'utilisent aucune valeur de marche inventee : les fichiers de
tests/fixtures sont des reponses reelles des sources, enregistrees le 2026-11-12
et parfois reduites en nombre de lignes. Seule exception, signalee dans son nom
de test : `bls_v2_invalid_key.json`, reponse reelle du BLS a une cle invalide,
ou la cle renvoyee a ete remplacee par le texte KEY_SENT_IN_TEST.

Aucun test n'appelle le reseau : `FakeHttp` rejoue ces reponses.
"""
import hashlib
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
FIXTURES = ROOT / "tests" / "fixtures"

from market_intelligence.errors import SourceUnavailable  # noqa: E402
from market_intelligence.http import FetchResult  # noqa: E402
from market_intelligence.timeutil import iso, parse_iso  # noqa: E402


class FakeHttp:
    """Remplace HttpClient : rend une reponse enregistree selon l'URL demandee.

    routes : liste de (texte cherche dans l'URL complete, nom de fixture ou None).
    None simule une source en panne. `now` fixe l'instant de collecte simule."""

    def __init__(self, store, routes, now="2026-11-12T09:00:00Z"):
        self.store = store
        self.routes = routes
        self.now: datetime = parse_iso(now)
        self.calls: list[dict] = []

    def _serve(self, source, method, url, params, payload):
        full = url + "?" + "&".join(f"{k}={v}" for k, v in (params or {}).items())
        self.calls.append({"source": source, "method": method, "url": full, "payload": payload})
        for needle, fixture in self.routes:
            if needle in full:
                if fixture is None:
                    self.store.log_fetch(source=source, url=url, fetched_at=iso(self.now), status=0,
                                         sha256=None, raw_path=None, error="panne simulee")
                    raise SourceUnavailable(source, url, "panne simulee")
                content = (FIXTURES / fixture).read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                fetch_id = self.store.log_fetch(source=source, url=url, fetched_at=iso(self.now),
                                                status=200, sha256=digest, raw_path=fixture, error=None)
                return FetchResult(fetch_id, source, url, self.now, 200, content, digest, FIXTURES / fixture)
        raise AssertionError(f"requete non prevue par le test : {full}")

    def get(self, source, url, *, params=None, headers=None, ext="bin"):
        return self._serve(source, "GET", url, params, None)

    def post_json(self, source, url, payload, *, ext="json", secrets=()):
        return self._serve(source, "POST", url, None, payload)


@pytest.fixture
def fixture_text():
    def read(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8-sig")
    return read


@pytest.fixture
def store():
    from market_intelligence.store import Store
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def settings(tmp_path):
    from market_intelligence.config import Settings
    return Settings(data_dir=tmp_path, fred_api_key=None, bls_api_key=None)


def load_vintages(store, series_id: str, fixture: str, obs_start: str):
    from market_intelligence.catalog import SERIES_BY_ID
    from market_intelligence.connectors.fred import compact_vintages, parse_alfredgraph
    text = (FIXTURES / fixture).read_text(encoding="utf-8-sig")
    known = store.latest_values(series_id)
    return store.add_observations(compact_vintages(
        SERIES_BY_ID[series_id], parse_alfredgraph(text, series_id), known, obs_start, 1))


@pytest.fixture
def unrate_store(store):
    """Vraies versions successives du taux de chomage, du 2025-01-10 au 2026-11-06."""
    load_vintages(store, "UNRATE", "alfredgraph_UNRATE_batch1.csv", "2023-01-01")
    load_vintages(store, "UNRATE", "alfredgraph_UNRATE_batch2.csv", "2023-01-01")
    return store


@pytest.fixture
def cpi_store(store):
    """Vraies versions du CPI : 2023-12-12, 2024-01-11, 2024-02-09 (revision seule), 2024-02-13."""
    load_vintages(store, "CPIAUCSL", "alfredgraph_CPIAUCSL_2024_revision.csv", "2022-01-01")
    return store
