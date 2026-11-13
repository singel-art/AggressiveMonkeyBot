"""Couche technique : ce que le marche montre a un instant donne, sur plusieurs unites de temps.

Ce module MESURE. Il ne contient aucune regle d'entree ou de sortie et ne dit pas
si une mesure est utile : cela se teste a part, avec un protocole fige.

Regle unique, verifiee par les tests : la valeur d'une mesure a la bougie M1 `i`
n'utilise que ce qui est connu A LA CLOTURE de cette bougie. Une bougie M5, M15,
H1 ou H4 n'est utilisee qu'une fois terminee ; un sommet ou un creux n'existe
qu'une fois confirme par les bougies qui le suivent.

Les bougies superieures sont reconstruites a partir du M1, alignees sur l'heure
UTC. Celles de MetaTrader sont alignees sur l'heure du serveur : les H4 peuvent
donc differer de celles du graphique MT5.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from amb_research.impulse_study import (OFFSET_RULES, PERIODS, UTC, Bars, atr, ema,
                                        high_impact_times_from_store, load_bars, load_meta, to_utc)

TREND_FRAMES = {"m15": (15, 20, 50), "h1": (60, 20, 50), "h4": (240, 50, 200)}   # minutes, EMA rapide, EMA lente
STRUCTURE_FRAMES = {"m5": 5, "m15": 15, "h1": 60}
SWING_WIDTH = 2                  # bougies de chaque cote pour confirmer un sommet ou un creux
RSI_PERIOD = 14
VOL_BASELINE_M15 = 96 * 20       # 20 jours de bougies M15 pour situer la volatilite
SPREAD_BASELINE_M1 = 1440        # 1 jour de bougies M1 pour situer le spread
SESSION_NAMES = {0: "asie", 1: "londres", 2: "londres+new_york", 3: "new_york"}
HOUR = 3600
DAY = 86400


# ------------------------------------------------------------ unites de temps
@dataclass
class Frame:
    """Bougies d'une unite superieure, et pour chaque bougie M1 l'indice de la derniere
    bougie superieure TERMINEE a la cloture de cette bougie M1 (-1 s'il n'y en a pas)."""
    bars: Bars
    closed_pos: np.ndarray


def resample(bars: Bars, minutes: int) -> Frame:
    span = minutes * 60
    bucket = bars.t // span
    first = np.flatnonzero(np.concatenate([[True], bucket[1:] != bucket[:-1]]))
    last = np.concatenate([first[1:] - 1, [len(bars) - 1]])
    upper = Bars(bucket[first] * span, bars.o[first], np.maximum.reduceat(bars.h, first),
                 np.minimum.reduceat(bars.l, first), bars.c[last], bars.spread[last], bars.in_utc)
    # Une bougie superieure est terminee quand l'instant de cloture de la bougie M1 atteint sa fin.
    closed_pos = np.searchsorted(bucket[first], (bars.t + 60) // span, side="left") - 1
    return Frame(upper, closed_pos)


def at_m1(values: np.ndarray, frame: Frame, min_pos: int = 0) -> np.ndarray:
    """Projette une serie calculee sur l'unite superieure vers les bougies M1."""
    out = np.full(len(frame.closed_pos), np.nan)
    ok = frame.closed_pos >= min_pos
    out[ok] = values[frame.closed_pos[ok]]
    return out


# ------------------------------------------------------------------- outils
def ffill(values: np.ndarray) -> np.ndarray:
    """Reporte la derniere valeur connue (non NaN) vers l'avant."""
    idx = np.maximum.accumulate(np.where(np.isnan(values), -1, np.arange(len(values))))
    return np.where(idx >= 0, values[np.maximum(idx, 0)], np.nan)


