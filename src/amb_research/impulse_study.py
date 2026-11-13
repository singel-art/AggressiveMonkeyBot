"""Etude statistique des impulsions M1 (etape 0 du plan : hypotheses H1 a H3).

Question posee : apres une impulsion M1, le prix continue-t-il dans le meme sens,
d'un montant superieur au cout d'execution ? Ce module mesure ; il ne trade pas
et ne contient aucune strategie.

Le protocole (signal, grille de parametres, periodes, critere de passage) est
fixe AVANT de voir les donnees, dans docs/research/H1_H3_PROTOCOL.md. La periode
de test final est verrouillee : elle ne se calcule qu'avec une option explicite,
et chaque execution est inscrite dans un registre.

Conventions de prix (MetaTrader 5) : les bougies sont des prix BID. Un achat
s'execute a l'ASK = bid + spread, une vente au bid. Le spread d'une bougie M1
est celui que MT5 enregistre pour cette bougie : c'est une approximation du
spread reellement paye, a confirmer plus tard sur des ticks reels.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

UTC = timezone.utc
HORIZONS = (1, 5, 15, 60)                 # minutes apres l'entree
GRID = [(n, k) for n in (5, 10, 20) for k in (0.5, 1.0, 1.5)]
ATR_PERIOD = 14
# Periodes fixees par le protocole. Bornes incluses, en dates UTC.
PERIODS = {
    "development": ("2023-01-01", "2024-12-31"),
    "validation": ("2025-01-01", "2025-12-31"),
    "final_test": ("2026-01-01", "2026-12-31"),
}
PASS_RATIO = 2.0                           # mouvement brut moyen >= 2 x cout moyen
BOOTSTRAP_DRAWS = 2000
SEED = 20261113


# ------------------------------------------------------------------ chargement
@dataclass
class Bars:
    t: np.ndarray        # debut de bougie, secondes depuis 1970, heure du SERVEUR puis UTC apres conversion
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    spread: np.ndarray   # en unites de prix (points x taille du point)
    in_utc: bool = False

    def __len__(self) -> int:
        return len(self.t)


def load_meta(path: Path) -> dict[str, str]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return {row["key"]: row["value"] for row in csv.DictReader(handle, delimiter=";")}


def load_bars(path: Path, point: float) -> Bars:
    """Lit l'export de AMB_ExportBars.mq5. Refuse un fichier dont les colonnes, l'ordre
    des dates ou la coherence des prix ne sont pas ceux attendus."""
    expected = ["time_server", "open", "high", "low", "close", "tick_volume", "spread_points", "real_volume"]
    t, o, h, l, c, s = [], [], [], [], [], []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle, delimiter=";")
        header = next(reader, None)
        if header != expected:
            raise ValueError(f"colonnes inattendues : {header}")
        for n, row in enumerate(reader, start=2):
            if len(row) != len(expected):
                raise ValueError(f"ligne {n} : nombre de colonnes incorrect")
            stamp = datetime.strptime(row[0], "%Y.%m.%d %H:%M:%S").replace(tzinfo=UTC)
            t.append(int(stamp.timestamp()))
            o.append(float(row[1])); h.append(float(row[2])); l.append(float(row[3])); c.append(float(row[4]))
            s.append(float(row[6]) * point)
    bars = Bars(np.array(t, dtype=np.int64), np.array(o), np.array(h), np.array(l), np.array(c), np.array(s))
    if len(bars) == 0:
        raise ValueError("fichier vide")
    if np.any(np.diff(bars.t) <= 0):
        raise ValueError("dates non strictement croissantes")
    if np.any(bars.h < np.maximum(bars.o, bars.c)) or np.any(bars.l > np.minimum(bars.o, bars.c)) \
            or np.any(bars.spread < 0):
        raise ValueError("bougie incoherente (haut, bas ou spread)")
    return bars


# ------------------------------------------------------- heure serveur -> UTC
def _us_dst(utc_seconds: np.ndarray) -> np.ndarray:
    ny = ZoneInfo("America/New_York")
    return np.array([bool(datetime.fromtimestamp(int(x), ny).dst()) for x in utc_seconds])


def _eu_dst(utc_seconds: np.ndarray) -> np.ndarray:
    eu = ZoneInfo("Europe/Berlin")
    return np.array([bool(datetime.fromtimestamp(int(x), eu).dst()) for x in utc_seconds])


OFFSET_RULES = {
    # Ecart serveur - UTC, en heures, selon la date.
    "utc+2_fixe": lambda t: np.full(len(t), 2.0),
    "utc+3_fixe": lambda t: np.full(len(t), 3.0),
    "utc+2/+3_heure_ete_US": lambda t: 2.0 + _us_dst(t),
    "utc+2/+3_heure_ete_UE": lambda t: 2.0 + _eu_dst(t),
    "utc_fixe": lambda t: np.zeros(len(t)),
}


def observed_offsets(bars: Bars, event_times_utc: list[int], window_hours: int = 6) -> list[tuple[int, float]]:
    """Pour chaque evenement dont l'heure UTC reelle est connue (publication de l'emploi
    americain), cherche la bougie M1 la plus ample autour de cette heure et en deduit
    l'ecart serveur - UTC ce jour-la. Renvoie [(instant UTC, ecart en heures)]."""
    out = []
    rng = bars.h - bars.l
    for ev in event_times_utc:
        lo, hi = np.searchsorted(bars.t, [ev - window_hours * 3600, ev + window_hours * 3600])
        if hi - lo < 60:
            continue
        i = lo + int(np.argmax(rng[lo:hi]))
        out.append((ev, round((int(bars.t[i]) - ev) / 1800.0) / 2.0))
    return out


