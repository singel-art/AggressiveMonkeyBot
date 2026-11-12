"""Les tests n'utilisent aucune valeur inventee : les fichiers de tests/fixtures
sont des reponses reelles des sources, enregistrees le 2026-11-12 et parfois
reduites en nombre de lignes. Aucun test n'appelle le reseau."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
FIXTURES = ROOT / "tests" / "fixtures"


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
def unrate_store(store, fixture_text):
    """Stockage charge avec les vraies versions successives du taux de chomage."""
    from market_intelligence.catalog import SERIES_BY_ID
    from market_intelligence.connectors.fred import compact_vintages, parse_alfredgraph
    by_vintage = parse_alfredgraph(fixture_text("alfredgraph_UNRATE.csv"), "UNRATE")
    store.add_observations(compact_vintages(SERIES_BY_ID["UNRATE"], by_vintage, {}, "2024-01-01", 1))
    return store
