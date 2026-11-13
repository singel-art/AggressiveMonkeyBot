"""Couche technique : arithmetique et absence de fuite du futur.

Les bougies sont fabriquees (marche aleatoire a graine fixe ou construites a la
main). Aucun de ces tests ne dit quoi que ce soit sur le comportement reel de l'or.
"""
from datetime import datetime, timezone

import numpy as np
import pytest

from amb_research import technical as tk
from amb_research.impulse_study import Bars


def ts(*args):
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


def random_bars(n, seed=7, t0=ts(2023, 3, 6, 0, 0), gaps=True):
    """Marche aleatoire M1 avec des minutes manquantes, comme un vrai export."""
    rng = np.random.default_rng(seed)
    step = rng.choice([1, 1, 1, 1, 2, 7], size=n) if gaps else np.ones(n, dtype=np.int64)
    t = t0 + 60 * np.cumsum(step).astype(np.int64)
    c = 2000.0 + np.cumsum(rng.normal(0, 0.4, n))
    o = np.concatenate([[2000.0], c[:-1]])
    h = np.maximum(o, c) + rng.uniform(0, 0.3, n)
    l = np.minimum(o, c) - rng.uniform(0, 0.3, n)
    return Bars(t, o, h, l, c, rng.uniform(0.1, 0.4, n), True)


def cut(bars, n):
    return Bars(bars.t[:n], bars.o[:n], bars.h[:n], bars.l[:n], bars.c[:n], bars.spread[:n], True)


