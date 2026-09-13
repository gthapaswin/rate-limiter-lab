"""Run every algorithm/backend combination through identical traffic and emit a
results table (CSV) plus comparison plots (PNG) into ``benchmark/results/``.

Measures, per Section 6 of the spec:
  * accuracy        -- admitted vs the ideal sustainable count
  * burst tolerance -- cumulative admits over time in the bursty scenario
  * memory footprint-- bytes stored per client, swept across limit sizes
  * latency overhead-- wall-clock cost of one allow_request, memory vs Redis

Run:
    python benchmark/run_benchmarks.py
Redis measurements are included automatically if a Redis is reachable at
REDIS_URL (else skipped with a note; the memory results still stand).
"""

from __future__ import annotations

import csv
import os
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: write PNGs, never open a window
import matplotlib.pyplot as plt  # noqa: E402

# Make the repo root importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ratelimiter import ALGORITHMS, MemoryBackend  # noqa: E402
from benchmark.load_generator import SHAPES  # noqa: E402

# ------------------------------------------------------------------ configuration
LIMIT = 100
WINDOW = 1.0            # 1-second windows keep the run fast; all times are virtual
DURATION = 10.0        # seconds of simulated traffic per shape
FOOTPRINT_LIMITS = [10, 100, 1000, 5000]
REDIS_URL = os.getenv("RL_REDIS_URL", "redis://localhost:6379/0")

RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)


# ------------------------------------------------------------------ backend setup
def make_backends() -> dict:
    """Return available backends by name. Redis included only if reachable."""
    backends = {"memory": MemoryBackend()}
    try:
        import redis as _redis

        from ratelimiter.backends.redis_backend import RedisBackend

        client = _redis.Redis.from_url(REDIS_URL)
        client.ping()
        backends["redis"] = RedisBackend(REDIS_URL)
        print(f"[ok] Redis reachable at {REDIS_URL} -- including Redis backend")
    except Exception as exc:  # noqa: BLE001
        print(f"[skip] Redis not reachable ({exc}); running memory backend only")
    return backends


# ------------------------------------------------------------------ measurements
def run_scenario(algo_cls, backend, backend_name, shape_name, timestamps):
    """Replay one traffic shape through one limiter; return metrics + admit curve."""
    backend.reset()
    limiter = algo_cls(backend, limit=LIMIT, window=WINDOW, namespace=f"bench:{shape_name}")
    cumulative = []
    admitted_times = []
    admitted = 0
    for t in timestamps:
        if limiter.allow_request("bench-client", now=t):
            admitted += 1
            admitted_times.append(t)
        cumulative.append((t, admitted))

    offered = len(timestamps)
    span = (timestamps[-1] - timestamps[0]) if timestamps else 0.0
    ideal = LIMIT * (span / WINDOW) if WINDOW else 0.0
    metrics = {
        "algorithm": algo_cls.name,
        "backend": backend_name,
        "shape": shape_name,
        "offered": offered,
        "admitted": admitted,
        "admit_rate": round(admitted / offered, 3) if offered else 0.0,
        "ideal_admits": round(ideal, 1),
        "over_admit": round(admitted - ideal, 1),
        # Worst-case instantaneous rate: the most admits inside any trailing
        # window of length WINDOW. A correct limiter keeps this <= LIMIT; Fixed
        # Window overshoots it on boundary-straddling bursts.
        "peak_per_window": _peak_in_window(admitted_times, WINDOW),
    }
    return metrics, cumulative


def _peak_in_window(times: list[float], window: float) -> int:
    """Max number of admitted requests falling within any trailing ``window``."""
    if not times:
        return 0
    peak = 0
    left = 0
    for right in range(len(times)):
        while times[right] - times[left] >= window:
            left += 1
        peak = max(peak, right - left + 1)
    return peak


def deep_sizeof(obj) -> int:
    """Recursive size for the memory backend's per-client entry (handles the
    Sliding Window Log's growing list of timestamps)."""
    if isinstance(obj, (list, tuple)):
        return sys.getsizeof(obj) + sum(deep_sizeof(x) for x in obj)
    if isinstance(obj, dict):
        return sys.getsizeof(obj) + sum(
            deep_sizeof(k) + deep_sizeof(v) for k, v in obj.items()
        )
    return sys.getsizeof(obj)