def trailing_mean(values: np.ndarray, window: int, min_count: int) -> np.ndarray:
    """Moyenne des `window` dernieres valeurs (bougie courante incluse), NaN ignores."""
    ok = ~np.isnan(values)
    csum = np.concatenate([[0.0], np.cumsum(np.where(ok, values, 0.0))])
    ccnt = np.concatenate([[0], np.cumsum(ok)])
    hi = np.arange(1, len(values) + 1)
    lo = np.maximum(hi - window, 0)
    count = ccnt[hi] - ccnt[lo]
    out = np.full(len(values), np.nan)
    good = count >= min_count
    out[good] = (csum[hi] - csum[lo])[good] / count[good]
    return out


def rsi(closes: np.ndarray, period: int = RSI_PERIOD) -> np.ndarray:
    """RSI de Wilder. NaN tant que l'historique manque."""
    out = np.full(len(closes), np.nan)
    if len(closes) <= period:
        return out
    delta = np.diff(closes)
    gain, loss = np.maximum(delta, 0.0), np.maximum(-delta, 0.0)
    avg_gain, avg_loss = gain[:period].mean(), loss[:period].mean()
    for i in range(period, len(closes)):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gain[i - 1]) / period
            avg_loss = (avg_loss * (period - 1) + loss[i - 1]) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


# ------------------------------------------------------------------ tendance
def trend(frame: Frame, fast: int, slow: int) -> tuple[np.ndarray, np.ndarray]:
    """Sens (+1, -1, 0) et force de la tendance : EMA rapide moins EMA lente, la force
    etant exprimee en ATR de l'unite de temps. 0 et NaN tant que l'historique manque."""
    gap = ema(frame.bars.c, fast) - ema(frame.bars.c, slow)
    strength = at_m1(gap / atr(frame.bars), frame, min_pos=slow)
    return np.nan_to_num(np.sign(strength)), strength


# ----------------------------------------------------------------- structure
def swings(bars: Bars, width: int = SWING_WIDTH) -> tuple[np.ndarray, np.ndarray]:
    """Dernier sommet et dernier creux CONFIRMES, connus a la cloture de chaque bougie.
    Un sommet est le plus haut de `width` bougies de chaque cote : il n'est connu que
    `width` bougies plus tard, et c'est a ce moment-la qu'il apparait ici."""
    n = len(bars)
    high_known, low_known = np.full(n, np.nan), np.full(n, np.nan)
    if n >= 2 * width + 1:
        centre = np.arange(width, n - width)
        is_high = bars.h[centre] == sliding_window_view(bars.h, 2 * width + 1).max(axis=1)
        is_low = bars.l[centre] == sliding_window_view(bars.l, 2 * width + 1).min(axis=1)
        high_known[centre[is_high] + width] = bars.h[centre[is_high]]
        low_known[centre[is_low] + width] = bars.l[centre[is_low]]
    return ffill(high_known), ffill(low_known)


