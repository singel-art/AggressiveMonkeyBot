"""Calculs de l'etude des impulsions M1.

Aucune donnee de marche ici : les bougies sont construites a la main pour verifier
l'arithmetique (detection, cout, absence de fuite du futur). Aucun de ces tests ne
dit quoi que ce soit sur le comportement reel de l'or.
"""
import json
from datetime import datetime, timezone

import numpy as np
import pytest

from amb_research import impulse_study as st

T0 = int(datetime(2023, 3, 1, 10, 0, tzinfo=timezone.utc).timestamp())


def flat_bars(n, price=2000.0, spread=0.3, t0=T0, in_utc=True):
    t = t0 + 60 * np.arange(n, dtype=np.int64)
    c = np.full(n, price)
    return st.Bars(t, c.copy(), c + 0.5, c - 0.5, c.copy(), np.full(n, spread), in_utc)


def with_up_impulse(bars, i, body=3.0):
    """Bougie i : ouvre au niveau courant et cloture `body` plus haut ; les suivantes restent a ce niveau."""
    bars.c[i:] += body
    bars.o[i + 1:] += body
    bars.h[i:] = np.maximum(bars.o[i:], bars.c[i:]) + 0.5
    bars.l[i + 1:] = np.minimum(bars.o[i + 1:], bars.c[i + 1:]) - 0.5
    bars.l[i] = bars.o[i] - 0.5
    return bars


def test_atr_is_a_trailing_average_with_no_value_before_enough_history():
    bars = flat_bars(30)
    values = st.atr(bars, 14)
    assert np.isnan(values[:13]).all() and values[13] == pytest.approx(1.0)


def test_impulse_is_detected_at_its_close_with_the_previous_bar_atr():
    bars = with_up_impulse(flat_bars(80), 40)
    idx, direction = st.find_impulses(bars, lookback=10, k=1.0, atr_values=st.atr(bars))
    assert list(idx) == [40] and list(direction) == [1]
    # Corps trop petit pour k=5 : aucun signal.
    assert len(st.find_impulses(bars, 10, 5.0, st.atr(bars))[0]) == 0


def test_down_impulse_is_symmetric():
    bars = flat_bars(80)
    bars.c[40:] -= 3.0
    bars.o[41:] -= 3.0
    bars.l[40:] = np.minimum(bars.o[40:], bars.c[40:]) - 0.5
    bars.h[41:] = np.maximum(bars.o[41:], bars.c[41:]) + 0.5
    idx, direction = st.find_impulses(bars, 10, 1.0, st.atr(bars))
    assert list(idx) == [40] and list(direction) == [-1]


def test_detection_does_not_depend_on_future_bars():
    """Fuite du futur : modifier les bougies posterieures au signal ne change pas la detection."""
    bars = with_up_impulse(flat_bars(120), 40)
    before = st.find_impulses(bars, 10, 1.0, st.atr(bars))
    changed = with_up_impulse(flat_bars(120), 40)
    for arr in (changed.o, changed.h, changed.l, changed.c):
        arr[41:] += 50.0
    after = st.find_impulses(changed, 10, 1.0, st.atr(changed))
    assert 40 in before[0] and 40 in after[0]
    assert list(before[0][before[0] <= 40]) == list(after[0][after[0] <= 40])


def test_long_pays_the_entry_spread_and_short_pays_the_exit_spread():
    bars = flat_bars(20, spread=0.3)
    bars.spread[6] = 0.2          # bougie d'entree
    bars.spread[10] = 0.7         # bougie de sortie a horizon 5
    bars.c[10] = 2004.0
    long_ = st.forward_outcomes(bars, np.array([5]), np.array([1]), 5)
    assert long_["gross"][0] == pytest.approx(4.0) and long_["cost"][0] == pytest.approx(0.2)
    assert long_["net"][0] == pytest.approx(3.8)
    short = st.forward_outcomes(bars, np.array([5]), np.array([-1]), 5)
    assert short["gross"][0] == pytest.approx(-4.0) and short["cost"][0] == pytest.approx(0.7)