def measure_footprint(backends) -> list[dict]:
    """Per-client bytes for each algorithm, swept across limit sizes.

    Fills one client up to its limit within a single window (so the log actually
    holds ``limit`` timestamps) and measures the stored state.
    """
    rows = []
    for algo_name, algo_cls in ALGORITHMS.items():
        for limit in FOOTPRINT_LIMITS:
            row = {"algorithm": algo_name, "limit": limit}

            # -- memory backend: recursive sizeof of the stored entry
            mem = MemoryBackend()
            lim = algo_cls(mem, limit=limit, window=WINDOW, namespace="fp")
            for i in range(limit):
                lim.allow_request("c", now=i * (WINDOW / (limit + 1)))
            entry = mem.store.get(f"fp:{algo_name}:c")
            row["memory_bytes"] = deep_sizeof(entry) if entry is not None else 0

            # -- redis backend: Redis's own accounting for the key
            if "redis" in backends:
                rb = backends["redis"]
                rb.reset()
                limr = algo_cls(rb, limit=limit, window=WINDOW, namespace="fp")
                for i in range(limit):
                    limr.allow_request("c", now=i * (WINDOW / (limit + 1)))
                row["redis_bytes"] = rb.key_bytes(f"fp:{algo_name}:c")
            rows.append(row)
    return rows


def measure_latency(backends) -> list[dict]:
    """Median-ish per-call cost of allow_request (microseconds), memory vs Redis."""
    rows = []
    counts = {"memory": 5000, "redis": 2000}
    for algo_name, algo_cls in ALGORITHMS.items():
        row = {"algorithm": algo_name}
        for bname, backend in backends.items():
            backend.reset()
            lim = algo_cls(backend, limit=10, window=WINDOW, namespace="lat")
            n = counts[bname]
            lim.allow_request("c")  # warm up (script load / first alloc)
            start = time.perf_counter()
            for _ in range(n):
                lim.allow_request("c")
            elapsed = time.perf_counter() - start
            row[f"{bname}_us_per_call"] = round(elapsed / n * 1e6, 2)
        rows.append(row)
    return rows


# ------------------------------------------------------------------ CSV output
def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    print(f"[write] {path.relative_to(RESULTS_DIR.parent)}")


# ------------------------------------------------------------------ plots
def plot_burst_tolerance(curves: dict) -> None:
    """Headline chart: cumulative admits over time in the bursty scenario."""
    fig, ax = plt.subplots(figsize=(9, 5.5))
    for algo_name, cum in curves.items():
        xs = [t for t, _ in cum]
        ys = [c for _, c in cum]
        ax.step(xs, ys, where="post", label=algo_name, linewidth=1.8)
    # Ideal sustainable line: LIMIT admits per WINDOW.
    ax.plot(
        [0, DURATION],
        [0, LIMIT * DURATION / WINDOW],
        "k--",
        alpha=0.5,
        label=f"ideal ({LIMIT}/window)",
    )
    ax.set_title("Burst tolerance — cumulative admitted requests (bursty traffic, memory backend)")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("cumulative admitted")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "burst_tolerance.png", dpi=120)
    plt.close(fig)
    print("[plot] burst_tolerance.png")


def plot_accuracy(scenario_rows: list[dict]) -> None:
    """Admitted vs ideal for each algorithm, one group of bars per traffic shape
    (memory backend)."""
    shapes = list(SHAPES)
    algos = list(ALGORITHMS)
    fig, axes = plt.subplots(1, len(shapes), figsize=(13, 4.5), sharey=True)
    for ax, shape in zip(axes, shapes):
        admitted, ideal = [], None
        for algo in algos:
            row = next(
                r for r in scenario_rows
                if r["backend"] == "memory" and r["shape"] == shape and r["algorithm"] == algo
            )
            admitted.append(row["admitted"])
            ideal = row["ideal_admits"]
        ax.bar(range(len(algos)), admitted, color="#4c78a8")
        if ideal:
            ax.axhline(ideal, color="crimson", linestyle="--", label=f"ideal={ideal:g}")
        ax.set_title(shape)
        ax.set_xticks(range(len(algos)))
        ax.set_xticklabels([a.replace("_", "\n") for a in algos], fontsize=8)
        ax.legend(fontsize=8)
        ax.grid(True, axis="y", alpha=0.3)
    axes[0].set_ylabel("admitted requests")
    fig.suptitle("Accuracy — admitted vs ideal sustainable count")
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "accuracy.png", dpi=120)
    plt.close(fig)
    print("[plot] accuracy.png")


