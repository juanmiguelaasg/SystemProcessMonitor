"""Rolling baselines for anomaly detection (EWMA mean and variance + z-score).

Every series keeps an exponentially weighted moving average and variance. A new
value is scored against the baseline *before* it is folded in:

    z = (value - mean) / max(std, min_std)

Old samples fade out with a half-life (30 minutes by default), so the baseline
follows slow changes such as a machine that is busier in the afternoon, while a
sudden jump still stands out. `min_std` keeps very flat series (for example
memory sitting at 91 %) from turning every tiny wobble into a huge z-score.

During warm-up the weight is at least 1/n, which makes the first samples behave
like a plain running mean/variance instead of trusting the very first value.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

DEFAULT_HALF_LIFE_S = 30 * 60
DEFAULT_WARMUP = 60


@dataclass
class Ewma:
    half_life_s: float = DEFAULT_HALF_LIFE_S
    min_std: float = 1.0
    warmup: int = DEFAULT_WARMUP
    mean: float | None = None
    var: float = 0.0
    samples: int = 0
    last_ts: float | None = None

    @property
    def ready(self) -> bool:
        return self.samples >= self.warmup and self.mean is not None

    @property
    def std(self) -> float:
        return max(math.sqrt(max(self.var, 0.0)), self.min_std)

    def score(self, value: float) -> float | None:
        if not self.ready:
            return None
        return (value - self.mean) / self.std

    def update(self, value: float, ts: float) -> None:
        if self.mean is None:
            self.mean, self.var, self.samples, self.last_ts = float(value), 0.0, 1, ts
            return
        dt = max(ts - (self.last_ts if self.last_ts is not None else ts), 1e-3)
        alpha = 1.0 - 0.5 ** (dt / self.half_life_s)
        alpha = max(alpha, 1.0 / (self.samples + 1))
        diff = value - self.mean
        incr = alpha * diff
        self.mean += incr
        self.var = (1.0 - alpha) * (self.var + diff * incr)
        self.samples += 1
        self.last_ts = ts

    def observe(self, value: float, ts: float) -> float | None:
        """Score the value against the current baseline, then learn from it."""
        z = self.score(value)
        self.update(value, ts)
        return z


@dataclass(frozen=True)
class SeriesSpec:
    label: str
    unit: str
    min_std: float
    value: Callable


KB = 1024.0

# series name -> how to read it from a Sample and how flat it is allowed to be
SERIES: dict[str, SeriesSpec] = {
    "system_cpu": SeriesSpec("CPU", "%", 2.0, lambda s: s.cpu),
    "system_memory": SeriesSpec("Memory", "%", 1.0, lambda s: s.mem_percent),
    "process_count": SeriesSpec("Process count", "", 3.0, lambda s: s.process_count),
    "network": SeriesSpec("Network traffic", " KB/s", 64.0,
                          lambda s: (s.net_recv_rate + s.net_sent_rate) / KB),
    "disk_io": SeriesSpec("Disk I/O", " KB/s", 256.0,
                          lambda s: (s.disk_read_rate + s.disk_write_rate) / KB),
}


def new_baseline(series: str) -> Ewma:
    return Ewma(min_std=SERIES[series].min_std)
