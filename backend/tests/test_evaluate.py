from eval_metrics import match_alerts, motion_baseline_alerts, timing_errors


def test_match_alerts_tolerance():
    truth = [(10.0, 20.0), (50.0, 55.0), (90.0, 91.0)]
    alerts = [9.0, 21.5, 52.0, 70.0]
    m = match_alerts(alerts, truth, tolerance_s=2.0)
    assert (m.tp, m.fp, m.fn) == (2, 1, 1)
    assert round(m.precision, 2) == 0.67 and round(m.recall, 2) == 0.67 and round(m.f1, 2) == 0.67


def test_timing_errors_pairs_nearest():
    errs = timing_errors([("enter", "kitchen", 10.0), ("exit", "kitchen", 40.0)],
                         [("enter", "kitchen", 10.6), ("exit", "kitchen", 39.0), ("enter", "door", 5.0)])
    assert sorted(round(e, 1) for e in errs) == [0.6, 1.0]


def test_motion_baseline_counts_onsets():
    moving = [False] * 10 + [True] * 5 + [False] * 30 + [True] * 2 + [False] * 3 + [True] * 4
    assert motion_baseline_alerts(moving, fps=5, quiet_s=4) == 2
