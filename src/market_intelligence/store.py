"""Stockage SQLite "point-in-time".

Principe : une observation n'est jamais ecrasee. Chaque version d'un chiffre
(premiere publication, puis revisions) est une ligne distincte, datee par
`available_at` : l'instant a partir duquel ce chiffre etait reellement connu.
Toute lecture pour un backtest passe par `asof()`, qui ne renvoie que ce qui
etait disponible a l'instant demande. C'est la protection contre le look-ahead.

Quatre notions sont tenues separees pour chaque ligne :
- `obs_date`     : la periode economique mesuree (date de reference) ;
- `vintage_date` : la date de la version dans la source ;
- `available_at` : notre meilleure estimation de l'instant de disponibilite ;
- `available_at_safe` : une borne tardive sure. Si l'heure n'est qu'estimee,
  c'est la fin du jour de publication a New York. Les lectures de backtest
  utilisent cette borne par defaut.

`available_basis` dit d'ou vient la date :
- 'vintage'  : date de version fournie par ALFRED. Ce n'est pas forcement la date
               de publication officielle, et l'heure est une estimation ;
- 'observed' : instant ou notre collecteur a vu la valeur pour la premiere fois ;
- 'rule'     : regle prudente appliquee a un historique recupere apres coup.

`time_quality` : 'observed' (instant mesure) ou 'estimated' (heure supposee).

`release_kind` :
- 'initial'  : premiere valeur publiee pour cette periode ;
- 'revision' : valeur modifiee par une version ulterieure ;
- 'backfill' : valeur deja ancienne lors de notre premier chargement. On ignore
               si elle est initiale ou revisee : elle n'est jamais presentee
               comme une premiere publication.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS fetch_log (
    fetch_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    url        TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    status     INTEGER NOT NULL,
    sha256     TEXT,
    raw_path   TEXT,
    error      TEXT
);
CREATE TABLE IF NOT EXISTS observations (
    series_id         TEXT NOT NULL,
    obs_date          TEXT NOT NULL,
    vintage_date      TEXT NOT NULL,
    value             REAL NOT NULL,
    available_at      TEXT NOT NULL,
    available_at_safe TEXT NOT NULL CHECK (available_at_safe >= available_at),
    available_basis   TEXT NOT NULL CHECK (available_basis IN ('vintage','observed','rule')),
    time_quality      TEXT NOT NULL CHECK (time_quality IN ('observed','estimated')),
    release_kind      TEXT NOT NULL CHECK (release_kind IN ('initial','revision','backfill')),
    source            TEXT NOT NULL,
    fetch_id          INTEGER REFERENCES fetch_log(fetch_id),
    PRIMARY KEY (series_id, obs_date, vintage_date)
);
CREATE INDEX IF NOT EXISTS idx_obs_asof ON observations(series_id, available_at_safe);
CREATE TABLE IF NOT EXISTS events (
    event_key        TEXT PRIMARY KEY,
    source           TEXT NOT NULL,
    kind             TEXT NOT NULL,
    title            TEXT NOT NULL,
    scheduled_at     TEXT NOT NULL,
    time_basis       TEXT NOT NULL CHECK (time_basis IN ('published','estimated')),
    advance_notice   TEXT NOT NULL CHECK (advance_notice IN ('scheduled','unscheduled')),
    importance       TEXT NOT NULL CHECK (importance IN ('high','medium','low')),
    currency         TEXT,
    actual           TEXT,
    forecast         TEXT,
    previous         TEXT,
    revised_previous TEXT,
    url              TEXT,
    first_seen_at    TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    fetch_id         INTEGER REFERENCES fetch_log(fetch_id)
);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(scheduled_at);
"""


SCHEMA_VERSION = 2


class SchemaMismatch(Exception):
    """La base a ete creee par une version anterieure du module."""


@dataclass(frozen=True)
class Observation:
    series_id: str
    obs_date: str
    vintage_date: str
    value: float
    available_at: str
    available_basis: str
    source: str
    fetch_id: int | None = None
    release_kind: str = "backfill"
    time_quality: str = "estimated"
    available_at_safe: str | None = None  # absent : identique a available_at

    @property
    def safe(self) -> str:
        return self.available_at_safe or self.available_at


def release_kind(known: dict, obs_date: str, first_load: bool) -> str:
    """Nature d'une ligne nouvelle, d'apres ce que le stockage connaissait deja."""
    if obs_date in known:
        return "revision"
    return "backfill" if first_load else "initial"


