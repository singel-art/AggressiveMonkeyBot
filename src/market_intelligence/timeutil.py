"""Outils de temps. Toutes les dates stockees sont en UTC, au format ISO 8601."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
ET = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")


def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(dt: datetime) -> str:
    """Datetime avec fuseau -> texte UTC 'YYYY-MM-DDTHH:MM:SSZ' (triable comme du texte)."""
    if dt.tzinfo is None:
        raise ValueError("datetime sans fuseau refuse : l'ambiguite creerait du look-ahead")
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime:
    text = text.strip()
    if len(text) == 10:
        text += "T00:00:00Z"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def local_to_utc(d: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, t, tzinfo=tz).astimezone(UTC)


def add_business_days(d: date, n: int, holidays: frozenset[str] = frozenset()) -> date:
    """Avance de n jours ouvres (lundi a vendredi). `holidays` : dates ISO des jours
    feries a sauter. Ils viennent du calendrier MT5 quand il a ete importe ; sinon
    l'ensemble est vide et un jour ferie est compte, a tort, comme ouvre."""
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    while remaining:
        d += timedelta(days=step)
        if d.weekday() < 5 and d.isoformat() not in holidays:
            remaining -= 1
    return d
