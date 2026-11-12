"""Contrat commun des connecteurs.

Un connecteur sait deux choses : verifier que sa source repond (`check`) et
collecter ses donnees dans le stockage (`collect`). Il ne calcule aucun signal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class CheckResult:
    source: str
    ok: bool
    detail: str
    needs_key: bool = False


@dataclass
class CollectReport:
    source: str
    rows_added: int = 0
    events_upserted: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class Connector:
    name = "base"

    def __init__(self, http, store, settings):
        self.http = http
        self.store = store
        self.settings = settings

    def check(self) -> CheckResult:  # pragma: no cover - interface
        raise NotImplementedError

    def collect(self, since: date) -> CollectReport:  # pragma: no cover - interface
        raise NotImplementedError