def choose_offset_rule(observations: list[tuple[int, float]]) -> dict:
    """Compare les regles candidates aux ecarts observes et retient la meilleure.
    Le resultat donne le taux d'accord de chaque regle : a lire avant de faire confiance."""
    if not observations:
        return {"rule": None, "agreement": {}, "events": 0}
    times = np.array([t for t, _ in observations])
    seen = np.array([o for _, o in observations])
    agreement = {name: float(np.mean(rule(times) == seen)) for name, rule in OFFSET_RULES.items()}
    best = max(agreement, key=agreement.get)
    return {"rule": best, "agreement": agreement, "events": len(observations)}


def to_utc(bars: Bars, rule: str) -> Bars:
    """Convertit les heures serveur en UTC. La regle depend de l'instant UTC, lui-meme
    inconnu : on part de l'ecart d'hiver puis on corrige une fois, ce qui suffit hors
    de l'heure de bascule."""
    fn = OFFSET_RULES[rule]
    guess = bars.t - 2 * 3600
    utc = bars.t - (fn(guess) * 3600).astype(np.int64)
    utc = bars.t - (fn(utc) * 3600).astype(np.int64)
    keep = np.concatenate([[True], np.diff(utc) > 0])     # heure doublee au changement d'heure
    return Bars(utc[keep], bars.o[keep], bars.h[keep], bars.l[keep], bars.c[keep], bars.spread[keep], True)


# -------------------------------------------------------------------- calculs
def atr(bars: Bars, period: int = ATR_PERIOD) -> np.ndarray:
    """Moyenne simple de l'amplitude vraie sur `period` bougies. NaN tant que l'historique manque."""
    prev_close = np.concatenate([[bars.c[0]], bars.c[:-1]])
    tr = np.maximum(bars.h - bars.l, np.maximum(np.abs(bars.h - prev_close), np.abs(bars.l - prev_close)))
    out = np.full(len(tr), np.nan)
    if len(tr) >= period:
        csum = np.cumsum(tr)
        out[period - 1:] = (csum[period - 1:] - np.concatenate([[0.0], csum[:-period]])) / period
    return out