def test_entry_is_the_next_bar_open_not_the_signal_close():
    bars = flat_bars(20)
    bars.o[6] = 2001.0            # ouverture de la bougie suivante, differente de la cloture du signal
    bars.c[6] = 2002.0
    res = st.forward_outcomes(bars, np.array([5]), np.array([1]), 1)
    assert res["gross"][0] == pytest.approx(1.0)


def test_signals_crossing_a_quote_gap_are_dropped():
    bars = flat_bars(40)
    bars.t[20:] += 2 * 24 * 3600                       # trou de cotation (week-end)
    res = st.forward_outcomes(bars, np.array([5, 17]), np.array([1, 1]), 5)
    assert list(res["entry_index"]) == [6]


def test_decluster_keeps_one_position_at_a_time():
    idx, direction = st.decluster(np.array([10, 15, 69, 70, 200]), np.array([1, -1, 1, 1, -1]), 60)
    assert list(idx) == [10, 70, 200] and list(direction) == [1, 1, -1]


def test_summary_statistics_and_fixed_seed_confidence_interval():
    gross = np.array([1.0, -0.5, 2.0] * 20)
    cost = np.full(60, 0.25)
    s1 = st.summarize(gross, cost, np.random.default_rng(st.SEED))
    s2 = st.summarize(gross, cost, np.random.default_rng(st.SEED))
    assert s1["n"] == 60 and s1["gross_mean"] == pytest.approx(5 / 6) and s1["net_mean"] == pytest.approx(5 / 6 - 0.25)
    assert s1["gross_to_cost"] == pytest.approx((5 / 6) / 0.25) and s1["hit_rate_net"] == pytest.approx(2 / 3)
    assert s1["net_mean_ci95"] == s2["net_mean_ci95"] and s1["net_mean_ci95"][0] < s1["net_mean"] < s1["net_mean_ci95"][1]
    assert st.summarize(np.array([]), np.array([])) == {"n": 0}


def test_higher_timeframe_trend_uses_only_closed_bars():
    n = 60 * 300
    bars = flat_bars(n)
    bars.c[:] = 2000.0 + np.arange(n) * 0.01            # hausse reguliere
    trend = st.trend_at(bars, 60, 20, 50)
    assert trend[-1] == 1.0 and trend[0] == 0.0          # pas d'avis sans historique
    # Changer la derniere heure, encore en cours, ne change pas l'avis rendu pendant cette heure.
    altered = flat_bars(n)
    altered.c[:] = bars.c
    altered.c[-30:] -= 500.0
    assert st.trend_at(altered, 60, 20, 50)[-1] == trend[-1]


def test_server_offset_is_recovered_from_announcement_spikes():
    """Serveur a UTC+2 l'hiver et UTC+3 l'ete (regle americaine) : la bougie la plus ample
    du jour est placee a l'heure serveur de la publication."""
    events, t, rng = [], [], []
    for day, hour_utc, offset in ((datetime(2024, 1, 5), 13, 2), (datetime(2024, 2, 2), 13, 2),
                                  (datetime(2024, 6, 7), 12, 3), (datetime(2024, 7, 5), 12, 3)):
        ev = int(day.replace(hour=hour_utc, minute=30, tzinfo=timezone.utc).timestamp())
        events.append(ev)
        start = ev + offset * 3600 - 300 * 60
        for m in range(600):
            t.append(start + 60 * m)
            rng.append(20.0 if m == 300 else 1.0)
    t = np.array(t, dtype=np.int64)
    rng = np.array(rng)
    bars = st.Bars(t, np.full(len(t), 2000.0), 2000.0 + rng / 2, 2000.0 - rng / 2, np.full(len(t), 2000.0),
                   np.full(len(t), 0.3))
    observations = st.observed_offsets(bars, events)
    assert [o for _, o in observations] == [2.0, 2.0, 3.0, 3.0]
    choice = st.choose_offset_rule(observations)
    assert choice["rule"] == "utc+2/+3_heure_ete_US" and choice["agreement"][choice["rule"]] == 1.0
    assert choice["agreement"]["utc+2_fixe"] == 0.5
    converted = st.to_utc(bars, choice["rule"])
    spikes = converted.t[(converted.h - converted.l) > 10]
    assert list(spikes) == events                        # chaque pic retombe sur l'heure UTC reelle


