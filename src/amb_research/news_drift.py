"""Mesure de la regle H7, derive apres annonce (protocole docs/research/H7_PROTOCOL.md).

Apres la premiere minute de reaction a une grande annonce americaine, l'or
continue-t-il dans le meme sens d'un montant superieur au cout ? Six
configurations fixees avant la mesure. C'est une mesure, pas un robot.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from amb_research.entry_rules import passes, simulate, stats
from amb_research.impulse_study import (OFFSET_RULES, PERIODS, SEED, UTC, Bars, _iso, append_registry, atr,
                                        load_bars, load_meta, to_utc)

# Emploi non agricole, CPI m/m, PPI m/m, ventes au detail m/m, PIB t/t, ISM manufacturier, decision de la Fed.
EVENT_IDS = ("840030016", "840030005", "840030001", "840020010", "840010007", "840040001", "840050014")
CONFIGS = [(float(k), d) for k in (1, 2) for d in (5, 10, 30)]


def announcement_times(db_path: Path) -> list[int]:
    """Heures UTC reelles des annonces retenues. Plusieurs annonces a la meme minute n'en font qu'une."""
    db = sqlite3.connect(db_path)
    marks = ",".join("?" for _ in EVENT_IDS)
    rows = db.execute(f"SELECT scheduled_at FROM events WHERE time_basis='published' AND "
                      f"substr(event_key, 5, 9) IN ({marks}) AND event_key LIKE 'mt5|%'", EVENT_IDS).fetchall()
    db.close()
    return sorted({int(datetime.strptime(r[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()) for r in rows})


def reaction_signal(bars: Bars, events_utc, k: float) -> tuple[np.ndarray, np.ndarray]:
    """Signal a la cloture de la bougie M1 qui ouvre a l'heure de l'annonce, si son corps vaut
    au moins k fois l'ATR M1 de la bougie precedente. Renvoie le signal et la distance du stop
    (retour complet de la reaction, mesure depuis l'ouverture de la bougie d'entree)."""
    n = len(bars)
    signal, distance = np.zeros(n, dtype=np.int64), np.full(n, np.nan)
    atr_m1 = atr(bars)
    events = np.asarray(sorted(set(int(e) for e in events_utc)), dtype=np.int64)
    pos = np.searchsorted(bars.t, events)
    for i in pos[(pos < n - 1) & (pos > 0)]:
        if bars.t[i] not in events or np.isnan(atr_m1[i - 1]):
            continue
        body = bars.c[i] - bars.o[i]
        if abs(body) < k * atr_m1[i - 1] or body == 0:
            continue
        side = 1 if body > 0 else -1
        dist = side * (bars.o[i + 1] - (bars.l[i] if side > 0 else bars.h[i]))
        if dist > 0:                                  # sinon l'entree ouvrirait deja au-dela du stop
            signal[i], distance[i] = side, dist
    return signal, distance


def run(bars: Bars, period: str, events_utc) -> dict:
    start, end = PERIODS[period]
    lo = int(datetime.fromisoformat(start).replace(tzinfo=UTC).timestamp())
    hi = int((datetime.fromisoformat(end) + timedelta(days=1)).replace(tzinfo=UTC).timestamp())
    keep = bars.t < hi
    bars = Bars(bars.t[keep], bars.o[keep], bars.h[keep], bars.l[keep], bars.c[keep], bars.spread[keep], True)
    in_period = bars.t >= lo
    middle = (lo + hi) // 2
    rng = np.random.default_rng(SEED)
    in_data = np.isin(np.asarray(sorted(events_utc), dtype=np.int64), bars.t[in_period])
    results = []
    for k, minutes in CONFIGS:
        signal, distance = reaction_signal(bars, events_utc, k)
        trades = simulate(bars, signal, distance, np.inf, minutes, allowed=in_period)
        net, t = trades["net"], trades["entry_t"]
        res = {"name": f"H7 annonce K={k:g} duree={minutes}min", "k": k, "max_minutes": minutes,
               "all": stats(net, rng), "first_half": stats(net[t < middle]), "second_half": stats(net[t >= middle]),
               "long": stats(net[trades["side"] > 0]), "short": stats(net[trades["side"] < 0]),
               "gross_mean": float(trades["gross"].mean()) if len(net) else None,
               "cost_mean": float(trades["cost"].mean()) if len(net) else None,
               "stop_distance_mean": float(trades["stop_distance"].mean()) if len(net) else None,
               "exits": {r: int((trades["reason"] == r).sum()) for r in ("stop", "duree")}}
        res["passes"] = passes(res)
        results.append(res)
    return {"period": period, "period_bounds": [start, end], "announcements_with_a_bar": int(in_data.sum()),
            "last_bar_utc": _iso(bars.t[-1]), "configs_tested": len(results),
            "configs_passing": sum(r["passes"] for r in results), "results": results}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Mesure de la regle H7 (derive apres annonce).")
    parser.add_argument("--bars", required=True)
    parser.add_argument("--store", required=True)
    parser.add_argument("--offset-rule", required=True, choices=list(OFFSET_RULES))
    parser.add_argument("--period", required=True, choices=list(PERIODS))
    parser.add_argument("--out", default="data/research")
    args = parser.parse_args(argv)
    if args.period == "final_test":
        print("La periode de test final (2026) est verrouillee.", file=sys.stderr)
        return 2
    bars_path = Path(args.bars)
    meta = load_meta(bars_path.with_name(bars_path.name[:-len(".csv")] + ".meta.csv"))
    bars = to_utc(load_bars(bars_path, float(meta["point"])), args.offset_rule)
    report = run(bars, args.period, announcement_times(Path(args.store)))
    report.update({"generated_at": _iso(datetime.now(UTC).timestamp()), "offset_rule": args.offset_rule,
                   "bars_file": bars_path.name})
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = f"news_drift_{args.period}_{datetime.now(UTC).date().isoformat()}.json"
    (out / name).write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    append_registry(out / "registry.jsonl", {"run_at": report["generated_at"], "study": "news_drift_h7",
                                             "period": args.period, "bars_file": bars_path.name,
                                             "offset_rule": args.offset_rule, "configs_tested": report["configs_tested"],
                                             "configs_passing": report["configs_passing"], "report": name})
    print(f"{report['announcements_with_a_bar']} annonces avec une bougie sur la periode {args.period} ; "
          f"{report['configs_passing']} configuration(s) sur {report['configs_tested']} passent le critere.")
    for r in report["results"]:
        a = r["all"]
        if a["n"] == 0:
            print(f"  {r['name']:<32} aucun trade")
            continue
        ci = a.get("net_mean_ci95", [float("nan")] * 2)
        pf = f"{a['profit_factor']:.2f}" if a["profit_factor"] is not None else "n/a"
        print(f"  {r['name']:<32} trades {a['n']:>4}  gagnants {a['win_rate']:.0%}  brut {r['gross_mean']:+.3f}  "
              f"cout {r['cost_mean']:.3f}  net moyen {a['net_mean']:+.3f} [{ci[0]:+.3f} ; {ci[1]:+.3f}]  PF {pf}  "
              f"{'PASSE' if r['passes'] else 'ne passe pas'}")
    print(f"Rapport : {out / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