def find_impulses(bars: Bars, lookback: int, k: float, atr_values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Impulsion = bougie qui cloture au-dela du plus haut (ou du plus bas) des `lookback`
    bougies precedentes, avec un corps d'au moins k fois l'ATR de la bougie precedente.

    Tout est connu a la cloture de la bougie signal : aucune donnee posterieure n'entre
    dans la detection. Renvoie (indices des bougies signal, direction +1 ou -1)."""
    n = len(bars)
    idx, direction = [], []
    if n <= lookback + 1:
        return np.array([], dtype=np.int64), np.array([], dtype=np.int64)
    windows_h = np.lib.stride_tricks.sliding_window_view(bars.h, lookback).max(axis=1)
    windows_l = np.lib.stride_tricks.sliding_window_view(bars.l, lookback).min(axis=1)
    # windows_h[j] couvre les bougies j .. j+lookback-1 ; la bougie signal est j+lookback.
    signal = np.arange(lookback, n)
    prior_high, prior_low = windows_h[: n - lookback], windows_l[: n - lookback]
    body = bars.c[signal] - bars.o[signal]
    ref_atr = atr_values[signal - 1]
    ok = ~np.isnan(ref_atr) & (ref_atr > 0)
    up = ok & (bars.c[signal] > prior_high) & (body >= k * ref_atr)
    down = ok & (bars.c[signal] < prior_low) & (-body >= k * ref_atr)
    idx = np.concatenate([signal[up], signal[down]])
    direction = np.concatenate([np.ones(up.sum(), dtype=np.int64), -np.ones(down.sum(), dtype=np.int64)])
    order = np.argsort(idx)
    return idx[order], direction[order]


def decluster(idx: np.ndarray, direction: np.ndarray, min_gap: int) -> tuple[np.ndarray, np.ndarray]:
    """Une seule position a la fois : apres un signal retenu, les suivants sont ignores
    pendant `min_gap` bougies."""
    keep, last = [], -10 ** 12
    for j, i in enumerate(idx):
        if i - last >= min_gap:
            keep.append(j)
            last = i
    keep = np.array(keep, dtype=np.int64)
    return idx[keep], direction[keep]


def forward_outcomes(bars: Bars, idx: np.ndarray, direction: np.ndarray, horizon: int) -> dict[str, np.ndarray]:
    """Resultat d'une entree a l'ouverture de la bougie qui suit le signal, sortie a la
    cloture `horizon` minutes plus tard.

    Achat  : entree a l'ask (bid + spread), sortie au bid.
    Vente  : entree au bid, sortie a l'ask (bid + spread de la bougie de sortie).
    Un signal dont la fenetre traverse un trou de cotation (week-end, coupure) est ecarte."""
    entry = idx + 1
    exit_ = entry + horizon - 1
    ok = exit_ < len(bars)
    entry, exit_, d = entry[ok], exit_[ok], direction[ok]
    continuous = (bars.t[exit_] - bars.t[entry]) == (horizon - 1) * 60
    entry, exit_, d = entry[continuous], exit_[continuous], d[continuous]
    gross = d * (bars.c[exit_] - bars.o[entry])
    cost = np.where(d > 0, bars.spread[entry], bars.spread[exit_])
    return {"entry_index": entry, "direction": d, "gross": gross, "cost": cost, "net": gross - cost}


def summarize(gross: np.ndarray, cost: np.ndarray, rng: np.random.Generator | None = None) -> dict:
    """Statistiques d'un ensemble de signaux. L'intervalle de confiance du resultat net
    moyen vient d'un reechantillonnage ; il suppose des signaux independants, ce qui
    n'est qu'approximatif."""
    n = int(len(gross))
    if n == 0:
        return {"n": 0}
    net = gross - cost
    out = {"n": n, "gross_mean": float(gross.mean()), "cost_mean": float(cost.mean()),
           "net_mean": float(net.mean()), "hit_rate_net": float((net > 0).mean()),
           "gross_to_cost": float(gross.mean() / cost.mean()) if cost.mean() > 0 else None}
    if n >= 30 and rng is not None:
        draws = rng.integers(0, n, size=(BOOTSTRAP_DRAWS, n))
        means = net[draws].mean(axis=1)
        out["net_mean_ci95"] = [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]
    return out


def ema(values: np.ndarray, period: int) -> np.ndarray:
    alpha = 2.0 / (period + 1.0)
    out = np.empty(len(values))
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def trend_at(bars: Bars, minutes: int, fast: int, slow: int) -> np.ndarray:
    """Tendance (+1, -1) d'une unite de temps superieure, vue depuis chaque bougie M1 :
    signe de EMA rapide - EMA lente calcule sur les bougies superieures DEJA CLOTUREES.
    La bougie superieure en cours n'est jamais utilisee."""
    bucket = bars.t // (minutes * 60)
    last_of_bucket = np.concatenate([bucket[1:] != bucket[:-1], [True]])
    closes = bars.c[last_of_bucket]
    sign = np.sign(ema(closes, fast) - ema(closes, slow))
    bucket_ids = bucket[last_of_bucket]
    # Pour chaque bougie M1 : derniere bougie superieure terminee avant elle.
    pos = np.searchsorted(bucket_ids, bucket, side="left") - 1
    out = np.zeros(len(bars))
    valid = pos >= max(slow, 1)                          # historique suffisant pour l'EMA lente
    out[valid] = sign[pos[valid]]
    return out


def period_mask(bars: Bars, name: str) -> np.ndarray:
    start, end = PERIODS[name]
    lo = int(datetime.fromisoformat(start).replace(tzinfo=UTC).timestamp())
    hi = int((datetime.fromisoformat(end) + timedelta(days=1)).replace(tzinfo=UTC).timestamp())
    return (bars.t >= lo) & (bars.t < hi)


def run_study(bars: Bars, period: str, *, blackout_utc: list[int] | None = None,
              blackout_minutes: int = 30) -> dict:
    """Execute la grille du protocole sur une periode. `bars` doit etre en UTC."""
    if not bars.in_utc:
        raise ValueError("les bougies doivent d'abord etre converties en UTC (to_utc)")
    rng = np.random.default_rng(SEED)
    atr_values = atr(bars)
    in_period = period_mask(bars, period)
    hours = (bars.t // 3600) % 24
    aligned = {"M15": trend_at(bars, 15, 20, 50), "H1": trend_at(bars, 60, 20, 50),
               "H4": trend_at(bars, 240, 50, 200)}
    near_event = np.zeros(len(bars), dtype=bool)
    if blackout_utc:
        ev = np.sort(np.array(blackout_utc, dtype=np.int64))
        pos = np.searchsorted(ev, bars.t)
        prev_gap = np.where(pos > 0, bars.t - ev[np.maximum(pos - 1, 0)], 10 ** 9)
        next_gap = np.where(pos < len(ev), ev[np.minimum(pos, len(ev) - 1)] - bars.t, 10 ** 9)
        near_event = np.minimum(prev_gap, next_gap) <= blackout_minutes * 60
    half = None
    if in_period.any():
        t_in = bars.t[in_period]
        half = int((t_in[0] + t_in[-1]) // 2)
    results = []
    for lookback, k in GRID:
        idx, direction = find_impulses(bars, lookback, k, atr_values)
        keep = in_period[idx]
        idx, direction = decluster(idx[keep], direction[keep], max(HORIZONS))
        config = {"lookback": lookback, "k_atr": k, "signals": int(len(idx)), "horizons": {}}
        for horizon in HORIZONS:
            res = forward_outcomes(bars, idx, direction, horizon)
            e, d = res["entry_index"], res["direction"]
            block = {"all": summarize(res["gross"], res["cost"], rng)}
            if half is not None:
                first = bars.t[e] < half
                block["first_half"] = summarize(res["gross"][first], res["cost"][first])
                block["second_half"] = summarize(res["gross"][~first], res["cost"][~first])
            # H2 : nombre d'unites de temps superieures alignees avec le signal (0 a 3).
            n_aligned = sum((aligned[tf][e] == d).astype(int) for tf in aligned)
            block["by_alignment"] = {str(a): summarize(res["gross"][n_aligned == a], res["cost"][n_aligned == a])
                                     for a in range(4)}
            # H3 : par heure UTC de l'entree.
            block["by_hour_utc"] = {str(hh): summarize(res["gross"][hours[e] == hh], res["cost"][hours[e] == hh])
                                    for hh in range(24) if (hours[e] == hh).any()}
            away = ~near_event[e]
            block["away_from_announcements"] = summarize(res["gross"][away], res["cost"][away])
            a = block["all"]
            both = [block.get("first_half", {}), block.get("second_half", {})]
            block["passes_criterion"] = bool(
                a.get("n", 0) >= 100 and a.get("gross_to_cost") is not None
                and a["gross_to_cost"] >= PASS_RATIO
                and all(h.get("n", 0) >= 30 and h.get("gross_mean", 0) > 0 for h in both))
            config["horizons"][str(horizon)] = block
        results.append(config)
    span = bars.t[in_period]
    return {"period": period, "period_bounds": PERIODS[period], "bars_in_period": int(in_period.sum()),
            "first_bar_utc": _iso(span[0]) if len(span) else None,
            "last_bar_utc": _iso(span[-1]) if len(span) else None,
            "grid": [{"lookback": n, "k_atr": k} for n, k in GRID], "horizons_minutes": list(HORIZONS),
            "pass_ratio": PASS_RATIO, "configs_tested": len(GRID) * len(HORIZONS),
            "configs_passing": sum(h["passes_criterion"] for c in results for h in c["horizons"].values()),
            "results": results}


def _iso(seconds) -> str:
    return datetime.fromtimestamp(int(seconds), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ registre
def append_registry(path: Path, entry: dict) -> None:
    """Chaque execution est notee, reussie ou non : plus on essaie de variantes, plus la
    meilleure doit a la chance, et ce registre permet d'en tenir compte."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def final_test_already_run(path: Path) -> bool:
    if not path.exists():
        return False
    with open(path, encoding="utf-8") as handle:
        return any(json.loads(line).get("period") == "final_test" for line in handle if line.strip())


def nfp_times_from_store(db_path: Path) -> list[int]:
    """Heures UTC reelles des publications de l'emploi americain (calendrier MT5 importe),
    pour verifier la conversion des heures des bougies."""
    import sqlite3
    db = sqlite3.connect(db_path)
    rows = db.execute("SELECT scheduled_at FROM events WHERE event_key LIKE 'mt5|840030016|%' "
                      "AND time_basis='published'").fetchall()
    db.close()
    return [int(datetime.strptime(r[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()) for r in rows]


def high_impact_times_from_store(db_path: Path) -> list[int]:
    import sqlite3
    db = sqlite3.connect(db_path)
    rows = db.execute("SELECT scheduled_at FROM events WHERE importance='high' AND kind<>'holiday'").fetchall()
    db.close()
    return sorted({int(datetime.strptime(r[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()) for r in rows})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Etude des impulsions M1 (protocole H1-H3)")
    parser.add_argument("--bars", required=True, help="export AMB_ExportBars (.csv) ; le .meta.csv doit etre a cote")
    parser.add_argument("--period", choices=list(PERIODS), default="development")
    parser.add_argument("--store", help="base market_intelligence.sqlite (heures reelles des annonces)")
    parser.add_argument("--offset-rule", choices=list(OFFSET_RULES),
                        help="regle heure serveur -> UTC ; par defaut, deduite des publications de l'emploi")
    parser.add_argument("--out", default="data/research", help="dossier des rapports (hors depot)")
    parser.add_argument("--unlock-final-test", action="store_true",
                        help="autorise le passage unique sur la periode de test final")
    args = parser.parse_args(argv)

    out = Path(args.out)
    registry = out / "registry.jsonl"
    if args.period == "final_test":
        if not args.unlock_final_test:
            print("La periode de test final est verrouillee. Elle ne se lance qu'une fois, apres gel "
                  "complet du protocole, avec --unlock-final-test.", file=sys.stderr)
            return 2
        if final_test_already_run(registry):
            print("Le test final a deja ete execute (voir le registre). Un second passage lui "
                  "retirerait sa valeur.", file=sys.stderr)
            return 2
    bars_path = Path(args.bars)
    meta = load_meta(bars_path.with_name(bars_path.name[:-len(".csv")] + ".meta.csv"))
    bars = load_bars(bars_path, float(meta["point"]))
    events, calibration = [], {"rule": None, "events": 0}
    blackout = None
    if args.store:
        events = nfp_times_from_store(Path(args.store))
        calibration = choose_offset_rule(observed_offsets(bars, events))
        blackout = high_impact_times_from_store(Path(args.store))
    rule = args.offset_rule or calibration["rule"]
    if rule is None:
        print("Impossible de deduire l'ecart horaire du serveur : fournir --store (calendrier MT5 importe) "
              "ou --offset-rule.", file=sys.stderr)
        return 2
    if not args.offset_rule and calibration["agreement"][rule] < 0.9:
        print(f"Conversion horaire incertaine : la meilleure regle ({rule}) n'explique que "
              f"{calibration['agreement'][rule]:.0%} des publications. Verifier avant de continuer, "
              "ou imposer --offset-rule.", file=sys.stderr)
        return 2
    report = run_study(to_utc(bars, rule), args.period, blackout_utc=blackout)
    report.update({"generated_at": _iso(datetime.now(UTC).timestamp()), "symbol": meta.get("symbol"),
                   "offset_rule": rule, "offset_calibration": calibration,
                   "spread_source": "colonne spread des bougies M1 (approximation)",
                   "bars_file": bars_path.name})
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"impulse_study_{args.period}_{date.today().isoformat()}.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    append_registry(registry, {"run_at": report["generated_at"], "period": args.period, "bars_file": bars_path.name,
                               "offset_rule": rule, "configs_tested": report["configs_tested"],
                               "configs_passing": report["configs_passing"], "report": target.name})
    print(f"{report['bars_in_period']} bougies sur la periode {args.period} ; "
          f"{report['configs_passing']} configuration(s) sur {report['configs_tested']} passent le critere.")
    print(f"Rapport : {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
