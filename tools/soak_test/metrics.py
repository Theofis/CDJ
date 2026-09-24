"""Resource observations and per-process trends (restarts never hide growth)."""
from __future__ import annotations

import os
from pathlib import Path
import statistics

import psutil


class Sampler:
    def __init__(self, pid):
        self.process = psutil.Process(pid)
        self.process.cpu_percent()
        self.peak = 0

    def sample(self, elapsed, attempt):
        p = self.process
        with p.oneshot():
            mem = p.memory_info()
            self.peak = max(self.peak, mem.rss)
            data = {"elapsed_s": elapsed, "attempt": attempt, "pid": p.pid,
                    "ram_mb": mem.rss / 1048576, "ram_peak_mb": self.peak / 1048576,
                    "cpu_percent": p.cpu_percent(), "threads": p.num_threads(),
                    "handles": p.num_handles() if os.name == "nt" else p.num_fds()}
        data["system_load"] = os.getloadavg()[0] if hasattr(os, "getloadavg") else None
        thermal = Path("/sys/class/thermal/thermal_zone0/temp")
        data["temperature_c"] = float(thermal.read_text()) / 1000 if thermal.exists() else None
        return data


def trend(rows):
    if not rows:
        return {}
    result = {
        "ram_start_mb": rows[0]["ram_mb"], "ram_end_mb": rows[-1]["ram_mb"],
        "ram_max_mb": max(r["ram_mb"] for r in rows),
        "ram_growth_mb": rows[-1]["ram_mb"] - rows[0]["ram_mb"],
        "cpu_average": statistics.mean(r["cpu_percent"] for r in rows),
        "cpu_max": max(r["cpu_percent"] for r in rows), "process_trends": [],
        "threads_max": max(r["threads"] for r in rows),
        "handles_max": max(r["handles"] for r in rows),
    }
    for attempt in sorted({r["attempt"] for r in rows}):
        group = [r for r in rows if r["attempt"] == attempt]
        # Ignore the first minute of decoder/JIT/cache warmup in slope estimation.
        warm = [r for r in group if r["elapsed_s"] - group[0]["elapsed_s"] >= 60]
        slope = 0.0
        if len(warm) > 2:
            xs = [r["elapsed_s"] for r in warm]
            ys = [r["ram_mb"] for r in warm]
            mx, my = statistics.mean(xs), statistics.mean(ys)
            denom = sum((x - mx)**2 for x in xs)
            slope = sum((x-mx)*(y-my) for x, y in zip(xs, ys)) / denom * 3600 if denom else 0
        duration = group[-1]["elapsed_s"] - group[0]["elapsed_s"]
        growth = group[-1]["ram_mb"] - group[0]["ram_mb"]
        result["process_trends"].append({"attempt": attempt, "duration_s": duration,
            "growth_mb": growth, "mb_per_hour_after_warmup": slope,
            "handles_start": group[0]["handles"], "handles_end": group[-1]["handles"],
            "threads_start": group[0]["threads"], "threads_end": group[-1]["threads"],
            "warning": duration >= 300 and growth > 100 and slope > 50})
    return result