def plot_footprint(footprint_rows: list[dict]) -> None:
    """Per-client memory vs limit (log-log): the Sliding Window Log's O(limit)
    growth against the others' flat O(1)."""
    fig, ax = plt.subplots(figsize=(8.5, 5.5))
    for algo_name in ALGORITHMS:
        xs = [r["limit"] for r in footprint_rows if r["algorithm"] == algo_name]
        ys = [r["memory_bytes"] for r in footprint_rows if r["algorithm"] == algo_name]
        ax.plot(xs, ys, marker="o", label=algo_name, linewidth=1.8)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_title("Memory footprint per client vs limit (in-memory backend)")
    ax.set_xlabel("limit (requests per window)")
    ax.set_ylabel("bytes stored per client")
    ax.legend()
    ax.grid(True, which="both", alpha=0.3)
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "memory_footprint.png", dpi=120)
    plt.close(fig)
    print("[plot] memory_footprint.png")


def plot_latency(latency_rows: list[dict], has_redis: bool) -> None:
    """Per-call overhead, memory vs Redis, grouped by algorithm."""
    algos = [r["algorithm"] for r in latency_rows]
    mem = [r["memory_us_per_call"] for r in latency_rows]
    fig, ax = plt.subplots(figsize=(9, 5.5))
    x = range(len(algos))
    width = 0.38
    ax.bar([i - width / 2 for i in x], mem, width, label="memory", color="#4c78a8")
    if has_redis:
        red = [r.get("redis_us_per_call", 0) for r in latency_rows]
        ax.bar([i + width / 2 for i in x], red, width, label="redis", color="#e45756")
    ax.set_yscale("log")
    ax.set_title("Latency overhead per allow_request() call")
    ax.set_xlabel("algorithm")
    ax.set_ylabel("microseconds per call (log scale)")
    ax.set_xticks(list(x))
    ax.set_xticklabels([a.replace("_", "\n") for a in algos], fontsize=8)
    ax.legend()
    ax.grid(True, axis="y", which="both", alpha=0.3)
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "latency.png", dpi=120)
    plt.close(fig)
    print("[plot] latency.png")


# ------------------------------------------------------------------ main
def main() -> None:
    backends = make_backends()
    has_redis = "redis" in backends

    # Precompute the traffic once so every combo sees identical load.
    traffic = {name: fn(LIMIT, WINDOW, DURATION) for name, fn in SHAPES.items()}
    for name, ts in traffic.items():
        print(f"[traffic] {name}: {len(ts)} requests over {DURATION:g}s")

    # --- scenarios: accuracy + burst tolerance
    scenario_rows = []
    burst_curves = {}  # memory backend only, for the headline plot
    for algo_name, algo_cls in ALGORITHMS.items():
        for bname, backend in backends.items():
            for shape_name, ts in traffic.items():
                metrics, cum = run_scenario(algo_cls, backend, bname, shape_name, ts)
                scenario_rows.append(metrics)
                if bname == "memory" and shape_name == "bursty":
                    burst_curves[algo_name] = cum

    write_csv(
        RESULTS_DIR / "results.csv",
        scenario_rows,
        ["algorithm", "backend", "shape", "offered", "admitted",
         "admit_rate", "ideal_admits", "over_admit", "peak_per_window"],
    )

    # --- memory footprint
    footprint_rows = measure_footprint(backends)
    fp_fields = ["algorithm", "limit", "memory_bytes"] + (["redis_bytes"] if has_redis else [])
    write_csv(RESULTS_DIR / "memory_footprint.csv", footprint_rows, fp_fields)

    # --- latency
    latency_rows = measure_latency(backends)
    lat_fields = ["algorithm", "memory_us_per_call"] + (
        ["redis_us_per_call"] if has_redis else []
    )
    write_csv(RESULTS_DIR / "latency.csv", latency_rows, lat_fields)

    # --- plots
    plot_burst_tolerance(burst_curves)
    plot_accuracy(scenario_rows)
    plot_footprint(footprint_rows)
    plot_latency(latency_rows, has_redis)

    # --- console summary
    print("\n=== scenario results (admitted / ideal / peak-per-window) ===")
    for r in scenario_rows:
        print(
            f"{r['backend']:>6} {r['algorithm']:<24} {r['shape']:<9} "
            f"admitted={r['admitted']:>5}  ideal={r['ideal_admits']:>7}  "
            f"over_admit={r['over_admit']:>7}  peak/window={r['peak_per_window']:>4} "
            f"(limit={LIMIT})"
        )
    print("\nAll CSVs and PNGs written to benchmark/results/")


if __name__ == "__main__":
    main()
