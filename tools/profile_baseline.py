#!/usr/bin/env python3
"""
Eve performance baseline profiler  (zero external deps — stdlib + PowerShell).

Purpose
-------
Capture a *before* snapshot so every later optimization can be judged against
real numbers instead of intuition (see ROADMAP -> "Performance & Efficiency").

It has two parts:

  Part A  Headless micro-benchmarks — safe to run ANY time, no Eve running, no
          side effects. Times the dispatch routing hot path (registry match),
          the fuzzy-catalog build, and feature-snapshot cost. This is the
          "how fast is the understanding path" number, minus STT.

  Part B  Live process sampler — needs Eve already running. Finds Eve's Python
          process and its Electron windows, then samples CPU%, memory (RSS),
          and thread count at idle over a short window. This is the "does it
          sip resources while doing nothing" number.

Usage
-----
    python tools/profile_baseline.py                 # A always; B if Eve is up
    python tools/profile_baseline.py --seconds 30    # longer idle sample
    python tools/profile_baseline.py --no-live       # micro-benchmarks only

Output
------
Prints a human-readable report AND writes it to
    profiling/baseline_<YYYYmmdd_HHMMSS>.txt
so you can paste the path back here and I read the file — no hand-copying
numbers from Task Manager.

Notes
-----
* Latency of a full "wake word -> spoken reply" round trip is deliberately NOT
  measured here: it needs the mic + a spoken command and is inherently
  interactive/distracting. Part A covers the non-audio half (routing+handler
  selection); STT + TTS latency should be eyeballed during real use.
* Part B identifies Eve processes by matching this repo's path in each
  process command line, so it won't mistake VS Code / other Electron apps for
  Eve.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "profiling"


# ──────────────────────────────────────────────────────────────────────────────
# small helpers
# ──────────────────────────────────────────────────────────────────────────────

def _ps(script: str, timeout: float = 30.0) -> str:
    """Run a PowerShell snippet and return stdout (empty string on failure)."""
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
        )
        return p.stdout or ""
    except Exception:
        return ""


def _fmt_mb(nbytes: float) -> str:
    return f"{nbytes / (1024 * 1024):7.1f} MB"


class Report:
    """Collects lines, prints them live, and writes them to a file at the end."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str = "") -> None:
        print(line)
        self.lines.append(line)

    def save(self) -> Path:
        OUT_DIR.mkdir(exist_ok=True)
        path = OUT_DIR / f"baseline_{datetime.now():%Y%m%d_%H%M%S}.txt"
        path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        return path


# ──────────────────────────────────────────────────────────────────────────────
# Part A — headless micro-benchmarks (no side effects)
# ──────────────────────────────────────────────────────────────────────────────

# Representative phrases that exercise routing WITHOUT executing anything —
# .best() only *matches*; it never calls the handler, so no apps launch.
_ROUTING_PHRASES = [
    "what time is it",
    "snap firefox to the top",
    "open the app manager",
    "bring discord to the front",
    "remind me to call mom at 3pm",
    "what's the weather",              # a miss — exercises the no-match path
    "protect this game",
    "move hud to top left of monitor 2",
]


def _bench(fn, iterations: int) -> float:
    """Return average milliseconds per call over `iterations` runs."""
    start = time.perf_counter()
    for _ in range(iterations):
        fn()
    return (time.perf_counter() - start) / iterations * 1000.0