@dataclass(frozen=True)
class Event:
    event_key: str
    source: str
    kind: str
    title: str
    scheduled_at: str
    time_basis: str
    importance: str
    # 'scheduled' : annonce a l'avance (reunion du FOMC, statistique au calendrier).
    # 'unscheduled' : connu seulement une fois survenu (discours, decision d'urgence).
    advance_notice: str = "scheduled"
    currency: str | None = "USD"
    actual: str | None = None
    forecast: str | None = None
    previous: str | None = None
    revised_previous: str | None = None
    url: str | None = None
    fetch_id: int | None = None


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        has_tables = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='observations'").fetchone()
        if has_tables and version != SCHEMA_VERSION:
            self.db.close()
            raise SchemaMismatch(
                f"{self.path} utilise l'ancien schema (version {version}, attendu {SCHEMA_VERSION}). "
                "Les dates de disponibilite y sont moins fiables : supprimer ce fichier puis relancer "
                "`python -m market_intelligence collect`. Les reponses brutes archivees sont conservees.")
        self.db.executescript(SCHEMA)
        self.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------ collectes
    def log_fetch(self, *, source, url, fetched_at, status, sha256, raw_path, error) -> int:
        cur = self.db.execute(
            "INSERT INTO fetch_log(source,url,fetched_at,status,sha256,raw_path,error) VALUES (?,?,?,?,?,?,?)",
            (source, url, fetched_at, status, sha256, raw_path, error))
        self.db.commit()
        return int(cur.lastrowid)

    def last_fetch(self, source: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM fetch_log WHERE source=? ORDER BY fetch_id DESC LIMIT 1", (source,)).fetchone()

    # --------------------------------------------------------------- observations
    def add_observations(self, rows: Iterable[Observation]) -> int:
        """Ajoute des versions. Une version deja connue (meme serie, meme date
        d'observation, meme vintage) est laissee intacte : l'historique est immuable."""
        cur = self.db.executemany(
            # Seul un doublon de cle est ignore. Une ligne incoherente (borne sure
            # anterieure a l'estimation, valeur hors liste) leve une erreur.
            "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(series_id, obs_date, vintage_date) DO NOTHING",
            [(r.series_id, r.obs_date, r.vintage_date, r.value, r.available_at, r.safe,
              r.available_basis, r.time_quality, r.release_kind, r.source, r.fetch_id) for r in rows])
        self.db.commit()
        return cur.rowcount

    def latest_values(self, series_id: str) -> dict[str, float]:
        """Derniere version stockee de chaque observation (sert a detecter les revisions)."""
        rows = self.db.execute(
            """SELECT o.obs_date, o.value FROM observations o
               JOIN (SELECT obs_date, MAX(available_at_safe) m FROM observations
                     WHERE series_id=? GROUP BY obs_date) x
                 ON o.obs_date=x.obs_date AND o.available_at_safe=x.m
               WHERE o.series_id=?""", (series_id, series_id)).fetchall()
        return {r["obs_date"]: r["value"] for r in rows}

    def asof(self, series_id: str, asof: str, *, limit: int | None = None,
             safe: bool = True) -> list[sqlite3.Row]:
        """La serie telle qu'elle etait connue a l'instant `asof` (UTC ISO).
        Pour chaque periode : la version la plus recente deja disponible.

        safe=True (defaut, a utiliser en backtest) : une valeur dont l'heure n'est
        qu'estimee n'apparait qu'apres la fin de son jour de publication.
        safe=False : utilise l'heure estimee. A reserver a l'analyse, jamais a une
        simulation de decisions prises le jour meme d'une publication."""
        col = "available_at_safe" if safe else "available_at"
        sql = f"""SELECT o.* FROM observations o
                 JOIN (SELECT obs_date, MAX({col}) m FROM observations
                       WHERE series_id=? AND {col}<=? GROUP BY obs_date) x
                   ON o.obs_date=x.obs_date AND o.{col}=x.m
                 WHERE o.series_id=? ORDER BY o.obs_date DESC"""
        params: list = [series_id, asof, series_id]
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return list(reversed(self.db.execute(sql, params).fetchall()))

    def first_releases(self, series_id: str) -> list[sqlite3.Row]:
        """Premiere publication de chaque periode : ce que le marche a vu le jour J.
        Seules les lignes dont le caractere initial est etabli sont renvoyees : une
        periode chargee apres coup ('backfill') n'y figure pas."""
        return self.db.execute(
            "SELECT * FROM observations WHERE series_id=? AND release_kind='initial' ORDER BY obs_date",
            (series_id,)).fetchall()

    def vintages(self, series_id: str) -> list[sqlite3.Row]:
        """Une ligne par version : nombre de premieres publications et de revisions."""
        return self.db.execute(
            """SELECT vintage_date, MIN(available_at) available_at, MIN(available_at_safe) available_at_safe,
                      SUM(release_kind='initial') n_initial, SUM(release_kind='revision') n_revision,
                      SUM(release_kind='backfill') n_backfill, MAX(fetch_id) fetch_id
               FROM observations WHERE series_id=? GROUP BY vintage_date ORDER BY vintage_date""",
            (series_id,)).fetchall()

    def revisions(self, series_id: str, obs_date: str) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM observations WHERE series_id=? AND obs_date=? ORDER BY available_at_safe",
            (series_id, obs_date)).fetchall()

    def series_summary(self) -> list[sqlite3.Row]:
        return self.db.execute(
            """SELECT series_id, source, COUNT(*) n_rows, COUNT(DISTINCT obs_date) n_obs,
                      MIN(obs_date) first_obs, MAX(obs_date) last_obs, MAX(available_at) last_available,
                      SUM(release_kind='initial') n_initial, SUM(release_kind='revision') n_revision,
                      SUM(release_kind='backfill') n_backfill
               FROM observations GROUP BY series_id ORDER BY series_id""").fetchall()

    # --------------------------------------------------------------------- events
    def upsert_events(self, events: Iterable[Event], seen_at: str) -> int:
        """Insere ou met a jour. `first_seen_at` n'est jamais modifie : il prouve
        depuis quand l'evenement etait connu de notre systeme."""
        n = 0
        for e in events:
            self.db.execute(
                """INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(event_key) DO UPDATE SET
                     title=excluded.title, kind=excluded.kind,
                     -- Une heure publiee par la source n'est jamais remplacee par une estimation.
                     scheduled_at=CASE WHEN events.time_basis='published' AND excluded.time_basis='estimated'
                                       THEN events.scheduled_at ELSE excluded.scheduled_at END,
                     time_basis=CASE WHEN events.time_basis='published' THEN 'published'
                                     ELSE excluded.time_basis END,
                     advance_notice=excluded.advance_notice, importance=excluded.importance,
                     actual=COALESCE(excluded.actual, events.actual),
                     forecast=COALESCE(excluded.forecast, events.forecast),
                     previous=COALESCE(excluded.previous, events.previous),
                     revised_previous=COALESCE(excluded.revised_previous, events.revised_previous),
                     url=COALESCE(excluded.url, events.url),
                     updated_at=excluded.updated_at, fetch_id=excluded.fetch_id""",
                (e.event_key, e.source, e.kind, e.title, e.scheduled_at, e.time_basis,
                 e.advance_notice, e.importance, e.currency, e.actual, e.forecast, e.previous,
                 e.revised_previous, e.url, seen_at, seen_at, e.fetch_id))
            n += 1
        self.db.commit()
        return n

    def events_between(self, start: str, end: str, *, asof: str | None = None,
                       min_importance: str = "low") -> list[dict]:
        """Evenements prevus entre deux instants. Avec `asof` :
        - le resultat publie (`actual`, `revised_previous`) est masque tant que l'heure
          prevue n'est pas passee ;
        - un evenement non annonce a l'avance (discours, decision d'urgence) n'apparait
          pas tant qu'il n'a pas eu lieu."""
        levels = {"low": ("low", "medium", "high"), "medium": ("medium", "high"), "high": ("high",)}
        keep = levels[min_importance]
        rows = self.db.execute(
            f"""SELECT * FROM events WHERE scheduled_at>=? AND scheduled_at<=?
                AND importance IN ({','.join('?' * len(keep))}) ORDER BY scheduled_at""",
            (start, end, *keep)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if asof is not None and d["scheduled_at"] > asof:
                if d["advance_notice"] == "unscheduled":
                    continue
                d["actual"] = None
                d["revised_previous"] = None
            out.append(d)
        return out

    def event_counts(self) -> list[sqlite3.Row]:
        return self.db.execute(
            """SELECT source, kind, COUNT(*) n, MIN(scheduled_at) first_at, MAX(scheduled_at) last_at
               FROM events GROUP BY source, kind ORDER BY source, kind""").fetchall()
