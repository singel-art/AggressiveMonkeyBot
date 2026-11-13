"""Mesure des regles H8 a H10 sur M15 et H1 (protocole docs/research/H8_H10_PROTOCOL.md).

Les signaux sont evalues a la cloture d'une bougie M15 ou H1 terminee ; la
simulation (stop, objectif, duree) se fait sur les bougies M1. Douze
configurations fixees avant la mesure. C'est une mesure, pas un robot.
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
from amb_research.entry_rules import common_filter, passes, simulate, stats
from amb_research.impulse_study import (OFFSET_RULES, PERIODS, SEED, UTC, Bars, _iso, append_registry, atr,
                                        high_impact_times_from_store, load_bars, load_meta, to_utc)

# (regle, nom, parametres du signal, stop en ATR H1, objectif en multiples du stop, duree maximale en heures)
CONFIGS = (
    [("H8", f"H8 canal H1 N={n} duree={d}h", {"n": n}, 2.0, np.inf, d) for n in (20, 55) for d in (24, 72)]
    + [("H9", f"H9 repli M15 S={s} R={r:g}", {"rsi": s}, 1.5, r, 48) for s in (30, 40) for r in (1.5, 3.0)]
    + [("H10", f"H10 retour moyenne H1 K={k:g} duree={d}h", {"k": k}, 2.0, 1.0, d) for k in (2.0, 2.5) for d in (6, 24)]
)


def on_close(frame: tk.Frame, direction: np.ndarray) -> np.ndarray:
    """Porte un signal calcule sur l'unite superieure vers la bougie M1 qui la termine."""
    pos = frame.closed_pos
    fresh = np.concatenate([[False], pos[1:] != pos[:-1]]) & (pos >= 0)
    out = np.zeros(len(pos), dtype=np.int64)
    out[fresh] = direction[pos[fresh]]
    return out


def _previous_extreme(values: np.ndarray, n: int, fn) -> np.ndarray:
    """Maximum ou minimum des n valeurs PRECEDENTES (bougie courante exclue)."""
    out = np.full(len(values), np.nan)
    if len(values) > n:
        out[n:] = fn(sliding_window_view(values, n), axis=1)[:-1]
    return out


def channel_breakout(h1: Bars, n: int) -> np.ndarray:
    """H8 : cloture H1 au-dela du plus haut ou du plus bas des n bougies precedentes."""
    with np.errstate(invalid="ignore"):
        return np.where(h1.c > _previous_extreme(h1.h, n, np.max), 1,
                        np.where(h1.c < _previous_extreme(h1.l, n, np.min), -1, 0))


def pullback_m15(m15: Bars, rsi: float) -> np.ndarray:
    """H9, partie M15 : RSI passe sous le seuil dans les 8 dernieres bougies, puis cloture
    au-dela de l'extreme de la bougie precedente. La tendance est verifiee a part."""
    r = np.where(np.isnan(tk.rsi(m15.c)), 50.0, tk.rsi(m15.c))
    low, high = np.full(len(r), np.nan), np.full(len(r), np.nan)
    if len(r) >= 8:
        low[7:], high[7:] = sliding_window_view(r, 8).min(axis=1), sliding_window_view(r, 8).max(axis=1)
    prev_high = np.concatenate([[np.inf], m15.h[:-1]])
    prev_low = np.concatenate([[-np.inf], m15.l[:-1]])
    with np.errstate(invalid="ignore"):
        return np.where((low <= rsi) & (m15.c > prev_high), 1, np.where((high >= 100 - rsi) & (m15.c < prev_low), -1, 0))


def mean_reversion(h1: Bars, k: float, window: int = 20) -> np.ndarray:
    """H10 : cloture H1 a plus de k ecarts-types de la moyenne des 20 dernieres clotures."""
    out = np.zeros(len(h1), dtype=np.int64)
    if len(h1) >= window:
        w = sliding_window_view(h1.c, window)
        mean, std = w.mean(axis=1), w.std(axis=1)
        c = h1.c[window - 1:]
        out[window - 1:] = np.where(c < mean - k * std, 1, np.where(c > mean + k * std, -1, 0))
    return out


def build_signal(rule: str, params: dict, f: dict, m15: tk.Frame, h1: tk.Frame) -> np.ndarray:
    if rule == "H8":
        raw = on_close(h1, channel_breakout(h1.bars, **params))
    elif rule == "H10":
        raw = on_close(h1, mean_reversion(h1.bars, **params))
    else:
        raw = on_close(m15, pullback_m15(m15.bars, **params))
        raw = np.where((f["trend_h1"] == raw) & (f["trend_h4"] == raw), raw, 0)
    return np.where(common_filter(f), raw, 0)


