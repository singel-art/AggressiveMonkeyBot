"""World Gold Council (www.gold.org) : reserves des banques centrales, flux des ETF.

Constat de l'audit du 2026-11-12 : les pages sont publiques, mais les fichiers de
donnees (/download/file/...) repondent HTTP 403 sans compte connecte. Le compte
Goldhub est gratuit. Ce connecteur ne contourne pas cette protection.

Fonctionnement retenu : vous telechargez les fichiers avec votre compte, vous les
deposez dans data/inbox/wgc/, et `ingest` les archive avec empreinte et date.
Leur lecture colonne par colonne sera ecrite a partir d'un vrai fichier, pas devinee.
"""
from __future__ import annotations

import hashlib
import shutil
from datetime import date
from pathlib import Path

from ..errors import SourceUnavailable
from ..timeutil import iso, now_utc
from .base import CheckResult, CollectReport, Connector

PAGES = {
    "central_banks": "https://www.gold.org/goldhub/data/gold-reserves-by-country",
    "etf_flows": "https://www.gold.org/goldhub/data/gold-etfs-holdings-and-flows",
}


class WgcConnector(Connector):
    name = "wgc"

    def check(self) -> CheckResult:
        try:
            res = self.http.get("wgc", PAGES["etf_flows"], ext="html")
        except SourceUnavailable as exc:
            return CheckResult("wgc", False, str(exc))
        gated = "/download/file/" in res.text and "login_form" in res.text
        return CheckResult("wgc", True, "pages publiques lisibles ; fichiers de donnees reserves "
                           "aux comptes connectes" if gated else "page lisible", needs_key=gated)

    def collect(self, since: date) -> CollectReport:
        """Archive les fichiers deposes a la main dans data/inbox/wgc/."""
        report = CollectReport("wgc")
        inbox = self.settings.inbox_dir / "wgc"
        inbox.mkdir(parents=True, exist_ok=True)
        files = sorted(p for p in inbox.iterdir() if p.is_file())
        if not files:
            report.notes.append(f"aucun fichier dans {inbox} : rien a archiver")
            return report
        for path in files:
            now = now_utc()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            target = self.settings.raw_dir / "wgc" / now.strftime("%Y/%m/%d") / f"{digest[:12]}_{path.name}"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), target)
            self.store.log_fetch(source="wgc", url=f"depot manuel : {path.name}", fetched_at=iso(now),
                                 status=200, sha256=digest, raw_path=str(target), error=None)
            report.notes.append(f"archive : {path.name}")
        return report
