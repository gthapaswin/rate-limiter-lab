"""Online traffic-shape classifier.

Watches one client's request arrivals and labels the recent shape as ``steady``,
``bursty``, ``ramping``, ``moderate``, or ``warmup`` (not enough data yet). The
:class:`~ratelimiter.adaptive.AdaptiveRateLimiter` uses the label to pick which
algorithm should govern the client.

Signal -- a **peak-to-mean ratio** over a rolling *time* horizon
----------------------------------------------------------------
Arrivals in the last few windows are bucketed into fixed-width time bins; the
burstiness index is ``max_bin_count / mean_bin_count``:

  * evenly spaced arrivals      -> every bin ~equal -> ratio ~ 1        -> steady
  * bursty arrivals (tight clusters + long idle) -> one bin spikes      -> bursty
  * a rate climbing over time   -> later bins > earlier bins            -> ramping

Binning over *time* (not over a fixed count of gaps) is what makes this robust:
a dense in-burst cluster can't evict the surrounding idle period from view the
way a fixed-length gap history would, so the idle-then-spike structure of a burst
stays visible. Cost is O(arrivals within the horizon) memory per client.
"""

from __future__ import annotations

from collections import deque


class TrafficDetector:
    def __init__(
        self,
        window: float,
        horizon_windows: int = 3,
        bins_per_window: int = 10,
        min_samples: int = 20,
        steady_ratio: float = 1.8,
        bursty_ratio: float = 3.0,
        ramp_ratio: float = 1.8,
    ) -> None:
        self.window = window
        self.horizon = horizon_windows * window
        self.bin_width = window / bins_per_window
        self.min_samples = min_samples
        self.steady_ratio = steady_ratio
        self.bursty_ratio = bursty_ratio
        self.ramp_ratio = ramp_ratio
        self._ts: deque[float] = deque()
        self.samples = 0

    def observe(self, now: float) -> None:
        """Record one arrival at time ``now`` and drop anything past the horizon."""
        self._ts.append(now)
        self.samples += 1
        cutoff = now - self.horizon
        while self._ts and self._ts[0] < cutoff:
            self._ts.popleft()

    def classify(self) -> str:
        """Return the current traffic-shape label."""
        n = len(self._ts)
        if n < self.min_samples:
            return "warmup"

        now = self._ts[-1]
        start = now - self.horizon
        nbins = max(1, round(self.horizon / self.bin_width))
        counts = [0] * nbins
        for t in self._ts:
            idx = int((t - start) / self.bin_width)
            idx = 0 if idx < 0 else (nbins - 1 if idx >= nbins else idx)
            counts[idx] += 1

        mean = sum(counts) / nbins
        if mean <= 0:
            return "warmup"
        ratio = max(counts) / mean

        # Ramping: later bins consistently busier than earlier ones, without the
        # extreme single-bin spike of a burst.
        third = max(1, nbins // 3)
        early = sum(counts[:third]) / third
        late = sum(counts[-third:]) / third
        if ratio < self.bursty_ratio and early > 0 and late / early >= self.ramp_ratio:
            return "ramping"

        if ratio >= self.bursty_ratio:
            return "bursty"
        if ratio <= self.steady_ratio:
            return "steady"
        return "moderate"
