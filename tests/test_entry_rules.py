"""Regles d'entree H4 a H6 : arithmetique de la simulation et des signaux.

Bougies fabriquees a la main. Aucun de ces tests ne dit quoi que ce soit sur le
comportement reel de l'or.
"""
from datetime import datetime, timezone

import numpy as np
import pytest

from amb_research import entry_rules as er
from amb_research.impulse_study import Bars

T0 = int(datetime(2024, 1, 16, 9, 0, tzinfo=timezone.utc).timestamp())


def flat(n, price=2000.0, spread=0.2, t0=T0):
    c = np.full(n, price)
    return Bars(t0 + 60 * np.arange(n, dtype=np.int64), c.copy(), c + 0.1, c - 0.1, c.copy(), np.full(n, spread), True)


def one_signal(n, at, side):
    s = np.zeros(n, dtype=np.int64)
    s[at] = side
    return s


def sim(bars, signal, reward=1.0, minutes=30, dist=1.0, **kw):
    return er.simulate(bars, signal, np.full(len(bars), dist), reward, minutes, **kw)


def test_long_reaches_target_and_pays_the_entry_spread():
    bars = flat(20)
    bars.h[8] = 2001.5
    tr = sim(bars, one_signal(20, 4, 1))
    assert list(tr["gross"]) == [1.0] and list(tr["cost"]) == [0.2] and tr["net"][0] == pytest.approx(0.8)
    assert tr["reason"][0] == "objectif" and tr["entry_t"][0] == bars.t[5]


def test_short_pays_the_spread_of_the_exit_bar():
    bars = flat(20)
    bars.l[8] = 1998.0
    bars.spread[8] = 0.5
    tr = sim(bars, one_signal(20, 4, -1))
    assert tr["gross"][0] == 1.0 and tr["cost"][0] == 0.5


def test_stop_wins_when_stop_and_target_are_hit_in_the_same_bar():
    bars = flat(20)
    bars.h[8], bars.l[8] = 2002.0, 1998.0
    tr = sim(bars, one_signal(20, 4, 1))
    assert tr["reason"][0] == "stop" and tr["gross"][0] == -1.0


def test_gap_through_the_stop_exits_at_the_worse_open():
    bars = flat(20)
    bars.o[8:], bars.c[8:], bars.h[8:], bars.l[8:] = 1997.0, 1997.0, 1997.1, 1996.9
    tr = sim(bars, one_signal(20, 4, 1))
    assert tr["reason"][0] == "stop" and tr["gross"][0] == pytest.approx(-3.0)


def test_time_limit_exits_at_the_close():
    bars = flat(60)
    bars.c[5:] = 2000.05
    tr = sim(bars, one_signal(60, 4, 1), minutes=10)
    assert tr["reason"][0] == "duree" and tr["gross"][0] == pytest.approx(0.05)


def test_only_one_position_at_a_time_and_open_trade_at_the_end_is_dropped():
    bars = flat(40)
    signal = np.zeros(40, dtype=np.int64)
    signal[[4, 6, 20]] = 1                      # le signal de la bougie 6 arrive pendant le premier trade
    tr = sim(bars, signal, minutes=10)
    assert [int(t) for t in tr["entry_t"]] == [int(bars.t[5]), int(bars.t[21])]
    assert len(sim(bars, one_signal(40, 35, 1), minutes=30)["net"]) == 0


def test_no_entry_after_a_market_closure_and_outside_allowed_bars():
    bars = flat(20)
    bars.t[5:] += 3600                          # le marche ferme juste apres le signal
    assert len(sim(bars, one_signal(20, 4, 1))["net"]) == 0
    assert len(sim(flat(20), one_signal(20, 4, 1), allowed=np.zeros(20, dtype=bool))["net"]) == 0


def features(n, **over):
    f = {"spread_ratio": np.ones(n), "minutes_to_event": np.full(n, np.nan), "minutes_since_event": np.full(n, np.nan),
         "atr_m15": np.ones(n), "trend_h1": np.zeros(n), "trend_h4": np.zeros(n), "rsi_m5": np.full(n, 50.0),
         "move_15": np.zeros(n), "asia_high": np.full(n, np.nan), "asia_low": np.full(n, np.nan)}
    f.update(over)
    return f


