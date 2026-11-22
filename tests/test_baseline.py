import math
import random

import pytest

from spm.baseline import SERIES, Ewma, new_baseline


def feed(b, values, start=0.0, step=2.0):
    ts = start
    for v in values:
        b.update(v, ts)
        ts += step
    return ts


def test_not_ready_during_warmup():
    b = Ewma(warmup=10)
    for i in range(9):
        assert b.observe(50, i * 2.0) is None
    b.update(50, 20)
    assert b.ready


def test_constant_series_scores_zero():
    b = Ewma(warmup=5, min_std=1.0)
    feed(b, [20.0] * 50)
    assert b.mean == pytest.approx(20.0)
    assert b.score(20.0) == pytest.approx(0.0)


def test_warmup_behaves_like_plain_mean_and_variance():
    values = [10, 12, 8, 11, 9, 10, 13, 7]
    b = Ewma(half_life_s=10 ** 9, min_std=0.0, warmup=1)
    feed(b, values)
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    assert b.mean == pytest.approx(mean)
    assert b.var == pytest.approx(var, rel=1e-6)


def test_spike_stands_out_from_noise():
    rng = random.Random(42)
    b = Ewma(warmup=30, min_std=1.0)
    ts = feed(b, [15 + rng.gauss(0, 3) for _ in range(300)])
    normal = b.score(18)
    spike = b.score(60)
    assert abs(normal) < 2
    assert spike > 8
    assert b.std == pytest.approx(3, rel=0.35)
    assert ts > 0


def test_score_is_taken_before_learning():
    b = Ewma(warmup=3, min_std=1.0)
    feed(b, [10, 10, 10, 10])
    z = b.observe(30, 100)
    assert z == pytest.approx(20.0)  # (30 - 10) / min_std
    assert b.mean > 10


def test_min_std_floor():
    b = Ewma(warmup=3, min_std=2.5)
    feed(b, [50.0] * 20)
    assert b.std == 2.5
    assert b.score(55) == pytest.approx(2.0)


def test_half_life():
    # after warm-up, a step change should be about half absorbed after one half-life
    b = Ewma(half_life_s=600, warmup=1, min_std=0)
    feed(b, [0.0] * 2000, step=2.0)           # long history at 0
    start = b.last_ts + 2
    feed(b, [100.0] * 300, start=start, step=2.0)  # 300 * 2s = 600s at 100
    assert b.mean == pytest.approx(50, abs=3)


def test_baseline_adapts_to_new_normal():
    b = Ewma(half_life_s=300, warmup=10, min_std=1.0)
    feed(b, [10.0] * 200)
    assert b.score(40) > 10
    feed(b, [40.0] * 1000, start=1000)
    assert abs(b.score(40)) < 1


def test_series_specs_read_samples():
    from conftest import make_sample

    s = make_sample(cpu=33.0, mem=44.0)
    assert SERIES["system_cpu"].value(s) == 33.0
    assert SERIES["system_memory"].value(s) == 44.0
    assert SERIES["network"].value(s) == pytest.approx((700 + 300) / 1024)
    assert SERIES["disk_io"].value(s) == pytest.approx((1000 + 2000) / 1024)
    for name in SERIES:
        assert new_baseline(name).min_std == SERIES[name].min_std
        assert math.isfinite(new_baseline(name).std)