def part_a(report: Report) -> None:
    report("=" * 68)
    report("PART A — Headless micro-benchmarks (routing hot path, no side effects)")
    report("=" * 68)

    # Import cost of the dispatch stack (command modules, registry) — this is
    # part of cold-start latency.
    t0 = time.perf_counter()
    try:
        import core.dispatcher as d
    except Exception as e:
        report(f"  ! could not import core.dispatcher: {e}")
        return
    report(f"  import core.dispatcher (+command modules) : {(time.perf_counter()-t0)*1000:8.1f} ms  (one-time, at startup)")

    # Routing: registry.best() over representative phrases. Warm once (builds the
    # lazy registry), then time.
    try:
        reg = d._registry()
        reg.best("warm up")  # build/prime
        def _route_all():
            for ph in _ROUTING_PHRASES:
                reg.best(ph)
        per_call = _bench(_route_all, 200) / len(_ROUTING_PHRASES)
        report(f"  registry.best() per phrase (avg)          : {per_call:8.3f} ms  ({len(_ROUTING_PHRASES)} phrases x200)")
    except Exception as e:
        report(f"  ! routing benchmark failed: {e}")

    # Fuzzy catalog build + one best_match (only hit on a routing miss). Needs
    # rapidfuzz; report clearly if it's absent.
    try:
        from core import intent_match
        t0 = time.perf_counter()
        catalog = intent_match.build_catalog()
        build_ms = (time.perf_counter() - t0) * 1000.0
        match_ms = _bench(lambda: intent_match.best_match("open the ap manger", catalog), 50)
        report(f"  intent_match.build_catalog()              : {build_ms:8.3f} ms  ({len(catalog)} phrases)")
        report(f"  intent_match.best_match() (avg)           : {match_ms:8.3f} ms  (fuzzy path, only on a miss)")
    except ModuleNotFoundError as e:
        report(f"  intent_match: SKIPPED — {e} (pip install rapidfuzz to measure the fuzzy path)")
    except Exception as e:
        report(f"  ! fuzzy benchmark failed: {e}")

    # Feature snapshot cost — this dict-building happens on EVERY display
    # broadcast, so it's on the per-command UI path.
    try:
        from core import features
        def _snap_features():
            s = {
                "features":       features.all_features(),
                "feature_status": features.all_status(),
                "feature_labels": features.LABELS,
                "feature_alpha":  features.alpha_keys(),
            }
            return json.dumps(s)
        snap_ms = _bench(_snap_features, 500)
        report(f"  feature-snapshot build + json.dumps (avg) : {snap_ms:8.3f} ms  (runs on every HUD broadcast)")
    except Exception as e:
        report(f"  ! feature-snapshot benchmark failed: {e}")

    report("")


# ──────────────────────────────────────────────────────────────────────────────
# Part B — live process sampler (needs Eve running)
# ──────────────────────────────────────────────────────────────────────────────

def _find_eve_processes() -> list[dict]:
    """Return [{pid, name, kind, cmdline}] for Eve's Python + Electron procs.

    Discriminated by this repo's path appearing in the command line, so other
    Python/Electron apps (e.g. VS Code) are not picked up.
    """
    repo_str = str(REPO).replace("\\", "\\\\")
    script = (
        "Get-CimInstance Win32_Process "
        "| Where-Object { $_.Name -match 'python|electron' } "
        "| Select-Object ProcessId,Name,CommandLine "
        "| ConvertTo-Json -Depth 3"
    )
    raw = _ps(script)
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]

    repo_low = str(REPO).lower()
    ui_low = str(REPO / "ui").lower()
    out: list[dict] = []
    for proc in data:
        cmd = (proc.get("CommandLine") or "")
        name = (proc.get("Name") or "")
        pid = proc.get("ProcessId")
        if pid is None:
            continue
        cl = cmd.lower()
        if repo_low not in cl and ui_low not in cl:
            continue
        if "electron" in name.lower():
            kind = "Electron (UI)"
        elif "main.py" in cl:
            kind = "Python (engine)"
        else:
            # a python proc in the repo but not main.py (e.g. this profiler) — skip
            if "profile_baseline" in cl:
                continue
            kind = "Python (other)"
        out.append({"pid": int(pid), "name": name, "kind": kind, "cmdline": cmd})
    return out


def _sample_procs(pids: list[int]) -> dict[int, dict]:
    """One-shot per-pid {cpu_seconds, rss_bytes, threads} via Get-Process."""
    if not pids:
        return {}
    id_list = ",".join(str(p) for p in pids)
    script = (
        f"Get-Process -Id {id_list} -ErrorAction SilentlyContinue "
        "| Select-Object Id,CPU,WorkingSet64,@{n='Threads';e={$_.Threads.Count}} "
        "| ConvertTo-Json -Depth 2"
    )
    raw = _ps(script)
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if isinstance(data, dict):
        data = [data]
    out: dict[int, dict] = {}
    for row in data:
        pid = row.get("Id")
        if pid is None:
            continue
        out[int(pid)] = {
            "cpu": float(row.get("CPU") or 0.0),
            "rss": float(row.get("WorkingSet64") or 0.0),
            "threads": int(row.get("Threads") or 0),
        }
    return out


