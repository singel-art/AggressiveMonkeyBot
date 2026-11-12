"""Construction du contexte Market Intelligence.

`build_context(store, asof)` ne lit que ce qui etait disponible a l'instant
`asof`. La meme fonction sert donc en direct (asof = maintenant) et en backtest
(asof = n'importe quel instant passe) : le contexte historique est reproductible.

Regles de surete :
- une donnee absente ou trop ancienne est marquee 'unavailable' ou 'stale' et
  n'entre dans aucun calcul ;
- le biais n'est publie que si au moins deux composantes sont exploitables ;
- une valeur dont l'heure de publication n'est qu'estimee n'entre dans le contexte
  qu'apres la fin de son jour de publication (borne `available_at_safe`) ;
- chaque valeur porte sa serie, sa date d'observation, sa date de disponibilite
  et sa source.

Le biais macro est une lecture mecanique de relations connues (taux reels,
dollar, taux courts). Ses seuils ne sont pas calibres et son utilite pour le
trading n'est pas demontree : il porte `"validated": false` tant qu'aucun test
ne l'a etablie.
"""
from __future__ import annotations

import csv
import json
import os
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

from .catalog import BLS_CROSSCHECK, SERIES_BY_ID
from .timeutil import iso, now_utc, parse_iso

SCHEMA_VERSION = 2

PARAMS = {
    "blackout_minutes_before": 30,
    "blackout_minutes_after": 30,
    "high_risk_minutes_before": 240,
    "elevated_risk_minutes_before": 1440,
    "calendar_max_age_hours": 48,
    "context_ttl_minutes": 30,
    "bias_lookback_observations": 20,
    "bias_threshold_real_yield_pt": 0.10,
    "bias_threshold_us2y_pt": 0.10,
    "bias_threshold_dollar_pct": 0.5,
    "bias_min_components": 2,
}

COT_MAX_AGE_DAYS = 12


def _provenance(row) -> dict:
    """Tout ce qui permet de juger la fiabilite temporelle d'une valeur."""
    return {"vintage_date": row["vintage_date"], "available_at": row["available_at"],
            "available_at_safe": row["available_at_safe"], "available_basis": row["available_basis"],
            "time_quality": row["time_quality"], "release_kind": row["release_kind"],
            "source": row["source"]}


def _metric(store, series_id: str, asof: datetime, *, title: str | None = None,
            max_age_days: int | None = None, lookbacks=(5, 20)) -> dict:
    meta = SERIES_BY_ID.get(series_id)
    out = {"series_id": series_id, "title": title or (meta.title if meta else series_id)}
    rows = store.asof(series_id, iso(asof), limit=max(lookbacks, default=0) + 1)
    if not rows:
        return {**out, "status": "unavailable", "reason": "aucune donnee disponible a cette date"}
    last = rows[-1]
    age = (asof.date() - date.fromisoformat(last["obs_date"])).days
    limit = max_age_days if max_age_days is not None else (meta.max_age_days if meta else 7)
    out.update({
        "value": last["value"], "unit": meta.unit if meta else None, "obs_date": last["obs_date"],
        **_provenance(last), "frequency": meta.frequency if meta else None,
        "age_days": age, "max_age_days": limit,
        "status": "ok" if age <= limit else "stale"})
    for n in lookbacks:
        if len(rows) > n:
            out[f"change_{n}obs"] = round(last["value"] - rows[-1 - n]["value"], 6)
            out[f"change_{n}obs_pct"] = round((last["value"] / rows[-1 - n]["value"] - 1) * 100, 4) \
                if rows[-1 - n]["value"] else None
    return out


def _yoy(store, series_id: str, asof: datetime) -> dict:
    """Glissement annuel calcule avec les deux chiffres tels que connus a `asof`."""
    meta = SERIES_BY_ID[series_id]
    base = {"series_id": series_id, "title": f"{meta.title}, glissement annuel", "unit": "%"}
    rows = store.asof(series_id, iso(asof), limit=14)
    if not rows:
        return {**base, "status": "unavailable", "reason": "aucune donnee disponible a cette date"}
    last = rows[-1]
    year_ago = f"{int(last['obs_date'][:4]) - 1}{last['obs_date'][4:]}"
    ref = next((r for r in rows if r["obs_date"] == year_ago), None)
    if ref is None:
        return {**base, "status": "unavailable", "reason": f"observation {year_ago} absente"}
    age = (asof.date() - date.fromisoformat(last["obs_date"])).days
    # Un chiffre mensuel porte la date du 1er du mois et parait le mois suivant.
    limit = meta.max_age_days + 31
    return {**base, "value": round((last["value"] / ref["value"] - 1) * 100, 3),
            "obs_date": last["obs_date"], **_provenance(last),
            "reference_vintage_date": ref["vintage_date"], "frequency": "M",
            "age_days": age, "max_age_days": limit,
            "status": "ok" if age <= limit else "stale"}