def run(bars: Bars, period: str, events_utc=None) -> dict:
    start, end = PERIODS[period]
    lo = int(datetime.fromisoformat(start).replace(tzinfo=UTC).timestamp())
    hi = int((datetime.fromisoformat(end) + timedelta(days=1)).replace(tzinfo=UTC).timestamp())
    keep = bars.t < hi
    bars = Bars(bars.t[keep], bars.o[keep], bars.h[keep], bars.l[keep], bars.c[keep], bars.spread[keep], True)
    f = tk.compute_features(bars, events_utc)
    m15, h1 = tk.resample(bars, 15), tk.resample(bars, 60)
    atr_h1 = tk.at_m1(atr(h1.bars), h1)
    f = dict(f, atr_m15=atr_h1)                       # le filtre commun exige que l'ATR du stop soit connu
    in_period = bars.t >= lo
    middle = (lo + hi) // 2
    rng = np.random.default_rng(SEED)
    results = []
    for rule, name, params, stop_atr, reward, hours in CONFIGS:
        signal = build_signal(rule, params, f, m15, h1)
        trades = simulate(bars, signal, stop_atr * atr_h1, reward, hours * 60, allowed=in_period)
        net, t = trades["net"], trades["entry_t"]
        years = np.array([datetime.fromtimestamp(int(x), UTC).year for x in t], dtype=np.int64)
        res = {"rule": rule, "name": name, "all": stats(net, rng), "first_half": stats(net[t < middle]),
               "second_half": stats(net[t >= middle]),
               "by_year": {str(y): stats(net[years == y]) for y in sorted(set(years.tolist()))},
               "long": stats(net[trades["side"] > 0]), "short": stats(net[trades["side"] < 0]),
               "gross_mean": float(trades["gross"].mean()) if len(net) else None,
               "cost_mean": float(trades["cost"].mean()) if len(net) else None,
               "stop_distance_mean": float(trades["stop_distance"].mean()) if len(net) else None,
               "exits": {r: int((trades["reason"] == r).sum()) for r in ("stop", "objectif", "duree")}}
        res["passes"] = passes(res)
        results.append(res)
    return {"period": period, "period_bounds": [start, end], "bars_in_period": int(in_period.sum()),
            "last_bar_utc": _iso(bars.t[-1]), "configs_tested": len(results),
            "configs_passing": sum(r["passes"] for r in results), "results": results}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Mesure des regles H8 a H10 (M15 et H1).")
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
    report = run(bars, args.period, high_impact_times_from_store(Path(args.store)))
    report.update({"generated_at": _iso(datetime.now(UTC).timestamp()), "offset_rule": args.offset_rule,
                   "bars_file": bars_path.name})
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = f"htf_rules_{args.period}_{datetime.now(UTC).date().isoformat()}.json"
    (out / name).write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    append_registry(out / "registry.jsonl", {"run_at": report["generated_at"], "study": "htf_rules_h8_h10",
                                             "period": args.period, "bars_file": bars_path.name,
                                             "offset_rule": args.offset_rule, "configs_tested": report["configs_tested"],
                                             "configs_passing": report["configs_passing"], "report": name})
    print(f"{report['configs_passing']} configuration(s) sur {report['configs_tested']} passent le critere "
          f"sur la periode {args.period}.")
    for r in report["results"]:
        a = r["all"]
        if a["n"] == 0:
            print(f"  {r['name']:<36} aucun trade")
            continue
        ci = a.get("net_mean_ci95", [float("nan")] * 2)
        pf = f"{a['profit_factor']:.2f}" if a["profit_factor"] is not None else "n/a"
        side = " ".join(f"{k} {r[k]['n']}/{r[k].get('net_mean', float('nan')):+.2f}" for k in ("long", "short"))
        print(f"  {r['name']:<36} trades {a['n']:>4} gagn. {a['win_rate']:.0%} brut {r['gross_mean']:+.2f} cout "
              f"{r['cost_mean']:.2f} net {a['net_mean']:+.2f} [{ci[0]:+.2f};{ci[1]:+.2f}] PF {pf} "
              f"moities {r['first_half'].get('net_mean', float('nan')):+.2f}/{r['second_half'].get('net_mean', float('nan')):+.2f} "
              f"{side} {'PASSE' if r['passes'] else 'ne passe pas'}")
    print(f"Rapport : {out / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