def test_common_filter_blocks_wide_spread_and_news():
    f = features(4, spread_ratio=np.array([1.0, 2.5, 1.0, 1.0]), minutes_to_event=np.array([90.0, 90.0, 20.0, 90.0]),
                 minutes_since_event=np.array([90.0, 90.0, 90.0, 3.0]))
    assert list(er.common_filter(f)) == [True, False, False, False]


def test_pullback_needs_trend_recent_low_rsi_and_a_close_above_the_previous_high():
    bars = flat(40)
    bars.c[35] = 2000.5
    bars.h[35] = 2000.6
    rsi = np.full(40, 50.0)
    rsi[20] = 25.0
    f = features(40, trend_h1=np.ones(40), trend_h4=np.ones(40), rsi_m5=rsi)
    assert list(np.flatnonzero(er.signal_pullback(bars, f, 30))) == [35]
    rsi[20], rsi[2] = 50.0, 25.0                # le creux du RSI date de plus de 30 bougies
    assert not er.signal_pullback(bars, f, 30).any()
    f = features(40, trend_h1=np.ones(40), trend_h4=-np.ones(40), rsi_m5=np.full(40, 25.0))
    assert not er.signal_pullback(bars, f, 30).any()


def test_asia_breakout_keeps_only_the_first_close_beyond_each_side():
    bars = flat(60)                             # 09:00 a 09:59 UTC, 16 janvier 2024 : fenetre de Londres
    bars.c[10:] = 2003.0
    f = features(60, asia_high=np.full(60, 2002.0), asia_low=np.full(60, 1998.0))
    assert list(np.flatnonzero(er.signal_asia_breakout(bars, f, False))) == [10]
    assert not er.signal_asia_breakout(bars, f, True).any()          # tendance H1 nulle : refuse
    f["trend_h1"] = np.ones(60)
    assert er.signal_asia_breakout(bars, f, True)[10] == 1
    late = flat(60, t0=T0 + 4 * 3600)           # 13:00 UTC : hors fenetre
    late.c[10:] = 2003.0
    assert not er.signal_asia_breakout(late, f, False).any()


def test_fade_sells_an_extreme_rise_and_buys_an_extreme_fall():
    f = features(3, move_15=np.array([5.0, -5.0, 5.0]), rsi_m5=np.array([85.0, 15.0, 60.0]))
    assert list(er.signal_fade(flat(3), f, 4.0)) == [-1, 1, 0]


def test_stats_and_pass_criterion():
    s = er.stats(np.array([1.0, -1.0, -1.0, 2.0]))
    assert s["n"] == 4 and s["win_rate"] == 0.5 and s["profit_factor"] == 1.5 and s["worst_losing_streak"] == 2
    good = {"n": 60, "net_mean": 0.1}
    assert er.passes({"all": {"n": 120, "net_mean": 0.1, "net_mean_ci95": [0.01, 0.2]}, "first_half": good, "second_half": good})
    assert not er.passes({"all": {"n": 120, "net_mean": 0.1, "net_mean_ci95": [-0.01, 0.2]}, "first_half": good, "second_half": good})
    assert not er.passes({"all": {"n": 90, "net_mean": 0.1, "net_mean_ci95": [0.01, 0.2]}, "first_half": good, "second_half": good})


def test_twelve_configurations_and_final_test_is_locked(capsys):
    assert len(er.CONFIGS) == 12
    assert er.main(["--bars", "x.csv", "--store", "x.sqlite", "--offset-rule", "utc_fixe", "--period", "final_test"]) == 2
    assert "verrouillee" in capsys.readouterr().err


def test_run_ignores_bars_after_the_period():
    rng = np.random.default_rng(3)
    n = 6000
    t = int(datetime(2024, 12, 30, 0, 0, tzinfo=timezone.utc).timestamp()) + 60 * np.arange(n, dtype=np.int64)
    c = 2000 + np.cumsum(rng.normal(0, 0.4, n))
    o = np.concatenate([[2000.0], c[:-1]])
    bars = Bars(t, o, np.maximum(o, c) + 0.1, np.minimum(o, c) - 0.1, c, np.full(n, 0.2), True)
    full = er.run(bars, "development")
    keep = t < int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
    cut = er.run(Bars(t[keep], o[keep], bars.h[keep], bars.l[keep], c[keep], bars.spread[keep], True), "development")
    assert full == cut and full["last_bar_utc"].startswith("2024-12-31")