def structure(bars: Bars, width: int = SWING_WIDTH) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Etat de la structure : +1 depuis la derniere cloture au-dessus du dernier sommet
    confirme, -1 depuis la derniere cloture sous le dernier creux confirme, 0 avant."""
    high, low = swings(bars, width)
    event = np.where(bars.c > high, 1.0, np.where(bars.c < low, -1.0, np.nan))
    return np.nan_to_num(ffill(event)), high, low


# ------------------------------------------------------- sessions et niveaux
def _offset(t: np.ndarray, zone: str) -> np.ndarray:
    """Decalage local - UTC en secondes. Calcule une fois par jour UTC (a midi) : les
    changements d'heure ont lieu le dimanche avant l'ouverture du marche."""
    days, inverse = np.unique(t // DAY, return_inverse=True)
    tz = ZoneInfo(zone)
    offsets = np.array([int(datetime.fromtimestamp(int(d) * DAY + DAY // 2, tz).utcoffset().total_seconds())
                        for d in days], dtype=np.int64)
    return offsets[inverse]


def sessions(t: np.ndarray) -> dict[str, np.ndarray]:
    """Session en cours et journee de trading. Londres et New York : 08h00-17h00 heure
    locale. La journee de trading change a 17h00 de New York, comme chez les brokers."""
    ny = t + _offset(t, "America/New_York")
    london_clock = (t + _offset(t, "Europe/London")) % DAY
    ny_clock = ny % DAY
    in_london = (london_clock >= 8 * HOUR) & (london_clock < 17 * HOUR)
    in_ny = (ny_clock >= 8 * HOUR) & (ny_clock < 17 * HOUR)
    since_day_start = (ny + 7 * HOUR) % DAY
    before_london = (since_day_start < 12 * HOUR) & ((london_clock < 8 * HOUR) | (london_clock >= 20 * HOUR))
    return {"session": np.where(in_london & in_ny, 2, np.where(in_london, 1, np.where(in_ny, 3, 0))),
            "trading_day": (ny + 7 * HOUR) // DAY, "before_london": before_london}


def day_levels(bars: Bars, trading_day: np.ndarray, before_london: np.ndarray) -> dict[str, np.ndarray]:
    """Plus haut, plus bas et cloture de la journee de trading PRECEDENTE ; plus haut et
    plus bas de la session asiatique du jour, connus seulement a partir de l'ouverture de Londres."""
    first = np.flatnonzero(np.concatenate([[True], trading_day[1:] != trading_day[:-1]]))
    last = np.concatenate([first[1:] - 1, [len(bars) - 1]])
    group = np.cumsum(np.concatenate([[True], trading_day[1:] != trading_day[:-1]])) - 1

    def previous(per_day: np.ndarray) -> np.ndarray:
        return np.concatenate([[np.nan], per_day[:-1]])[group]

    asia_high = np.maximum.reduceat(np.where(before_london, bars.h, -np.inf), first)[group]
    asia_low = np.minimum.reduceat(np.where(before_london, bars.l, np.inf), first)[group]
    known = ~before_london & np.isfinite(asia_high)
    return {"prev_day_high": previous(np.maximum.reduceat(bars.h, first)),
            "prev_day_low": previous(np.minimum.reduceat(bars.l, first)),
            "prev_day_close": previous(bars.c[last]),
            "asia_high": np.where(known, asia_high, np.nan),
            "asia_low": np.where(known, asia_low, np.nan)}


def event_distance(t_close: np.ndarray, events_utc) -> tuple[np.ndarray, np.ndarray]:
    """Minutes jusqu'a la prochaine annonce majeure et depuis la precedente (NaN s'il n'y en a pas).
    Les annonces sont programmees a l'avance : connaitre la prochaine n'est pas lire le futur."""
    events = np.unique(np.asarray(events_utc if events_utc is not None else [], dtype=np.int64))
    until, since = np.full(len(t_close), np.nan), np.full(len(t_close), np.nan)
    if len(events):
        nxt = np.searchsorted(events, t_close, side="left")
        prev = np.searchsorted(events, t_close, side="right") - 1
        has_next, has_prev = nxt < len(events), prev >= 0
        until[has_next] = (events[nxt[has_next]] - t_close[has_next]) / 60.0
        since[has_prev] = (t_close[has_prev] - events[prev[has_prev]]) / 60.0
    return until, since


# ------------------------------------------------------------------ ensemble
def compute_features(bars: Bars, events_utc=None) -> dict[str, np.ndarray]:
    """Toutes les mesures, une valeur par bougie M1, connues a la cloture de cette bougie."""
    if not bars.in_utc:
        raise ValueError("les bougies doivent etre en UTC (voir to_utc)")
    with np.errstate(divide="ignore", invalid="ignore"):
        return _compute(bars, events_utc)


