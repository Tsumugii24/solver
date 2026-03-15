#!/usr/bin/env python3
"""Compare solver estimate_memory output against observed Windows process memory."""

from __future__ import annotations

import argparse
import ctypes
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from queue import Empty, Queue
from typing import Iterable

from run_min_repro import (
    LOG_DIR,
    RESULT_DIR,
    RESOURCE_DIR,
    SOLVER_EXE,
    board_to_name,
    ensure_layout,
    load_sia_ranges,
    parse_indices,
    read_boards,
    write_config,
)


if sys.platform != "win32":
    raise SystemExit("compare_memory_estimate.py is intended for Windows only")


PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.CloseHandle.restype = ctypes.c_int
psapi.GetProcessMemoryInfo.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX),
    ctypes.c_ulong,
]
psapi.GetProcessMemoryInfo.restype = ctypes.c_int


MEMORY_LINE_PATTERNS = {
    "tree_gb": re.compile(r"Tree structure:\s+([0-9.]+)\s+GB"),
    "solver_state_gb": re.compile(r"Solver state:\s+([0-9.]+)\s+GB"),
    "trainable_slots_gb": re.compile(r"Trainable slots:\s+([0-9.]+)\s+GB"),
    "trainable_data_gb": re.compile(r"Trainable data:\s+([0-9.]+)\s+GB"),
    "river_cache_gb": re.compile(r"River cache .*:\s+([0-9.]+)\s+GB"),
    "working_gb": re.compile(r"Working buffers:\s+([0-9.]+)\s+GB"),
    "safety_margin_gb": re.compile(r"Safety margin:\s+([0-9.]+)\s+GB"),
    "persistent_lower_bound_gb": re.compile(r"Persistent lower bound:\s+([0-9.]+)\s+GB"),
    "likely_peak_gb": re.compile(r"Likely peak while solving:\s+([0-9.]+)\s+GB"),
}


@dataclass
class MemorySample:
    ts: float
    working_set: int
    private_usage: int
    peak_working_set: int
    peak_pagefile: int


@dataclass
class MemoryEstimate:
    tree_gb: float | None = None
    solver_state_gb: float | None = None
    trainable_slots_gb: float | None = None
    trainable_data_gb: float | None = None
    river_cache_gb: float | None = None
    working_gb: float | None = None
    safety_margin_gb: float | None = None
    persistent_lower_bound_gb: float | None = None
    likely_peak_gb: float | None = None


@dataclass
class ComparisonResult:
    index: int
    board: str
    status: str
    elapsed: float
    config_path: Path
    log_path: Path
    result_path: Path
    report_path: Path
    note: str = ""
    estimate: MemoryEstimate | None = None
    samples_collected: int = 0
    start_solve_private_gb: float | None = None
    peak_private_gb: float | None = None
    delta_private_gb: float | None = None
    start_solve_working_set_gb: float | None = None
    peak_working_set_gb: float | None = None
    delta_working_set_gb: float | None = None


def bytes_to_gb(value: int | None) -> float | None:
    if value is None:
        return None
    return value / (1024.0 ** 3)


def open_process_for_memory(pid: int) -> ctypes.c_void_p:
    desired_access = PROCESS_QUERY_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ
    handle = kernel32.OpenProcess(desired_access, 0, pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), f"OpenProcess failed for pid={pid}")
    return handle


def read_memory_sample(handle: ctypes.c_void_p) -> MemorySample:
    counters = PROCESS_MEMORY_COUNTERS_EX()
    counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
    ok = psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
    if not ok:
        raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
    return MemorySample(
        ts=time.monotonic(),
        working_set=int(counters.WorkingSetSize),
        private_usage=int(counters.PrivateUsage),
        peak_working_set=int(counters.PeakWorkingSetSize),
        peak_pagefile=int(counters.PeakPagefileUsage),
    )


def start_output_reader(process: subprocess.Popen[str], log_handle) -> Queue:
    queue: Queue = Queue()

    def _reader() -> None:
        assert process.stdout is not None
        try:
            for line in iter(process.stdout.readline, ""):
                log_handle.write(line)
                log_handle.flush()
                queue.put((time.monotonic(), line))
        finally:
            queue.put((time.monotonic(), None))

    threading.Thread(target=_reader, daemon=True).start()
    return queue