def _monthly_change(store, series_id: str, asof: datetime) -> dict:
    meta = SERIES_BY_ID[series_id]
    m = _metric(store, series_id, asof, max_age_days=meta.max_age_days + 31, lookbacks=(1,))
    if "change_1obs" in m:
        m["monthly_change"] = m.pop("change_1obs")
        m.pop("change_1obs_pct", None)
    return m


def _policy(store, asof: datetime) -> dict:
    upper = _metric(store, "DFEDTARU", asof, lookbacks=())
    lower = _metric(store, "DFEDTARL", asof, lookbacks=())
    out = {"target_upper": upper, "target_lower": lower}
    rows = store.asof("DFEDTARU", iso(asof))
    change = None
    for prev, cur in zip(rows, rows[1:]):
        if cur["value"] != prev["value"]:
            change = {"effective_date": cur["obs_date"], "from": prev["value"], "to": cur["value"],
                      "direction": "hausse" if cur["value"] > prev["value"] else "baisse",
                      "source": cur["source"]}
    out["last_change"] = change or {"status": "unavailable",
                                    "reason": "aucun changement dans l'historique charge"}
    return out


def _events(store, asof: datetime, historical: bool) -> dict:
    a = iso(asof)
    upcoming = store.events_between(a, iso(asof + timedelta(days=7)), asof=a, min_importance="medium")
    recent_high = store.events_between(iso(asof - timedelta(hours=24)), a, asof=a, min_importance="high")
    horizon = store.events_between(a, iso(asof + timedelta(days=60)), asof=a, min_importance="high")
    next_high = horizon[0] if horizon else None
    last_high = recent_high[-1] if recent_high else None

    def brief(e):
        return {k: e[k] for k in ("scheduled_at", "title", "kind", "importance", "time_basis",
                                  "advance_notice", "source", "forecast", "previous", "actual", "url")
                if e.get(k) is not None}

    fresh = store.db.execute(
        "SELECT MAX(fetched_at) m FROM fetch_log WHERE status=200 AND source IN ('fed','fred','mt5_calendar') "
        "AND fetched_at<=?", (a,)).fetchone()["m"]
    sources = [r["source"] for r in store.db.execute("SELECT DISTINCT source FROM events")]
    warnings = []
    if "mt5_calendar" not in sources:
        warnings.append("calendrier MT5 non importe : previsions absentes, heures des publications "
                        "de donnees estimees")
    risk = {"params": {k: v for k, v in PARAMS.items() if "risk" in k or "blackout" in k}}
    calendar_ok = True
    if not historical:
        if fresh is None:
            calendar_ok = False
        else:
            calendar_ok = (asof - parse_iso(fresh)) <= timedelta(hours=PARAMS["calendar_max_age_hours"])
    if not sources or not calendar_ok:
        risk.update({"level": "unknown", "reason": "calendrier indisponible ou perime : "
                     "par prudence, aucune nouvelle entree ne devrait etre ouverte"})
    else:
        to_next = (parse_iso(next_high["scheduled_at"]) - asof).total_seconds() / 60 if next_high else None
        since_last = (asof - parse_iso(last_high["scheduled_at"])).total_seconds() / 60 if last_high else None
        if (to_next is not None and to_next <= PARAMS["blackout_minutes_before"]) or \
                (since_last is not None and since_last <= PARAMS["blackout_minutes_after"]):
            level = "blackout"
        elif to_next is not None and to_next <= PARAMS["high_risk_minutes_before"]:
            level = "high"
        elif to_next is not None and to_next <= PARAMS["elevated_risk_minutes_before"]:
            level = "elevated"
        else:
            level = "normal"
        risk.update({"level": level,
                     "minutes_to_next_high_impact": round(to_next) if to_next is not None else None,
                     "minutes_since_last_high_impact": round(since_last) if since_last is not None else None})
    return {"calendar_last_refresh": fresh, "event_sources": sorted(sources),
            "next_high_impact": brief(next_high) if next_high else None,
            "last_high_impact_24h": brief(last_high) if last_high else None,
            "upcoming_7d": [brief(e) for e in upcoming],
            "announcement_risk": risk, "warnings": warnings}


