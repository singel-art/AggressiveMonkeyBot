"""Client HTTP commun a tous les connecteurs.

Garanties :
- chaque reponse recue est archivee telle quelle sur disque (preuve et rejouabilite) ;
- chaque appel, reussi ou non, est inscrit au journal des collectes ;
- en cas d'echec, une exception SourceUnavailable est levee : jamais de valeur de repli ;
- les cles API n'apparaissent ni dans le journal ni dans les noms de fichiers.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests

from .errors import SourceUnavailable
from .timeutil import iso, now_utc

_SECRET_PARAMS = re.compile(r"(?i)\b(api_key|registrationkey|key|token|c)=([^&\s]+)")
# Statuts qui signalent un refus et non une panne passagere : inutile d'insister.
_NO_RETRY = {400, 401, 403, 404, 410}


def redact(url: str) -> str:
    return _SECRET_PARAMS.sub(lambda m: f"{m.group(1)}=***", url)


@dataclass
class FetchResult:
    fetch_id: int
    source: str
    url: str  # deja expurgee des secrets
    fetched_at: datetime
    status: int
    content: bytes
    sha256: str
    raw_path: Path

    @property
    def text(self) -> str:
        return self.content.decode("utf-8-sig", errors="replace")


class HttpClient:
    def __init__(self, store, raw_dir: Path, *, timeout: float = 45.0, retries: int = 3,
                 min_interval: float = 1.0, session: requests.Session | None = None):
        self.store = store
        self.raw_dir = Path(raw_dir)
        self.timeout = timeout
        self.retries = retries
        self.min_interval = min_interval
        self.session = session or requests.Session()
        self._last_call: dict[str, float] = {}

    def _throttle(self, host: str) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_call.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        self._last_call[host] = time.monotonic()

    def get(self, source: str, url: str, *, params: dict | None = None,
            headers: dict | None = None, ext: str = "bin") -> FetchResult:
        return self._request("GET", source, url, params=params, headers=headers, ext=ext)

    def post_json(self, source: str, url: str, payload: dict, *, ext: str = "json",
                  secrets: tuple[str, ...] = ()) -> FetchResult:
        """`secrets` : valeurs envoyees dans le corps de la requete (cle API). Certaines
        sources les recopient dans leur reponse : elles sont masquees avant l'archivage."""
        return self._request("POST", source, url, json=payload, ext=ext, secrets=secrets)

    def _request(self, method: str, source: str, url: str, *, params=None, headers=None,
                 json=None, ext: str = "bin", secrets: tuple[str, ...] = ()) -> FetchResult:
        prepared = requests.Request(method, url, params=params).prepare()
        safe_url = redact(prepared.url)
        host = urlsplit(url).netloc
        last_reason = "inconnu"
        for attempt in range(1, self.retries + 1):
            self._throttle(host)
            try:
                resp = self.session.request(method, url, params=params, headers=headers,
                                            json=json, timeout=self.timeout)
            except requests.RequestException as exc:
                last_reason = f"{type(exc).__name__}"
            else:
                if resp.status_code == 200 and resp.content:
                    return self._archive(source, safe_url, resp, ext, secrets)
                last_reason = f"HTTP {resp.status_code}" if resp.status_code != 200 else "reponse vide"
                if resp.status_code in _NO_RETRY:
                    break
            if attempt < self.retries:
                time.sleep(2 ** attempt)
        self.store.log_fetch(source=source, url=safe_url, fetched_at=iso(now_utc()), status=0,
                             sha256=None, raw_path=None, error=last_reason)
        raise SourceUnavailable(source, safe_url, last_reason)

    def _archive(self, source: str, safe_url: str, resp: requests.Response, ext: str,
                 secrets: tuple[str, ...] = ()) -> FetchResult:
        fetched_at = now_utc()
        content = resp.content
        for secret in secrets:
            if secret:
                content = content.replace(secret.encode(), b"***")
        digest = hashlib.sha256(content).hexdigest()
        folder = self.raw_dir / source / fetched_at.strftime("%Y/%m/%d")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{fetched_at.strftime('%H%M%S')}_{digest[:12]}.{ext}"
        path.write_bytes(content)
        fetch_id = self.store.log_fetch(source=source, url=safe_url, fetched_at=iso(fetched_at),
                                        status=resp.status_code, sha256=digest,
                                        raw_path=str(path), error=None)
        return FetchResult(fetch_id, source, safe_url, fetched_at, resp.status_code,
                           content, digest, path)
