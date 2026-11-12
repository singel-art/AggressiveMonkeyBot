"""Heures reelles de publication, tirees du calendrier MT5 une fois importe.

Sans calendrier, l'heure d'une version ALFRED est une estimation (8 h 30 a New
York). Le calendrier MT5 donne l'heure reelle de chaque publication. Ce module :

1. remplace l'heure estimee des lignes concernees par l'heure reelle ;
2. donne a l'evenement de publication (une ligne par publication) son heure reelle ;
3. compare ce que disent ALFRED et MT5, pour mesurer la fiabilite des deux.

Regle de prudence : une heure MT5 ANTERIEURE a l'estimation n'est pas appliquee.
Trois cas existent dans l'export du 2026-11-12 (7 h 30 a New York) ; rien ne
permet de dire s'ils sont exacts, et retenir l'heure la plus tardive ne peut pas
creer de look-ahead.
"""
from __future__ import annotations

from .catalog import FRED_RELEASES, FRED_SERIES, MT5_CFTC_GOLD, MT5_RELEASE_EVENTS
from .store import Event
from datetime import date

from .timeutil import ET, iso, local_to_utc, now_utc, parse_iso


def published_times(store) -> dict[int, dict[str, str]]:
    """{publication FRED: {date a New York: instant UTC publie}} d'apres le calendrier MT5."""
    out: dict[int, dict[str, str]] = {}
    for rid, event_id in MT5_RELEASE_EVENTS.items():
        rows = store.db.execute(
            "SELECT scheduled_at FROM events WHERE event_key LIKE ? AND time_basis='published'",
            (f"mt5|{event_id}|%",)).fetchall()
        out[rid] = {parse_iso(r[0]).astimezone(ET).date().isoformat(): r[0] for r in rows}
    return out


def apply_published_times(store) -> dict:
    """Applique les heures reelles. Sans calendrier MT5 importe, ne fait rien."""
    times = published_times(store)
    report = {"rows_refined": 0, "releases_matched": 0, "earlier_than_estimate": []}
    events, seen_at = [], iso(now_utc())
    for rid, by_day in times.items():
        if not by_day:
            continue
        label, importance = FRED_RELEASES[rid]
        series = [s for s in FRED_SERIES if s.fred_release_id == rid]
        usual = series[0].release_time_et
        for day, published in sorted(by_day.items()):
            # Heure usuelle de cette publication ce jour-la : c'est l'estimation de reference,
            # que des lignes de serie existent deja ou non pour cette date.
            estimate = iso(local_to_utc(date.fromisoformat(day), usual, ET))
            if published < estimate:
                report["earlier_than_estimate"].append({"release": label, "day": day, "published": published,
                                                        "estimate": estimate})
                continue
            report["releases_matched"] += 1
            for s in series:
                report["rows_refined"] += store.refine_availability(s.series_id, day, published)
            events.append(Event(event_key=f"fred_release|{rid}|{day}", source="mt5_calendar",
                                kind="data_release", title=label, scheduled_at=published,
                                time_basis="published", importance=importance))
    if events:
        store.upsert_events(events, seen_at)
    return report


def apply_cftc_published_times(store) -> dict:
    """Date chaque rapport de la CFTC par l'heure reelle de sa publication.

    Le calendrier MT5 publie, pour l'or, la position nette non commerciale en milliers
    de contrats. Chaque rapport est apparie a la premiere publication posterieure a sa
    date dont la valeur est identique (au dixieme de millier pres). L'appariement par
    la valeur resiste aux retards : jours feries, fermetures administratives.
    Un rapport sans correspondance garde sa regle, et il est compte."""
    events = []
    for r in store.db.execute(
            "SELECT scheduled_at, actual FROM events WHERE event_key LIKE ? AND actual IS NOT NULL "
            "ORDER BY scheduled_at", (f"mt5|{MT5_CFTC_GOLD}|%",)):
        try:
            events.append((r["scheduled_at"], float(r["actual"].split()[0])))
        except ValueError:
            continue
    report = {"matched": 0, "unmatched": 0, "rows_refined": 0}
    if not events:
        return report
    series = [r[0] for r in store.db.execute(
        "SELECT DISTINCT series_id FROM observations WHERE series_id LIKE 'COT_GOLD_%'")]
    pending = store.db.execute(
        """SELECT obs_date, value FROM observations WHERE series_id='COT_GOLD_NONCOMM_NET'
           AND available_basis='rule' AND time_quality='estimated' ORDER BY obs_date""").fetchall()
    for row in pending:
        target = row["value"] / 1000.0
        published = next((t for t, v in events if t[:10] > row["obs_date"] and abs(v - target) <= 0.051), None)
        if published is None:
            report["unmatched"] += 1
            continue
        report["matched"] += 1
        for series_id in series:
            report["rows_refined"] += store.refine_rule_availability(series_id, row["obs_date"], published)
    return report


def consistency_report(store) -> dict:
    """Compare les dates de publication deduites d'ALFRED a celles du calendrier MT5,
    et dit combien de lignes ont une heure reelle, estimee ou observee."""
    times = published_times(store)
    releases = {}
    for rid, by_day in times.items():
        series = next(s for s in FRED_SERIES if s.fred_release_id == rid)
        alfred = {v["vintage_date"] for v in store.vintages(series.series_id) if v["n_initial"]}
        if not alfred or not by_day:
            releases[FRED_RELEASES[rid][0]] = {"status": "unavailable"}
            continue
        first, last = min(alfred), max(alfred)
        mt5 = {d for d in by_day if first <= d <= last}
        releases[FRED_RELEASES[rid][0]] = {
            "alfred_releases": len(alfred), "mt5_releases": len(mt5), "common": len(alfred & mt5),
            "alfred_only": sorted(alfred - mt5), "mt5_only": sorted(mt5 - alfred)}
    cot = {r[0]: r[1] for r in store.db.execute(
        "SELECT time_quality, COUNT(*) FROM observations WHERE series_id='COT_GOLD_MM_NET' GROUP BY 1")}
    quality = {f"{r[0]}/{r[1]}": r[2] for r in store.db.execute(
        "SELECT available_basis, time_quality, COUNT(*) FROM observations GROUP BY 1, 2")}
    return {"releases": releases, "cftc_reports_by_time_quality": cot,
            "rows_by_basis_and_time_quality": quality,
            "holidays_known": len(store.holidays())}