def start_memory_sampler(process: subprocess.Popen[str], sample_interval: float, samples: list[MemorySample]) -> threading.Event:
    stop_event = threading.Event()

    def _sampler() -> None:
        try:
            handle = open_process_for_memory(process.pid)
        except OSError:
            return

        try:
            while not stop_event.is_set():
                if process.poll() is not None:
                    try:
                        samples.append(read_memory_sample(handle))
                    except OSError:
                        pass
                    break
                try:
                    samples.append(read_memory_sample(handle))
                except OSError:
                    break
                time.sleep(sample_interval)
        finally:
            kernel32.CloseHandle(handle)

    threading.Thread(target=_sampler, daemon=True).start()
    return stop_event


def nearest_sample(samples: list[MemorySample], ts: float) -> MemorySample | None:
    if not samples:
        return None
    return min(samples, key=lambda sample: abs(sample.ts - ts))


def parse_estimate_line(estimate: MemoryEstimate, line: str) -> None:
    for field_name, pattern in MEMORY_LINE_PATTERNS.items():
        match = pattern.search(line)
        if match:
            setattr(estimate, field_name, float(match.group(1)))
            return


def terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
        return
    except subprocess.TimeoutExpired:
        pass
    process.kill()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass


def write_report(report_path: Path, result: ComparisonResult) -> None:
    lines = [
        f"index={result.index}",
        f"board={result.board}",
        f"status={result.status}",
        f"elapsed={result.elapsed:.3f}",
        f"note={result.note}",
        f"samples_collected={result.samples_collected}",
    ]
    if result.estimate is not None:
        for key, value in asdict(result.estimate).items():
            lines.append(f"{key}={value}")
    lines.extend(
        [
            f"start_solve_private_gb={result.start_solve_private_gb}",
            f"peak_private_gb={result.peak_private_gb}",
            f"delta_private_gb={result.delta_private_gb}",
            f"start_solve_working_set_gb={result.start_solve_working_set_gb}",
            f"peak_working_set_gb={result.peak_working_set_gb}",
            f"delta_working_set_gb={result.delta_working_set_gb}",
        ]
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_comparison(index: int, board: str, args: argparse.Namespace) -> ComparisonResult:
    ensure_layout()
    args.estimate_memory = True
    config_path, result_path = write_config(index, board, args)
    stem = f"{index:04d}_{board_to_name(board)}"
    log_path = LOG_DIR / f"{stem}.memory.log"
    report_path = LOG_DIR / f"{stem}.memory_report.txt"
    if result_path.exists():
        result_path.unlink()

    cmd = [
        str(SOLVER_EXE),
        "-i",
        str(config_path.resolve()),
        "-r",
        str(RESOURCE_DIR),
        "-m",
        args.mode,
    ]

    process: subprocess.Popen[str] | None = None
    start_time = time.monotonic()
    estimate = MemoryEstimate()
    samples: list[MemorySample] = []
    start_solve_ts: float | None = None

    with log_path.open("w", encoding="utf-8") as log_handle:
        log_handle.write(f"index={index}\nboard={board}\ncmd={' '.join(cmd)}\n")
        log_handle.write("=" * 80 + "\n")
        log_handle.flush()

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(RESULT_DIR),
            )
            output_queue = start_output_reader(process, log_handle)
            sampler_stop = start_memory_sampler(process, args.sample_interval, samples)

            while True:
                try:
                    line_ts, item = output_queue.get(timeout=1)
                except Empty:
                    if process.poll() is not None:
                        break
                    continue

                if item is None:
                    break

                print(item, end="")
                parse_estimate_line(estimate, item)
                if item.strip() == "<<<START SOLVING>>>":
                    start_solve_ts = line_ts

            process.wait(timeout=5)
            sampler_stop.set()
            time.sleep(args.sample_interval * 2)
            elapsed = time.monotonic() - start_time

            peak_private = max((sample.private_usage for sample in samples), default=0)
            peak_working = max((sample.working_set for sample in samples), default=0)
            solve_sample = nearest_sample(samples, start_solve_ts) if start_solve_ts is not None else None
            start_private = solve_sample.private_usage if solve_sample else None
            start_working = solve_sample.working_set if solve_sample else None

            result = ComparisonResult(
                index=index,
                board=board,
                status="ok" if process.returncode == 0 else "failed",
                elapsed=elapsed,
                config_path=config_path,
                log_path=log_path,
                result_path=result_path,
                report_path=report_path,
                note="" if process.returncode == 0 else f"return code {process.returncode}",
                estimate=estimate,
                samples_collected=len(samples),
                start_solve_private_gb=bytes_to_gb(start_private),
                peak_private_gb=bytes_to_gb(peak_private),
                delta_private_gb=bytes_to_gb(peak_private - start_private) if start_private is not None else None,
                start_solve_working_set_gb=bytes_to_gb(start_working),
                peak_working_set_gb=bytes_to_gb(peak_working),
                delta_working_set_gb=bytes_to_gb(peak_working - start_working) if start_working is not None else None,
            )
            write_report(report_path, result)
            return result
        except Exception as exc:
            if process is not None:
                terminate_process(process)
            result = ComparisonResult(
                index=index,
                board=board,
                status="failed",
                elapsed=time.monotonic() - start_time,
                config_path=config_path,
                log_path=log_path,
                result_path=result_path,
                report_path=report_path,
                note=str(exc),
                estimate=estimate,
                samples_collected=len(samples),
            )
            write_report(report_path, result)
            return result


