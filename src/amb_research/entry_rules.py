"""Mesure des regles d'entree H4 a H6 (protocole docs/research/H4_H6_PROTOCOL.md).

Trois regles, douze configurations, fixees avant la mesure. Ce module simule une
position a la fois avec un stop fixe et un objectif, sur des bougies M1 : c'est
une mesure, pas un robot, et le resultat reste a confirmer sur des ticks reels.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from amb_research import technical as tk
from amb_research.impulse_study import (BOOTSTRAP_DRAWS, OFFSET_RULES, PERIODS, SEED, UTC, Bars, _iso,
                                        append_registry, high_impact_times_from_store, load_bars,
                                        load_meta, to_utc)

STOP_ATR = 1.0                     # stop a 1 ATR M15
MAX_SPREAD_RATIO = 2.0
EVENT_BEFORE, EVENT_AFTER = 30.0, 5.0          # minutes sans entree autour d'une annonce majeure
MAX_ENTRY_DELAY = 300              # secondes entre le signal et l'ouverture de la bougie d'entree
HOUR = 3600

# (regle, nom, parametres du signal, objectif en multiples du stop, duree maximale en minutes)
CONFIGS = (
    [("H4", f"H4 repli S={s} R={r}", {"rsi": s}, float(r), 120) for s in (30, 40) for r in (1, 2)]
    + [("H5", f"H5 cassure Asie {'avec' if f else 'sans'} filtre H1 R={r}", {"trend_filter": f}, float(r), 240)
       for f in (False, True) for r in (1, 2)]
    + [("H6", f"H6 retour K={k} duree={m}min", {"k": float(k)}, 1.0, m) for k in (4, 6) for m in (15, 60)]
)


# -------------------------------------------------------------------- signaux
def _trailing(values: np.ndarray, window: int, fn) -> np.ndarray:
    """Minimum ou maximum des `window` dernieres valeurs, bougie courante comprise."""
    out = np.full(len(values), np.nan)
    if len(values) >= window:
        out[window - 1:] = fn(sliding_window_view(values, window), axis=1)
    return out


def common_filter(f: dict) -> np.ndarray:
    """Filtres communs : spread normal, pas d'annonce majeure proche, ATR M15 connu."""
    with np.errstate(invalid="ignore"):
        near_event = (f["minutes_to_event"] <= EVENT_BEFORE) | (f["minutes_since_event"] <= EVENT_AFTER)
        return ~(f["spread_ratio"] > MAX_SPREAD_RATIO) & ~near_event & (f["atr_m15"] > 0)


def signal_pullback(bars: Bars, f: dict, rsi: float) -> np.ndarray:
    """H4 : tendance H1 et H4 alignees, RSI M5 passe sous le seuil dans les 30 dernieres
    bougies, et la bougie M1 cloture au-dela de l'extreme de la precedente."""
    r = np.where(np.isnan(f["rsi_m5"]), 50.0, f["rsi_m5"])
    prev_high = np.concatenate([[np.inf], bars.h[:-1]])
    prev_low = np.concatenate([[-np.inf], bars.l[:-1]])
    with np.errstate(invalid="ignore"):
        buy = (f["trend_h1"] == 1) & (f["trend_h4"] == 1) & (_trailing(r, 30, np.min) <= rsi) & (bars.c > prev_high)
        sell = (f["trend_h1"] == -1) & (f["trend_h4"] == -1) & (_trailing(r, 30, np.max) >= 100 - rsi) \
            & (bars.c < prev_low)
    return np.where(buy, 1, np.where(sell, -1, 0))


def signal_asia_breakout(bars: Bars, f: dict, trend_filter: bool) -> np.ndarray:
    """H5 : premiere cloture de la journee au-dela d'une borne du range asiatique, entre
    08h00 et 11h00 heure de Londres. Une seule entree par jour, appliquee plus bas."""
    close_t = bars.t + 60
    clock = (close_t + tk._offset(close_t, "Europe/London")) % 86400
    window = (clock >= 8 * HOUR) & (clock < 11 * HOUR)
    day = tk.sessions(bars.t)["trading_day"]
    out = np.zeros(len(bars), dtype=np.int64)
    with np.errstate(invalid="ignore"):
        for side, beyond in ((1, bars.c > f["asia_high"]), (-1, bars.c < f["asia_low"])):
            idx = np.flatnonzero(beyond & window)
            if len(idx):
                first = idx[np.concatenate([[True], day[idx][1:] != day[idx][:-1]])]
                if trend_filter:
                    first = first[f["trend_h1"][first] == side]
                out[first] = side
    return out


def signal_fade(bars: Bars, f: dict, k: float) -> np.ndarray:
    """H6 : mouvement extreme en 15 bougies et RSI M5 extreme, entree a contre-sens."""
    with np.errstate(invalid="ignore"):
        sell = (f["move_15"] >= k) & (f["rsi_m5"] >= 80)
        buy = (f["move_15"] <= -k) & (f["rsi_m5"] <= 20)
    return np.where(buy, 1, np.where(sell, -1, 0))


