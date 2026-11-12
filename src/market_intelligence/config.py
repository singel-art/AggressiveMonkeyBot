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


def read_env_file(path: Path) -> dict[str, str]:
    """Lit un fichier .env simple (NOM=valeur, lignes vides et # ignores)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def load_settings(data_dir: str | os.PathLike | None = None, env_file: Path | None = None) -> Settings:
    """Les variables d'environnement priment sur le fichier .env (jamais versionne)."""
    for name, value in read_env_file(env_file or PROJECT_ROOT / ".env").items():
        if value and name in ("AMB_DATA_DIR", "BLS_API_KEY", "FRED_API_KEY"):
            os.environ.setdefault(name, value)
    root = Path(data_dir or os.environ.get("AMB_DATA_DIR") or PROJECT_ROOT / "data")
    return Settings(
        data_dir=root,
        fred_api_key=os.environ.get("FRED_API_KEY") or None,
        bls_api_key=os.environ.get("BLS_API_KEY") or None,
    )