def _bias(rates: dict, dollar: dict) -> dict:
    n = PARAMS["bias_lookback_observations"]
    key = f"change_{n}obs"

    def vote(metric: dict, field: str, threshold: float, name: str, rationale: str) -> dict:
        comp = {"name": name, "series_id": metric.get("series_id"), "rationale": rationale,
                "threshold": threshold}
        if metric.get("status") != "ok" or metric.get(field) is None:
            return {**comp, "status": "excluded", "reason": metric.get("status", "unavailable")}
        change = metric[field]
        v = 1 if change <= -threshold else (-1 if change >= threshold else 0)
        return {**comp, "status": "used", "change": change, "vote": v,
                "obs_date": metric["obs_date"], "source": metric["source"]}

    components = [
        vote(rates["real10y"], key, PARAMS["bias_threshold_real_yield_pt"], "taux reel a 10 ans",
             "une baisse du taux reel reduit le cout de detention de l'or"),
        vote(dollar["broad_index"], key + "_pct", PARAMS["bias_threshold_dollar_pct"], "dollar",
             "un dollar plus faible soutient le prix de l'or en dollars"),
        vote(rates["us2y"], key, PARAMS["bias_threshold_us2y_pt"], "taux a 2 ans",
             "une baisse du taux a 2 ans traduit des anticipations de politique plus souple"),
    ]
    used = [c for c in components if c["status"] == "used"]
    out = {"validated": False, "lookback_observations": n, "components": components,
           "note": "lecture mecanique, seuils non calibres ; a valider avant tout usage en trading"}
    if len(used) < PARAMS["bias_min_components"]:
        return {**out, "label": "unavailable", "score": None,
                "reason": f"{len(used)} composante(s) exploitable(s) sur {len(components)}"}
    score = sum(c["vote"] for c in used)
    label = "bullish" if score >= 2 else ("bearish" if score <= -2 else "neutral")
    return {**out, "label": label, "score": score, "components_used": len(used)}


def _verification(store, asof: datetime) -> list[dict]:
    """Recoupe le dernier chiffre BLS avec la meme observation lue sur FRED/ALFRED."""
    out = []
    for bls_id, fred_id in BLS_CROSSCHECK.items():
        bls = store.asof(f"BLS:{bls_id}", iso(asof), limit=1)
        if not bls:
            out.append({"fred": fred_id, "bls": bls_id, "status": "unavailable"})
            continue
        month = bls[-1]["obs_date"]
        fred = [r for r in store.asof(fred_id, iso(asof), limit=6) if r["obs_date"] == month]
        if not fred:
            out.append({"fred": fred_id, "bls": bls_id, "month": month[:7], "status": "unavailable",
                        "reason": "mois absent cote FRED"})
            continue
        match = abs(fred[0]["value"] - bls[-1]["value"]) <= 1e-6 * max(1.0, abs(fred[0]["value"]))
        out.append({"fred": fred_id, "bls": bls_id, "month": month[:7], "fred_value": fred[0]["value"],
                    "bls_value": bls[-1]["value"], "status": "match" if match else "mismatch"})
    return out


def build_context(store, asof: datetime | None = None, *, historical: bool = False) -> dict:
    generated = now_utc()
    asof = asof or generated
    rates = {
        "us10y": _metric(store, "DGS10", asof),
        "us2y": _metric(store, "DGS2", asof),
        "real10y": _metric(store, "DFII10", asof),
        "breakeven10y": _metric(store, "T10YIE", asof),
        "fed_funds_effective": _metric(store, "DFF", asof),
    }
    dollar = {"broad_index": _metric(store, "DTWEXBGS", asof)}
    ctx = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(generated),
        "asof": iso(asof),
        "mode": "historical" if historical else "live",
        "valid_until": iso(generated + timedelta(minutes=PARAMS["context_ttl_minutes"])),
        "policy": _policy(store, asof),
        "rates": rates,
        "dollar": dollar,
        "inflation": {"cpi_yoy": _yoy(store, "CPIAUCSL", asof), "core_cpi_yoy": _yoy(store, "CPILFESL", asof),
                      "pce_yoy": _yoy(store, "PCEPI", asof), "core_pce_yoy": _yoy(store, "PCEPILFE", asof)},
        "labor": {"nonfarm_payrolls": _monthly_change(store, "PAYEMS", asof),
                  "unemployment_rate": _monthly_change(store, "UNRATE", asof)},
        "gold_positioning": {
            "managed_money_net": _metric(store, "COT_GOLD_MM_NET", asof, title="Position nette des gerants "
                                         "sur l'or (COMEX)", max_age_days=COT_MAX_AGE_DAYS, lookbacks=(1, 4)),
            "open_interest": _metric(store, "COT_GOLD_OPEN_INTEREST", asof, title="Positions ouvertes sur "
                                     "l'or (COMEX)", max_age_days=COT_MAX_AGE_DAYS, lookbacks=(1, 4))},
        "events": _events(store, asof, historical),
        "verification": _verification(store, asof),
    }
    ctx["gold_macro_bias"] = _bias(rates, dollar)
    problems = [name for name, m in {**rates, **dollar}.items() if m.get("status") != "ok"]
    risk_level = ctx["events"]["announcement_risk"]["level"]
    if risk_level == "unknown" and len(problems) == len(rates) + len(dollar):
        ctx["status"] = "unavailable"
    elif problems or risk_level == "unknown" or ctx["gold_macro_bias"]["label"] == "unavailable":
        ctx["status"] = "degraded"
    else:
        ctx["status"] = "ok"
    ctx["degraded_items"] = problems
    ctx["sources"] = [dict(r) for r in store.db.execute(
        """SELECT source, MAX(CASE WHEN status=200 THEN fetched_at END) last_success,
                  MAX(CASE WHEN status<>200 THEN fetched_at END) last_failure
           FROM fetch_log WHERE fetched_at<=? GROUP BY source ORDER BY source""", (iso(asof),))]
    return ctx


