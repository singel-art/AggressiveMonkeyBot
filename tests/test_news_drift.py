"""Regle H7 (derive apres annonce) : arithmetique du signal. Bougies fabriquees a la main."""
from datetime import datetime, timezone

import numpy as np
import pytest

from amb_research import news_drift as nd
from amb_research.entry_rules import simulate
from amb_research.impulse_study import Bars

T0 = int(datetime(2024, 1, 5, 13, 0, tzinfo=timezone.utc).timestamp())


def flat(n=60):
    c = np.full(n, 2000.0)
    return Bars(T0 + 60 * np.arange(n, dtype=np.int64), c.copy(), c + 0.5, c - 0.5, c.copy(), np.full(n, 0.2), True)


def with_reaction(i, body):
    bars = flat()
    bars.c[i:] += body
    bars.o[i + 1:] += body
    bars.h[i:] = np.maximum(bars.o[i:], bars.c[i:]) + 0.5
    bars.l[i:] = np.minimum(bars.o[i:], bars.c[i:]) - 0.5
    return bars


def test_signal_follows_the_reaction_bar_with_a_stop_at_its_far_end():
    bars = with_reaction(30, 3.0)                       # ATR M1 precedent : 1.0
    signal, dist = nd.reaction_signal(bars, [int(bars.t[30])], 2.0)
    assert list(np.flatnonzero(signal)) == [30] and signal[30] == 1
    assert dist[30] == pytest.approx(3.5)               # ouverture d'entree 2003.0, bas de la reaction 1999.5
    down, dist = nd.reaction_signal(with_reaction(30, -3.0), [int(bars.t[30])], 2.0)
    assert down[30] == -1 and dist[30] == pytest.approx(3.5)


def test_no_signal_for_a_small_reaction_or_a_missing_announcement_bar():
    bars = with_reaction(30, 1.5)
    assert not nd.reaction_signal(bars, [int(bars.t[30])], 2.0)[0].any()
    assert nd.reaction_signal(bars, [int(bars.t[30])], 1.0)[0][30] == 1
    assert not nd.reaction_signal(bars, [int(bars.t[30]) + 30], 1.0)[0].any()      # pas de bougie a cette minute
    assert not nd.reaction_signal(with_reaction(30, 3.0), [int(bars.t[10])], 1.0)[0].any()   # bougie plate


def test_trade_exits_on_time_without_target_and_on_full_retracement():
    bars = with_reaction(30, 3.0)
    signal, dist = nd.reaction_signal(bars, [int(bars.t[30])], 2.0)
    tr = simulate(bars, signal, dist, np.inf, 5)
    assert tr["reason"][0] == "duree" and tr["gross"][0] == 0.0 and tr["net"][0] == pytest.approx(-0.2)
    bars.l[33] = 1999.0
    tr = simulate(bars, signal, dist, np.inf, 5)
    assert tr["reason"][0] == "stop" and tr["gross"][0] == pytest.approx(-3.5)


def test_six_configurations_and_locked_final_test(capsys):
    assert len(nd.CONFIGS) == 6
    assert nd.main(["--bars", "x.csv", "--store", "x", "--offset-rule", "utc_fixe", "--period", "final_test"]) == 2