def format_summary(results: Iterable[ComparisonResult]) -> str:
    lines = ["", "=" * 80, "Memory Comparison Summary", "=" * 80]
    for result in results:
        lines.append(f"[{result.index}] {result.board} -> {result.status} ({result.elapsed:.1f}s) {result.note}".rstrip())
        if result.estimate is not None:
            lines.append(
                "  estimate:"
                f" persistent={result.estimate.persistent_lower_bound_gb} GB,"
                f" likely_peak={result.estimate.likely_peak_gb} GB"
            )
        lines.append(
            "  actual private:"
            f" start_solve={result.start_solve_private_gb} GB,"
            f" peak={result.peak_private_gb} GB,"
            f" delta={result.delta_private_gb} GB"
        )
        lines.append(
            "  actual working set:"
            f" start_solve={result.start_solve_working_set_gb} GB,"
            f" peak={result.peak_working_set_gb} GB,"
            f" delta={result.delta_working_set_gb} GB"
        )
        lines.append(f"  samples={result.samples_collected}")
        lines.append(f"  config:  {result.config_path}")
        lines.append(f"  log:     {result.log_path}")
        lines.append(f"  report:  {result.report_path}")
        lines.append(f"  result:  {result.result_path}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare estimate_memory with observed Windows process memory")
    parser.add_argument("--indices", default="46,48", help="Board indices, for example: 46,48 or 46-48")
    parser.add_argument("--range-oop", default=None)
    parser.add_argument("--range-ip", default=None)
    parser.add_argument("--pot", type=int, default=5)
    parser.add_argument("--stack", type=int, default=98)
    parser.add_argument("--thread-num", type=int, default=1)
    parser.add_argument("--use-isomorphism", type=int, choices=[0, 1], default=1)
    parser.add_argument("--accuracy", type=float, default=1.0)
    parser.add_argument("--max-iteration", type=int, default=1)
    parser.add_argument("--print-interval", type=int, default=1)
    parser.add_argument("--mode", default="holdem")
    parser.add_argument("--dump-format", choices=["json", "parquet"], default="json")
    parser.add_argument("--sample-interval", type=float, default=0.05)
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if not SOLVER_EXE.exists():
        print(f"solver not found: {SOLVER_EXE}", file=sys.stderr)
        return 1

    if args.range_oop is None or args.range_ip is None:
        default_oop, default_ip = load_sia_ranges()
        if args.range_oop is None:
            args.range_oop = default_oop
        if args.range_ip is None:
            args.range_ip = default_ip

    all_boards = read_boards()
    indices = parse_indices(args.indices, len(all_boards))
    if not indices:
        print("no valid indices", file=sys.stderr)
        return 1

    print("=" * 80)
    print("Windows Memory Estimate Comparison")
    print("=" * 80)
    print(f"indices={indices}")
    print(f"thread_num={args.thread_num}")
    print(f"use_isomorphism={args.use_isomorphism}")
    print(f"max_iteration={args.max_iteration}")
    print(f"dump_format={args.dump_format}")
    print(f"sample_interval={args.sample_interval}")

    results: list[ComparisonResult] = []
    for index in indices:
        board = all_boards[index - 1]
        print("\n" + "-" * 80)
        print(f"[{index}] {board}")
        print("-" * 80)
        results.append(run_comparison(index, board, args))

    print(format_summary(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