# ------------------------------------------------------------------- ecriture
def _ascii(text) -> str:
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return text.replace(";", ",").replace("\n", " ").strip()


def _atomic_write(path: Path, text: str) -> None:
    """Ecrit dans un fichier temporaire puis renomme : l'EA ne lit jamais un fichier a moitie ecrit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def flat_rows(ctx: dict) -> list[tuple[str, str, str, str]]:
    """Version a plat pour MQL5 : (cle, valeur, statut, source). Une valeur non
    exploitable est ecrite vide, avec son statut : l'EA ne peut pas la confondre avec un nombre."""
    rows: list[tuple[str, str, str, str]] = []

    def put(key, value, status="ok", source=""):
        rows.append((key, "" if value is None else _ascii(value), status, source))

    def epoch(text):
        return int(parse_iso(text).timestamp())

    put("schema_version", ctx["schema_version"])
    put("generated_at_epoch", epoch(ctx["generated_at"]))
    put("valid_until_epoch", epoch(ctx["valid_until"]))
    put("context_status", ctx["status"])
    risk = ctx["events"]["announcement_risk"]
    put("risk_level", risk["level"])
    nxt = ctx["events"]["next_high_impact"]
    if nxt:
        put("next_high_impact_epoch", epoch(nxt["scheduled_at"]), "ok", nxt["source"])
        put("next_high_impact_title", nxt["title"], "ok", nxt["source"])
        put("next_high_impact_time_basis", nxt["time_basis"], "ok", nxt["source"])
    else:
        put("next_high_impact_epoch", None, "unavailable")
    bias = ctx["gold_macro_bias"]
    put("gold_macro_bias", bias["label"], "ok" if bias["label"] != "unavailable" else "unavailable")
    put("gold_macro_bias_score", bias["score"], "ok" if bias["score"] is not None else "unavailable")
    put("gold_macro_bias_validated", int(bias["validated"]))
    for group, name, fields in (("rates", "us10y", ("value", "change_5obs", "change_20obs")),
                                ("rates", "us2y", ("value", "change_5obs", "change_20obs")),
                                ("rates", "real10y", ("value", "change_5obs", "change_20obs")),
                                ("dollar", "broad_index", ("value", "change_5obs_pct", "change_20obs_pct"))):
        m = ctx[group][name]
        usable = m.get("status") == "ok"
        for f in fields:
            put(f"{name}_{f}", m.get(f) if usable else None, m.get("status", "unavailable"), m.get("source", ""))
    return rows


def write_context(ctx: dict, out_dir: Path) -> dict[str, Path]:
    out_dir = Path(out_dir)
    paths = {"json": out_dir / "context.json", "flat": out_dir / "context_mt5.csv",
             "events": out_dir / "events_mt5.csv"}
    _atomic_write(paths["json"], json.dumps(ctx, ensure_ascii=False, indent=2))
    lines = ["key;value;status;source"] + [";".join(r) for r in flat_rows(ctx)]
    _atomic_write(paths["flat"], "\n".join(lines) + "\n")
    ev = ["time_epoch;time_utc;importance;kind;time_basis;source;title"]
    for e in ctx["events"]["upcoming_7d"]:
        ev.append(";".join([str(int(parse_iso(e["scheduled_at"]).timestamp())), e["scheduled_at"],
                            e["importance"], e["kind"], e["time_basis"], e["source"], _ascii(e["title"])]))
    _atomic_write(paths["events"], "\n".join(ev) + "\n")
    return paths


def read_flat(path: Path) -> dict[str, dict]:
    with open(path, newline="", encoding="utf-8") as handle:
        return {r["key"]: r for r in csv.DictReader(handle, delimiter=";")}
