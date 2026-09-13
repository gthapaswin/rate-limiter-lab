"""Evaluate the AdaptiveRateLimiter against the fixed-choice algorithms.

Runs a three-phase timeline -- steady, then bursty, then steady -- through four
strategies and scores each phase separately:

  * steady phases -> "precision": how far admitted strays above the ideal
    sustainable count (lower is better; over-admission = leaking).
  * bursty phase  -> "absorption": how many burst requests are admitted (higher
    means fewer 429s on legitimate spikes) and "overshoot": the worst admitted
    count in any single window (should stay near the limit).

The thesis the numbers test: adaptive should inherit the *steady precision of a
sliding counter* AND the *burst absorption of a token bucket* -- a point neither
fixed choice reaches -- by switching between them as the shape changes, with no
advance knowledge of the phases.

Run:
    python benchmark/adaptive_eval.py
Writes adaptive_eval.csv and adaptive_eval.png into benchmark/results/.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ratelimiter import MemoryBackend, build_limiter  # noqa: E402
from ratelimiter.adaptive import _CANDIDATE_CLASSES  # noqa: E402
from benchmark.load_generator import mixed_traffic  # noqa: E402

LIMIT = 100
WINDOW = 1.0
PHASE_WINDOWS = 5
CLIENT = "client"

RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

ALGO_COLORS = {
    "fixed_window": "#9aa0a6",
    "sliding_window_log": "#8fce8f",
    "sliding_window_counter": "#7fb0d8",
    "token_bucket": "#e59aa0",
}


def peak_in_window(times: list[float], window: float) -> int:
    if not times:
        return 0
    peak = left = 0
    for right in range(len(times)):
        while times[right] - times[left] >= window:
            left += 1
        peak = max(peak, right - left + 1)
    return peak


def run_strategy(name: str, timestamps: list[float]):
    """Run one strategy over the timeline. Returns (cumulative curve,
    admitted_times, active_algo_track)."""
    if name == "adaptive":
        limiter = build_limiter(
            "adaptive", "memory", limit=LIMIT, window=WINDOW, min_dwell_windows=1
        )
    else:
        limiter = _CANDIDATE_CLASSES[name](
            MemoryBackend(), limit=LIMIT, window=WINDOW, namespace="eval"
        )

    cumulative, admitted_times, active_track = [], [], []
    admitted = 0
    for t in timestamps:
        if limiter.allow_request(CLIENT, now=t):
            admitted += 1
            admitted_times.append(t)
        cumulative.append((t, admitted))
        if name == "adaptive":
            active_track.append((t, limiter.stats(CLIENT)["active"]))

    switches = limiter.stats(CLIENT)["switches"] if name == "adaptive" else 0
    return cumulative, admitted_times, active_track, switches


def phase_metrics(admitted_times, phases):
    """Per-phase admitted count, ideal, over-admit, and peak-per-window."""
    out = {}
    for i, (start, end, label) in enumerate(phases):
        in_phase = [t for t in admitted_times if start <= t < end]
        ideal = LIMIT * ((end - start) / WINDOW)
        out[f"phase{i}_{label}"] = {
            "admitted": len(in_phase),
            "ideal": round(ideal, 1),
            "over_admit": round(len(in_phase) - ideal, 1),
            "peak_per_window": peak_in_window(in_phase, WINDOW),
        }
    return out


def main() -> None:
    timestamps, phases = mixed_traffic(LIMIT, WINDOW, phase_windows=PHASE_WINDOWS)
    print(f"[traffic] mixed timeline: {len(timestamps)} requests, phases={phases}")

    strategies = ["fixed_window", "sliding_window_counter", "token_bucket", "adaptive"]
    results = {}
    adaptive_track = None
    for name in strategies:
        cum, admits, track, switches = run_strategy(name, timestamps)
        results[name] = {
            "cumulative": cum,
            "metrics": phase_metrics(admits, phases),
            "switches": switches,
        }
        if name == "adaptive":
            adaptive_track = track

    # ---- CSV ----
    csv_path = RESULTS_DIR / "adaptive_eval.csv"
    phase_keys = list(next(iter(results.values()))["metrics"].keys())
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["strategy", "phase", "admitted", "ideal", "over_admit", "peak_per_window"])
        for name in strategies:
            for pk in phase_keys:
                m = results[name]["metrics"][pk]
                w.writerow([name, pk, m["admitted"], m["ideal"], m["over_admit"], m["peak_per_window"]])
    print(f"[write] results/{csv_path.name}")

    # ---- plot ----
    fig, ax = plt.subplots(figsize=(11, 6))

    # Background shading = which algorithm adaptive was using.
    if adaptive_track:
        seg_start = adaptive_track[0][0]
        seg_algo = adaptive_track[0][1]
        for (t, algo) in adaptive_track[1:] + [(adaptive_track[-1][0], None)]:
            if algo != seg_algo:
                ax.axvspan(seg_start, t, color=ALGO_COLORS.get(seg_algo, "#eee"), alpha=0.25)
                seg_start, seg_algo = t, algo

    line_styles = {
        "fixed_window": ("--", "#5f6368"),
        "sliding_window_counter": ("-", "#1a73e8"),
        "token_bucket": ("-", "#d93025"),
        "adaptive": ("-", "black"),
    }
    for name in strategies:
        xs = [t for t, _ in results[name]["cumulative"]]
        ys = [c for _, c in results[name]["cumulative"]]
        ls, color = line_styles[name]
        lw = 2.6 if name == "adaptive" else 1.6
        ax.step(xs, ys, where="post", label=name, linestyle=ls, color=color, linewidth=lw)

    # Phase boundaries.
    for (start, end, label) in phases:
        ax.axvline(end, color="k", alpha=0.2, linewidth=1)
    for (start, end, label) in phases:
        ax.text((start + end) / 2, ax.get_ylim()[1] * 0.02, label.upper(),
                ha="center", fontsize=9, alpha=0.6)

    # Legend: strategies + a note on the shading.
    shade_handles = [
        mpatches.Patch(color=ALGO_COLORS[a], alpha=0.25, label=f"adaptive using {a}")
        for a in ["sliding_window_counter", "token_bucket"]
    ]
    handles, labels = ax.get_legend_handles_labels()
    ax.legend(handles + shade_handles, labels + [h.get_label() for h in shade_handles],
              fontsize=8, loc="upper left")

    ax.set_title("Adaptive limiter vs fixed choices — cumulative admits over a "
                 "steady → bursty → steady timeline")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("cumulative admitted")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "adaptive_eval.png", dpi=120)
    plt.close(fig)
    print("[plot] adaptive_eval.png")

    # ---- console summary ----
    print("\n=== per-phase results (limit=100/window) ===")
    header = f"{'strategy':<24}" + "".join(f"{pk:>26}" for pk in phase_keys)
    print(header)
    for name in strategies:
        row = f"{name:<24}"
        for pk in phase_keys:
            m = results[name]["metrics"][pk]
            row += f"{'adm=' + str(m['admitted']) + ' peak=' + str(m['peak_per_window']):>26}"
        print(row)
    print(f"\nadaptive switches: {results['adaptive']['switches']}")
    print("\nRead: in STEADY phases, over-admit near 0 is precise; in the BURSTY "
          "phase, higher admitted = more legitimate-burst absorption, and peak "
          "near 100 = limit respected. Adaptive should track the sliding counter "
          "in steady phases and the token bucket in the bursty phase.")


if __name__ == "__main__":
    main()