def build_signal(rule: str, bars: Bars, f: dict, params: dict) -> np.ndarray:
    raw = {"H4": signal_pullback, "H5": signal_asia_breakout, "H6": signal_fade}[rule](bars, f, **params)
    return np.where(common_filter(f), raw, 0)


# ----------------------------------------------------------------- simulation
def simulate(bars: Bars, signal: np.ndarray, stop_distance: np.ndarray, reward: float, max_minutes: int,
             allowed: np.ndarray | None = None, one_per_day: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Une position a la fois. Entree a l'ouverture de la bougie qui suit le signal.
    Achat : entree a l'ask (bid + spread d'entree), sortie au bid. Vente : entree au bid,
    sortie a l'ask (bid + spread de sortie). Stop et objectif touches dans la meme bougie :
    le stop est compte. `allowed` limite les bougies de signal ; `one_per_day` donne la
    journee de chaque bougie quand une seule entree par jour est permise."""
    n = len(bars)
    rows = []
    free_from, last_day = 0, None
    for s in np.flatnonzero(signal):
        e = s + 1
        if s < free_from or e >= n or (allowed is not None and not allowed[s]):
            continue
        if one_per_day is not None and one_per_day[s] == last_day:
            continue
        if bars.t[e] - bars.t[s] > MAX_ENTRY_DELAY:
            continue
        side, dist, entry = int(signal[s]), float(stop_distance[s]), float(bars.o[e])
        stop, target = entry - side * dist, entry + side * reward * dist
        deadline = bars.t[e] + max_minutes * 60
        exit_price = exit_bar = None
        for j in range(e, n):
            o, h, l = bars.o[j], bars.h[j], bars.l[j]
            if bars.t[j] >= deadline:
                exit_price, reason = o, "duree"
            elif j > e and side * (o - stop) <= 0:
                exit_price, reason = o, "stop"
            elif (l <= stop) if side > 0 else (h >= stop):
                exit_price, reason = stop, "stop"
            elif (h >= target) if side > 0 else (l <= target):
                exit_price, reason = target, "objectif"
            elif bars.t[j] + 60 >= deadline:
                exit_price, reason = bars.c[j], "duree"
            if exit_price is not None:
                exit_bar = j
                break
        if exit_bar is None:                              # encore ouvert a la fin des donnees : non compte
            break
        gross = side * (exit_price - entry)
        cost = float(bars.spread[e] if side > 0 else bars.spread[exit_bar])
        rows.append((bars.t[e], side, gross, cost, dist, reason))
        free_from = exit_bar
        last_day = one_per_day[s] if one_per_day is not None else None
    cols = list(zip(*rows)) if rows else [[], [], [], [], [], []]
    gross, cost = np.array(cols[2], dtype=float), np.array(cols[3], dtype=float)
    return {"entry_t": np.array(cols[0], dtype=np.int64), "side": np.array(cols[1], dtype=np.int64),
            "gross": gross, "cost": cost, "net": gross - cost, "stop_distance": np.array(cols[4], dtype=float),
            "reason": np.array(cols[5], dtype=object)}


# ---------------------------------------------------------------- statistiques
def stats(net: np.ndarray, rng: np.random.Generator | None = None) -> dict:
    n = int(len(net))
    if n == 0:
        return {"n": 0}
    wins, losses = net[net > 0].sum(), -net[net < 0].sum()
    streak = worst = 0
    for x in net:
        streak = streak + 1 if x <= 0 else 0
        worst = max(worst, streak)
    out = {"n": n, "win_rate": float((net > 0).mean()), "net_mean": float(net.mean()),
           "net_total": float(net.sum()), "profit_factor": float(wins / losses) if losses > 0 else None,
           "worst_losing_streak": int(worst)}
    if n >= 30 and rng is not None:
        means = net[rng.integers(0, n, size=(BOOTSTRAP_DRAWS, n))].mean(axis=1)
        out["net_mean_ci95"] = [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]
    return out


def passes(result: dict) -> bool:
    """Critere du protocole : 100 trades, gain net moyen positif borne basse comprise,
    positif dans chaque moitie avec au moins 30 trades."""
    a, h1, h2 = result["all"], result["first_half"], result["second_half"]
    return bool(a["n"] >= 100 and a.get("net_mean_ci95", [-1])[0] > 0
                and h1["n"] >= 30 and h2["n"] >= 30 and h1["net_mean"] > 0 and h2["net_mean"] > 0)


def run(bars: Bars, period: str, events_utc=None) -> dict:
    """Mesure les douze configurations sur une periode. Les bougies posterieures a la
    periode sont ecartees avant tout calcul."""
    start, end = PERIODS[period]
    lo = int(datetime.fromisoformat(start).replace(tzinfo=UTC).timestamp())
    hi = int((datetime.fromisoformat(end) + timedelta(days=1)).replace(tzinfo=UTC).timestamp())
    keep = bars.t < hi
    bars = Bars(bars.t[keep], bars.o[keep], bars.h[keep], bars.l[keep], bars.c[keep], bars.spread[keep], True)
    f = tk.compute_features(bars, events_utc)
    in_period = bars.t >= lo
    day = tk.sessions(bars.t)["trading_day"]
    middle = (lo + hi) // 2
    rng = np.random.default_rng(SEED)
    results = []
    for rule, name, params, reward, max_minutes in CONFIGS:
        signal = build_signal(rule, bars, f, params)
        trades = simulate(bars, signal, STOP_ATR * f["atr_m15"], reward, max_minutes, allowed=in_period,
                          one_per_day=day if rule == "H5" else None)
        net, t = trades["net"], trades["entry_t"]
        years = np.array([datetime.fromtimestamp(int(x), UTC).year for x in t], dtype=np.int64)
        res = {"rule": rule, "name": name, "params": params, "reward": reward, "max_minutes": max_minutes,
               "all": stats(net, rng), "first_half": stats(net[t < middle]), "second_half": stats(net[t >= middle]),
               "by_year": {str(y): stats(net[years == y]) for y in sorted(set(years.tolist()))},
               "long": stats(net[trades["side"] > 0]), "short": stats(net[trades["side"] < 0]),
               "gross_mean": float(trades["gross"].mean()) if len(net) else None,
               "cost_mean": float(trades["cost"].mean()) if len(net) else None,
               "stop_distance_mean": float(trades["stop_distance"].mean()) if len(net) else None,
               "exits": {r: int((trades["reason"] == r).sum()) for r in ("stop", "objectif", "duree")}}
        res["passes"] = passes(res)
        results.append(res)
    return {"period": period, "period_bounds": [start, end], "bars_in_period": int(in_period.sum()),
            "first_bar_utc": _iso(bars.t[in_period][0]) if in_period.any() else None,
            "last_bar_utc": _iso(bars.t[-1]), "configs_tested": len(results),
            "configs_passing": sum(r["passes"] for r in results), "results": results}


# ------------------------------------------------------------------------ CLI
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Mesure des regles d'entree H4 a H6.")
    parser.add_argument("--bars", required=True)
    parser.add_argument("--store", required=True, help="base Market Intelligence (annonces majeures)")
    parser.add_argument("--offset-rule", required=True, choices=list(OFFSET_RULES))
    parser.add_argument("--period", required=True, choices=list(PERIODS))
    parser.add_argument("--out", default="data/research")
    args = parser.parse_args(argv)
    if args.period == "final_test":
        print("La periode de test final (2026) est verrouillee : elle ne s'ouvre qu'une fois, pour une "
              "configuration retenue sur le developpement et la validation.", file=sys.stderr)
        return 2
    bars_path = Path(args.bars)
    meta = load_meta(bars_path.with_name(bars_path.name[:-len(".csv")] + ".meta.csv"))
    bars = to_utc(load_bars(bars_path, float(meta["point"])), args.offset_rule)
    report = run(bars, args.period, high_impact_times_from_store(Path(args.store)))
    report.update({"generated_at": _iso(datetime.now(UTC).timestamp()), "offset_rule": args.offset_rule,
                   "symbol": meta.get("symbol"), "bars_file": bars_path.name})
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = f"entry_rules_{args.period}_{datetime.now(UTC).date().isoformat()}.json"
    (out / name).write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    append_registry(out / "registry.jsonl", {"run_at": report["generated_at"], "study": "entry_rules_h4_h6",
                                             "period": args.period, "bars_file": bars_path.name,
                                             "offset_rule": args.offset_rule, "configs_tested": report["configs_tested"],
                                             "configs_passing": report["configs_passing"], "report": name})
    print(f"{report['bars_in_period']} bougies sur la periode {args.period} ; "
          f"{report['configs_passing']} configuration(s) sur {report['configs_tested']} passent le critere.")
    for r in report["results"]:
        a = r["all"]
        if a["n"] == 0:
            print(f"  {r['name']:<42} aucun trade")
            continue
        ci = a.get("net_mean_ci95", [float("nan")] * 2)
        pf = f"{a['profit_factor']:.2f}" if a["profit_factor"] is not None else "n/a"
        print(f"  {r['name']:<42} trades {a['n']:>5}  gagnants {a['win_rate']:.0%}  net moyen {a['net_mean']:+.3f} "
              f"[{ci[0]:+.3f} ; {ci[1]:+.3f}]  PF {pf}  {'PASSE' if r['passes'] else 'ne passe pas'}")
    print(f"Rapport : {out / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