def _compute(bars: Bars, events_utc) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {"t": bars.t, "close": bars.c}

    # Tendance par unite de temps, et nombre d'unites alignees.
    for name, (minutes, fast, slow) in TREND_FRAMES.items():
        out[f"trend_{name}"], out[f"trend_strength_{name}"] = trend(resample(bars, minutes), fast, slow)
    out["trend_sum"] = sum(out[f"trend_{name}"] for name in TREND_FRAMES)

    # Structure : derniers sommets et creux confirmes, sens de la derniere cassure.
    frames = {name: resample(bars, minutes) for name, minutes in STRUCTURE_FRAMES.items()}
    for name, frame in frames.items():
        state, high, low = structure(frame.bars)
        out[f"structure_{name}"] = np.nan_to_num(at_m1(state, frame))
        out[f"swing_high_{name}"], out[f"swing_low_{name}"] = at_m1(high, frame), at_m1(low, frame)

    # Volatilite et spread, situes par rapport a leur niveau habituel.
    out["atr_m1"] = atr(bars)
    atr_m15 = atr(frames["m15"].bars)
    out["atr_m15"] = at_m1(atr_m15, frames["m15"])
    out["vol_ratio"] = at_m1(atr_m15 / trailing_mean(atr_m15, VOL_BASELINE_M15, 96 * 5), frames["m15"])
    out["spread"] = bars.spread
    out["spread_ratio"] = bars.spread / trailing_mean(bars.spread, SPREAD_BASELINE_M1, 240)

    # Momentum : RSI M5 et variation recente du prix, en ATR M1.
    out["rsi_m5"] = at_m1(rsi(frames["m5"].bars.c), frames["m5"])
    for k in (5, 15):
        move = np.full(len(bars), np.nan)
        move[k:] = bars.c[k:] - bars.c[:-k]
        out[f"move_{k}"] = move / out["atr_m1"]

    # Session, niveaux de la veille et de l'Asie, distance aux annonces.
    out["session"] = sessions(bars.t + 60)["session"]          # a l'instant de cloture de la bougie
    by_open = sessions(bars.t)
    out.update(day_levels(bars, by_open["trading_day"], by_open["before_london"]))
    out["minutes_to_event"], out["minutes_since_event"] = event_distance(bars.t + 60, events_utc)
    return out


def snapshot(features: dict[str, np.ndarray], i: int) -> dict:
    """Les mesures d'une bougie, sous forme lisible."""
    row = {}
    for key, values in features.items():
        v = values[i]
        if key == "t":
            row["bar_open_utc"] = datetime.fromtimestamp(int(v), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        elif key == "session":
            row[key] = SESSION_NAMES[int(v)]
        else:
            row[key] = None if np.isnan(v) else round(float(v), 4)
    return row


# ------------------------------------------------------------------------ CLI
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Affiche les mesures techniques a un instant passe.")
    parser.add_argument("--bars", required=True, help="export de AMB_ExportBars.mq5")
    parser.add_argument("--offset-rule", required=True, choices=list(OFFSET_RULES),
                        help="regle de conversion heure serveur -> UTC (voir le protocole H1-H3)")
    parser.add_argument("--at", required=True, help="instant UTC, par exemple 2024-06-12T14:30")
    parser.add_argument("--store", help="base Market Intelligence, pour la distance aux annonces")
    args = parser.parse_args(argv)

    at = int(datetime.fromisoformat(args.at.replace("Z", "")).replace(tzinfo=UTC).timestamp())
    locked_from = int(datetime.fromisoformat(PERIODS["final_test"][0]).replace(tzinfo=UTC).timestamp())
    if at >= locked_from:
        print("La periode de test final (2026) est verrouillee : aucune mesure n'y est affichee.", file=sys.stderr)
        return 2
    bars_path = Path(args.bars)
    meta = load_meta(bars_path.with_name(bars_path.name[:-len(".csv")] + ".meta.csv"))
    bars = to_utc(load_bars(bars_path, float(meta["point"])), args.offset_rule)
    keep = bars.t + 60 <= at                          # rien apres l'instant demande n'est charge
    if not keep.any():
        print("Aucune bougie avant cet instant.", file=sys.stderr)
        return 2
    bars = Bars(bars.t[keep], bars.o[keep], bars.h[keep], bars.l[keep], bars.c[keep], bars.spread[keep], True)
    events = high_impact_times_from_store(Path(args.store)) if args.store else None
    print(json.dumps(snapshot(compute_features(bars, events), len(bars) - 1), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
