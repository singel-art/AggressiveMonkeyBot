"""Regles H8 a H10 : arithmetique des signaux sur bougies fabriquees a la main."""
from datetime import datetime, timezone

import numpy as np

from amb_research import htf_rules as hr
from amb_research import technical as tk
from amb_research.impulse_study import Bars

T0 = int(datetime(2024, 1, 16, 0, 0, tzinfo=timezone.utc).timestamp())


def hourly(closes, highs=None, lows=None):
    c = np.array(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.array(highs, dtype=float) if highs is not None else np.maximum(o, c)
    l = np.array(lows, dtype=float) if lows is not None else np.minimum(o, c)
    return Bars(T0 + 3600 * np.arange(len(c), dtype=np.int64), o, h, l, c, np.full(len(c), 0.2), True)


def test_channel_breakout_compares_with_previous_bars_only():
    bars = hourly([10, 10, 10, 10, 12, 12, 7])
    assert list(hr.channel_breakout(bars, 3)) == [0, 0, 0, 0, 1, 0, -1]


def test_mean_reversion_buys_far_below_the_average_and_sells_far_above():
    closes = [100.0, 101.0] * 10 + [90.0]
    assert hr.mean_reversion(hourly(closes), 2.0)[-1] == 1
    assert hr.mean_reversion(hourly([100.0, 101.0] * 10 + [111.0]), 2.0)[-1] == -1
    assert not hr.mean_reversion(hourly([100.0, 101.0] * 10), 2.0).any()


def test_pullback_needs_a_recent_low_rsi_and_a_close_above_the_previous_high():
    closes = list(np.linspace(120, 100, 21)) + [100.2, 101.5]
    sig = hr.pullback_m15(hourly(closes), 30)
    assert sig[-1] == 1 and not sig[:-1].any()
    assert not (hr.pullback_m15(hourly(list(np.linspace(100, 120, 21)) + [121.0, 122.5]), 30) == 1).any()


def test_signal_lands_on_the_m1_bar_that_closes_the_higher_bar():
    n = 180
    c = np.full(n, 2000.0)
    m1 = Bars(T0 + 60 * np.arange(n, dtype=np.int64), c.copy(), c + 0.1, c - 0.1, c.copy(), np.full(n, 0.2), True)
    frame = tk.resample(m1, 60)
    out = hr.on_close(frame, np.array([1, -1, 1]))
    assert list(np.flatnonzero(out)) == [59, 119, 179] and list(out[[59, 119, 179]]) == [1, -1, 1]


def test_pullback_is_kept_only_with_h1_and_h4_trend():
    n = 60
    c = np.full(n, 2000.0)
    m1 = Bars(T0 + 60 * np.arange(n, dtype=np.int64), c.copy(), c + 0.1, c - 0.1, c.copy(), np.full(n, 0.2), True)
    m15, h1 = tk.resample(m1, 15), tk.resample(m1, 60)
    f = {"spread_ratio": np.ones(n), "minutes_to_event": np.full(n, np.nan), "minutes_since_event": np.full(n, np.nan),
         "atr_m15": np.ones(n), "trend_h1": np.ones(n), "trend_h4": np.ones(n)}
    original = hr.pullback_m15
    hr.pullback_m15 = lambda bars, rsi: np.array([1, -1, 1, 1])
    try:
        sig = hr.build_signal("H9", {"rsi": 30}, f, m15, h1)
        assert list(np.flatnonzero(sig)) == [14, 44, 59]            # la vente de 29 est contre la tendance
        f["trend_h4"] = -np.ones(n)
        assert not hr.build_signal("H9", {"rsi": 30}, f, m15, h1).any()
    finally:
        hr.pullback_m15 = original


def test_twelve_configurations_and_locked_final_test(capsys):
    assert len(hr.CONFIGS) == 12
    assert hr.main(["--bars", "x.csv", "--store", "x", "--offset-rule", "utc_fixe", "--period", "final_test"]) == 2


def test_run_ignores_bars_after_the_period():
    rng = np.random.default_rng(5)
    n = 9000
    t = int(datetime(2024, 12, 27, 0, 0, tzinfo=timezone.utc).timestamp()) + 60 * np.arange(n, dtype=np.int64)
    c = 2000 + np.cumsum(rng.normal(0, 0.4, n))
    o = np.concatenate([[2000.0], c[:-1]])
    bars = Bars(t, o, np.maximum(o, c) + 0.1, np.minimum(o, c) - 0.1, c, np.full(n, 0.2), True)
    keep = t < int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
    cut = Bars(t[keep], o[keep], bars.h[keep], bars.l[keep], c[keep], bars.spread[keep], True)
    assert hr.run(bars, "development") == hr.run(cut, "development")