def part_b(report: Report, seconds: int) -> None:
    report("=" * 68)
    report(f"PART B — Live idle sampler ({seconds}s window)")
    report("=" * 68)

    procs = _find_eve_processes()
    if not procs:
        report("  Eve does not appear to be running (no engine/Electron process")
        report("  found under this repo path).")
        report("  -> Launch Eve, leave it IDLE (don't talk to it), then re-run:")
        report("     python tools/profile_baseline.py")
        report("")
        return

    pids = [p["pid"] for p in procs]
    by_pid = {p["pid"]: p for p in procs}
    ncpu = os.cpu_count() or 1
    report(f"  Found {len(procs)} Eve process(es); sampling at idle. CPU% is of ALL cores ({ncpu} logical).")
    report(f"  Keep Eve idle (no commands) during the {seconds}s window for a clean baseline.")
    report("")

    first = _sample_procs(pids)
    t0 = time.perf_counter()

    # Collect RSS samples across the window; CPU is measured start->end via the
    # process' cumulative CPU-seconds counter.
    rss_samples: dict[int, list[float]] = {p: [] for p in pids}
    threads_last: dict[int, int] = {}
    interval = max(1.0, seconds / 8.0)
    elapsed = 0.0
    while elapsed < seconds:
        time.sleep(interval)
        snap = _sample_procs(pids)
        for pid, s in snap.items():
            rss_samples.setdefault(pid, []).append(s["rss"])
            threads_last[pid] = s["threads"]
        elapsed = time.perf_counter() - t0

    last = _sample_procs(pids)
    wall = time.perf_counter() - t0

    report(f"  {'process':<18}{'CPU %':>9}{'RSS avg':>13}{'RSS peak':>13}{'threads':>9}")
    report(f"  {'-'*17:<18}{'-'*8:>9}{'-'*12:>13}{'-'*12:>13}{'-'*8:>9}")

    tot_cpu = tot_rss = tot_threads = 0.0
    for pid in pids:
        f0 = first.get(pid)
        f1 = last.get(pid)
        if not f0 or not f1:
            report(f"  pid {pid}: exited or unreadable during sampling")
            continue
        cpu_pct = (f1["cpu"] - f0["cpu"]) / wall / ncpu * 100.0
        samples = rss_samples.get(pid) or [f1["rss"]]
        rss_avg = sum(samples) / len(samples)
        rss_peak = max(samples)
        threads = threads_last.get(pid, f1["threads"])
        label = by_pid[pid]["kind"]
        report(f"  {label:<18}{cpu_pct:>8.1f}%{_fmt_mb(rss_avg):>13}{_fmt_mb(rss_peak):>13}{threads:>9}")
        tot_cpu += cpu_pct
        tot_rss += rss_avg
        tot_threads += threads

    report(f"  {'-'*17:<18}{'-'*8:>9}{'-'*12:>13}{'-'*12:>13}{'-'*8:>9}")
    report(f"  {'TOTAL':<18}{tot_cpu:>8.1f}%{_fmt_mb(tot_rss):>13}{'':>13}{int(tot_threads):>9}")
    report("")
    report("  Interpretation hints:")
    report("   * Idle CPU% well above ~0 means something is busy-looping/animating")
    report("     when it shouldn't be (prime suspect: the orb's 60fps canvas).")
    report("   * Compare TOTAL RSS before/after any lazy-window or Electron change.")
    report("")


# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Eve performance baseline profiler")
    ap.add_argument("--seconds", type=int, default=16,
                    help="idle sampling window for Part B (default 16)")
    ap.add_argument("--no-live", action="store_true",
                    help="skip Part B (headless micro-benchmarks only)")
    args = ap.parse_args()

    # Make `import core...` work regardless of where this is run from.
    sys.path.insert(0, str(REPO))

    report = Report()
    report(f"Eve performance baseline — {datetime.now():%Y-%m-%d %H:%M:%S}")
    report(f"repo: {REPO}")
    report(f"python: {sys.version.split()[0]}   logical CPUs: {os.cpu_count()}")
    report("")

    part_a(report)
    if not args.no_live:
        part_b(report, args.seconds)

    path = report.save()
    print(f"\nReport written to: {path}")
    print("Paste that path back to Claude and it will read the numbers.")


if __name__ == "__main__":
    main()