def test_run_study_requires_utc_and_respects_the_period():
    bars = with_up_impulse(flat_bars(400), 100)
    with pytest.raises(ValueError, match="UTC"):
        st.run_study(flat_bars(400, in_utc=False), "development")
    report = st.run_study(bars, "development")
    assert report["bars_in_period"] == 400 and report["configs_tested"] == 36
    assert report["configs_passing"] == 0                # un seul signal : loin des 100 requis
    first = report["results"][0]
    assert first["signals"] == 1 and first["horizons"]["5"]["all"]["n"] == 1
    assert st.run_study(bars, "validation")["bars_in_period"] == 0


def _write_export(tmp_path, bars):
    path = tmp_path / "amb_bars_XAUUSD_M1.csv"
    lines = ["time_server;open;high;low;close;tick_volume;spread_points;real_volume"]
    for i in range(len(bars)):
        stamp = datetime.fromtimestamp(int(bars.t[i]), timezone.utc).strftime("%Y.%m.%d %H:%M:%S")
        lines.append(f"{stamp};{bars.o[i]:.2f};{bars.h[i]:.2f};{bars.l[i]:.2f};{bars.c[i]:.2f};10;30;0")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (tmp_path / "amb_bars_XAUUSD_M1.meta.csv").write_text("key;value\nsymbol;XAUUSD\npoint;0.01\n", encoding="utf-8")
    return path


def test_export_file_is_read_and_checked(tmp_path):
    path = _write_export(tmp_path, flat_bars(50))
    bars = st.load_bars(path, 0.01)
    assert len(bars) == 50 and bars.spread[0] == pytest.approx(0.30) and not bars.in_utc
    bad = path.read_text().splitlines()
    bad[5], bad[6] = bad[6], bad[5]                      # dates dans le desordre
    path.write_text("\n".join(bad) + "\n")
    with pytest.raises(ValueError, match="croissantes"):
        st.load_bars(path, 0.01)


def test_final_test_is_locked_and_can_only_run_once(tmp_path, capsys):
    path = _write_export(tmp_path, flat_bars(200, t0=int(datetime(2026, 3, 2, 10, tzinfo=timezone.utc).timestamp())))
    out = tmp_path / "out"
    args = ["--bars", str(path), "--period", "final_test", "--offset-rule", "utc_fixe", "--out", str(out)]
    assert st.main(args) == 2 and "verrouillee" in capsys.readouterr().err
    assert st.main(args + ["--unlock-final-test"]) == 0
    registry = [json.loads(line) for line in (out / "registry.jsonl").read_text().splitlines()]
    assert registry[0]["period"] == "final_test" and registry[0]["configs_tested"] == 36
    assert st.main(args + ["--unlock-final-test"]) == 2 and "deja ete execute" in capsys.readouterr().err


def test_study_refuses_to_guess_the_server_time(tmp_path, capsys):
    path = _write_export(tmp_path, flat_bars(200))
    assert st.main(["--bars", str(path), "--out", str(tmp_path / "out")]) == 2
    assert "ecart horaire" in capsys.readouterr().err


def test_protocol_document_matches_the_code():
    from conftest import ROOT
    text = (ROOT / "docs" / "research" / "H1_H3_PROTOCOL.md").read_text(encoding="utf-8")
    assert "36 configurations" in text and len(st.GRID) * len(st.HORIZONS) == 36
    assert st.PERIODS["development"] == ("2023-01-01", "2024-12-31") and "2023 et 2024" in text
    assert st.PASS_RATIO == 2.0 and "au moins 2 fois son coût moyen" in text


def test_bar_export_script_static_checks():
    """Verification statique seulement : le script n'a pas ete compile."""
    import re

    from conftest import ROOT
    code = (ROOT / "mt5" / "Scripts" / "AMB_ExportBars.mq5").read_text(encoding="utf-8")
    assert '"time_server;open;high;low;close;tick_volume;spread_points;real_volume\\n"' in code
    stripped = re.sub(r"'(?:\\.|[^'\\])*'", "''", re.sub(r'"(?:\\.|[^"\\])*"', '""', re.sub(r"//[^\n]*", "", code)))
    for opening, closing in ("{}", "()", "[]"):
        assert stripped.count(opening) == stripped.count(closing)
    assert "OrderSend" not in code and "CTrade" not in code          # lecture seule
