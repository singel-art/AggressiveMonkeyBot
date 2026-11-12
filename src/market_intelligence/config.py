"""Configuration : chemins, cles (lues dans l'environnement, jamais dans le code)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Racine du depot : <racine>/src/market_intelligence/config.py
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    fred_api_key: str | None
    bls_api_key: str | None
    proxy: str | None = None

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "market_intelligence.sqlite"

    @property
    def context_dir(self) -> Path:
        return self.data_dir / "context"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"


def load_settings(data_dir: str | os.PathLike | None = None) -> Settings:
    root = Path(data_dir or os.environ.get("AMB_DATA_DIR") or PROJECT_ROOT / "data")
    return Settings(
        data_dir=root,
        fred_api_key=os.environ.get("FRED_API_KEY") or None,
        bls_api_key=os.environ.get("BLS_API_KEY") or None,
    )