def minute_bars(t0, closes, highs=None, lows=None):
    c = np.array(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.array(highs, dtype=float) if highs is not None else np.maximum(o, c)
    l = np.array(lows, dtype=float) if lows is not None else np.minimum(o, c)
    return Bars(t0 + 60 * np.arange(len(c), dtype=np.int64), o, h, l, c, np.full(len(c), 0.2), True)


@pytest.mark.parametrize("n", [1, 37, 999, 4321, 7000])
def test_no_feature_changes_when_the_future_is_removed(n):
    """Le test central : couper tout ce qui suit la bougie n ne doit modifier aucune
    mesure des n premieres bougies. Sinon une mesure lit le futur."""
    bars = random_bars(9000)
    events = [int(bars.t[k]) for k in (500, 3000, 8000)]
    full = tk.compute_features(bars, events)
    part = tk.compute_features(cut(bars, n), events)
    for key in full:
        np.testing.assert_array_equal(part[key], full[key][:n], err_msg=key)


def test_features_need_utc_bars():
    bars = random_bars(50)
    bars.in_utc = False
    with pytest.raises(ValueError):
        tk.compute_features(bars)


def test_higher_bar_is_used_only_once_finished():
    bars = minute_bars(ts(2023, 3, 6, 10, 0), np.arange(1.0, 13.0))       # 10:00 a 10:11
    frame = tk.resample(bars, 5)
    assert list(frame.bars.c) == [5.0, 10.0, 12.0]
    assert list(frame.bars.h) == [5.0, 10.0, 12.0] and list(frame.bars.o) == [1.0, 5.0, 10.0]
    # La M5 de 10:00 n'est connue qu'a la cloture de la bougie M1 de 10:04.
    assert list(frame.closed_pos) == [-1, -1, -1, -1, 0, 0, 0, 0, 0, 1, 1, 1]


def test_higher_bar_with_missing_last_minute_closes_when_time_has_passed():
    bars = minute_bars(ts(2023, 3, 6, 10, 0), [1.0, 2.0, 3.0, 4.0])
    bars.t[3] = ts(2023, 3, 6, 10, 6)                                     # trou de 10:03 a 10:05
    assert list(tk.resample(bars, 5).closed_pos) == [-1, -1, -1, 0]


def test_swing_high_appears_only_after_its_confirmation():
    highs = [10, 11, 15, 12, 11, 10, 9]
    bars = minute_bars(ts(2023, 3, 6, 10, 0), [9] * 7, highs=highs, lows=[8] * 7)
    high, _ = tk.swings(bars, width=2)
    assert np.isnan(high[:4]).all()            # le sommet de la bougie 2 n'est connu qu'a la bougie 4
    assert list(high[4:]) == [15.0, 15.0, 15.0]


def test_structure_turns_up_on_a_close_above_the_last_confirmed_high():
    closes = [9, 9, 9, 9, 9, 9, 16, 16]
    highs = [10, 11, 15, 12, 11, 10, 16, 16]
    bars = minute_bars(ts(2023, 3, 6, 10, 0), closes, highs=highs, lows=[8] * 8)
    state, _, _ = tk.structure(bars, width=2)
    assert list(state) == [0, 0, 0, 0, 0, 0, 1, 1]


def test_rsi_extremes_and_warmup():
    assert np.isnan(tk.rsi(np.arange(10.0))).all()
    assert tk.rsi(np.arange(30.0))[-1] == 100.0
    assert tk.rsi(np.arange(30.0, 0.0, -1.0))[-1] == 0.0


def test_trend_follows_a_steady_rise_and_is_silent_without_history():
    n = 60 * 80
    bars = minute_bars(ts(2023, 3, 6, 0, 0), 2000 + 0.01 * np.arange(n))
    bars.h += 0.05
    bars.l -= 0.05
    sign, strength = tk.trend(tk.resample(bars, 60), 20, 50)
    assert sign[0] == 0 and np.isnan(strength[0])
    assert sign[-1] == 1 and strength[-1] > 0


def test_sessions_follow_local_clocks_through_daylight_saving():
    def name(*args):
        return tk.SESSION_NAMES[int(tk.sessions(np.array([ts(*args)]))["session"][0])]
    assert name(2024, 1, 15, 3, 0) == "asie"
    assert name(2024, 1, 15, 9, 0) == "londres"
    assert name(2024, 1, 15, 14, 0) == "londres+new_york"
    assert name(2024, 1, 15, 18, 0) == "new_york"
    assert name(2024, 7, 15, 7, 30) == "londres"          # ete : Londres ouvre a 07:00 UTC
    assert name(2024, 1, 15, 7, 30) == "asie"


def test_trading_day_rolls_at_five_pm_new_york():
    day = tk.sessions(np.array([ts(2024, 1, 15, 21, 59), ts(2024, 1, 15, 22, 0)]))["trading_day"]
    assert day[1] == day[0] + 1


def test_previous_day_levels_and_asia_range():
    # Deux journees de trading : du 15 janvier 22:00 UTC, puis du 16 janvier 22:00 UTC.
    t = np.array([ts(2024, 1, 15, 23, 0), ts(2024, 1, 16, 2, 0), ts(2024, 1, 16, 9, 0),
                  ts(2024, 1, 16, 20, 30), ts(2024, 1, 16, 23, 0), ts(2024, 1, 17, 9, 0)], dtype=np.int64)
    h = np.array([10.0, 12.0, 11.0, 30.0, 20.0, 21.0])
    l = h - 5
    bars = Bars(t, l + 1, h, l, h - 1, np.full(6, 0.2), True)
    s = tk.sessions(t)
    levels = tk.day_levels(bars, s["trading_day"], s["before_london"])
    assert np.isnan(levels["prev_day_high"][:4]).all()
    assert list(levels["prev_day_high"][4:]) == [30.0, 30.0] and list(levels["prev_day_low"][4:]) == [5.0, 5.0]
    assert list(levels["prev_day_close"][4:]) == [29.0, 29.0]
    # Asie : inconnue avant l'ouverture de Londres, puis figee. La bougie de 20:30 n'en fait pas partie.
    assert np.isnan(levels["asia_high"][[0, 1, 4]]).all()
    assert list(levels["asia_high"][[2, 3, 5]]) == [12.0, 12.0, 20.0]
    assert list(levels["asia_low"][[2, 3, 5]]) == [5.0, 5.0, 15.0]


def test_event_distance_before_and_after():
    until, since = tk.event_distance(np.array([1000 * 60, 1010 * 60, 1020 * 60]), [1010 * 60])
    assert list(until[:2]) == [10.0, 0.0] and np.isnan(until[2])
    assert np.isnan(since[0]) and list(since[1:]) == [0.0, 10.0]
    until, since = tk.event_distance(np.array([60]), None)
    assert np.isnan(until[0]) and np.isnan(since[0])


def test_spread_ratio_flags_a_wide_spread():
    bars = random_bars(600, gaps=False)
    bars.spread[:] = 0.2
    bars.spread[-1] = 1.0
    ratio = tk.compute_features(bars)["spread_ratio"]
    assert np.isnan(ratio[100]) and ratio[400] == pytest.approx(1.0) and ratio[-1] > 4.5


def test_snapshot_is_readable():
    bars = random_bars(3000)
    row = tk.snapshot(tk.compute_features(bars), 2999)
    assert row["bar_open_utc"].endswith("Z") and row["session"] in tk.SESSION_NAMES.values()
    assert row["minutes_to_event"] is None and isinstance(row["atr_m1"], float)


def test_cli_refuses_the_locked_final_test_period(capsys):
    assert tk.main(["--bars", "absent.csv", "--offset-rule", "utc_fixe", "--at", "2026-03-01T10:00"]) == 2
    assert "verrouillee" in capsys.readouterr().err
