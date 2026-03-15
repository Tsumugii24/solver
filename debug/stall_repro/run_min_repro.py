#!/usr/bin/env python3
"""Minimal stall reproducer for selected board indices."""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Iterable


ROOT_DIR = Path(__file__).resolve().parents[2]
WORK_DIR = Path(__file__).resolve().parent
CARDS_FILE = ROOT_DIR / "cards" / "cards.txt"
SIA_RANGE_FILE = ROOT_DIR / "ranges" / "sia.txt"
CONFIG_DIR = WORK_DIR / "configs"
LOG_DIR = WORK_DIR / "logs"
RESULT_DIR = WORK_DIR / "results"
RESOURCE_DIR = ROOT_DIR / "resources"


def resolve_solver_exe() -> Path:
    candidates: list[Path] = []
    if sys.platform == "win32":
        candidates.extend(
            [
                ROOT_DIR / "build" / "console_solver.exe",
                ROOT_DIR / "install" / "console_solver.exe",
                ROOT_DIR / "build" / "console_solver",
                ROOT_DIR / "install" / "console_solver",
            ]
        )
    else:
        candidates.extend(
            [
                ROOT_DIR / "install" / "console_solver",
                ROOT_DIR / "build" / "console_solver",
                ROOT_DIR / "install" / "console_solver.exe",
                ROOT_DIR / "build" / "console_solver.exe",
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


SOLVER_EXE = resolve_solver_exe()

CONFIG_TEMPLATE = """set_pot {pot}
set_effective_stack {effective_stack}
set_board {board}
set_range_oop {range_oop}
set_range_ip {range_ip}
set_bet_sizes oop,flop,bet,33
set_bet_sizes oop,flop,raise,50
set_bet_sizes oop,flop,allin
set_bet_sizes ip,flop,bet,30
set_bet_sizes ip,flop,raise,50
set_bet_sizes ip,flop,allin
set_bet_sizes oop,turn,bet,25
set_bet_sizes oop,turn,raise,150
set_bet_sizes oop,turn,donk,33
set_bet_sizes oop,turn,allin
set_bet_sizes ip,turn,bet,50
set_bet_sizes ip,turn,raise,75
set_bet_sizes ip,turn,allin
set_bet_sizes oop,river,bet,30
set_bet_sizes oop,river,raise,75
set_bet_sizes oop,river,donk,33
set_bet_sizes oop,river,allin
set_bet_sizes ip,river,bet,30
set_bet_sizes ip,river,raise,75
set_bet_sizes ip,river,allin
set_allin_threshold 0.5
set_raise_limit {raise_limit}
build_tree
{estimate_memory_line}set_thread_num {thread_num}
set_accuracy {accuracy}
set_max_iteration {max_iteration}
set_print_interval {print_interval}
set_use_isomorphism {use_isomorphism}
set_enable_range 1
start_solve
set_dump_format {dump_format}
set_dump_rounds 1
dump_result {output_file}
"""


@dataclass
class ReproResult:
    index: int
    board: str
    status: str
    elapsed: float
    log_path: Path
    config_path: Path
    result_path: Path
    note: str = ""


@dataclass
class IterationWatchdog:
    current_iter: str | None = None
    saw_player0: bool = False
    saw_player1: bool = False
    saw_total: bool = False
    last_progress_at: float = 0.0


def normalize_board(board: str) -> str:
    board = board.strip()
    if "," in board:
        return board
    return ",".join(board[i:i + 2] for i in range(0, len(board), 2) if board[i:i + 2])


def board_to_name(board: str) -> str:
    return board.replace(",", "")


def read_boards() -> list[str]:
    boards: list[str] = []
    with CARDS_FILE.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if text:
                boards.append(normalize_board(text))
    return boards


def load_sia_ranges() -> tuple[str, str]:
    oop = ""
    ip = ""
    with SIA_RANGE_FILE.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip().upper()
            value = value.strip().strip("\"'")
            if key == "OOP_RANGE":
                oop = value
            elif key == "IP_RANGE":
                ip = value
    if not oop or not ip:
        raise ValueError(f"missing OOP_RANGE or IP_RANGE in {SIA_RANGE_FILE}")
    return oop, ip


def parse_indices(expr: str, max_value: int) -> list[int]:
    indices: set[int] = set()
    for part in expr.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                start, end = end, start
            indices.update(range(start, end + 1))
        else:
            indices.add(int(part))
    return sorted(index for index in indices if 1 <= index <= max_value)


def ensure_layout() -> None:
    for directory in (CONFIG_DIR, LOG_DIR, RESULT_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def dump_format_to_extension(dump_format: str) -> str:
    if dump_format == "json":
        return ".json"
    return ".parquet"


def write_config(index: int, board: str, args: argparse.Namespace) -> tuple[Path, Path]:
    board_name = board_to_name(board)
    output_name = f"{index:04d}_{board_name}{dump_format_to_extension(args.dump_format)}"
    config_path = CONFIG_DIR / f"{index:04d}_{board_name}.txt"
    result_path = RESULT_DIR / output_name
    estimate_memory_line = "estimate_memory\n" if args.estimate_memory else ""
    config_text = CONFIG_TEMPLATE.format(
        pot=args.pot,
        effective_stack=args.stack,
        board=board,
        range_oop=args.range_oop,
        range_ip=args.range_ip,
        raise_limit=args.raise_limit,
        estimate_memory_line=estimate_memory_line,
        thread_num=args.thread_num,
        accuracy=args.accuracy,
        max_iteration=args.max_iteration,
        print_interval=args.print_interval,
        use_isomorphism=args.use_isomorphism,
        dump_format=args.dump_format,
        output_file=output_name,
    )
    config_path.write_text(config_text, encoding="utf-8")
    return config_path, result_path


def _start_reader(process: subprocess.Popen[str], log_handle) -> Queue:
    queue: Queue = Queue()

    def _reader() -> None:
        assert process.stdout is not None
        try:
            for line in iter(process.stdout.readline, ""):
                log_handle.write(line)
                log_handle.flush()
                queue.put(line)
        finally:
            queue.put(None)

    threading.Thread(target=_reader, daemon=True).start()
    return queue


def _terminate_process(process: subprocess.Popen[str]) -> None:
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


def update_iteration_watchdog(watchdog: IterationWatchdog, line: str, now: float) -> None:
    stripped = line.strip()
    if stripped.startswith("Iter:"):
        watchdog.current_iter = stripped.split(":", 1)[1].strip()
        watchdog.saw_player0 = False
        watchdog.saw_player1 = False
        watchdog.saw_total = False
        watchdog.last_progress_at = now
        return

    if watchdog.current_iter is None:
        return

    if stripped.startswith("player 0 exploitability"):
        watchdog.saw_player0 = True
        watchdog.last_progress_at = now
    elif stripped.startswith("player 1 exploitability"):
        watchdog.saw_player1 = True
        watchdog.last_progress_at = now
    elif stripped.startswith("Total exploitability"):
        watchdog.saw_total = True
        watchdog.last_progress_at = now
        watchdog.current_iter = None


def pending_iteration_stage(watchdog: IterationWatchdog) -> str | None:
    if watchdog.current_iter is None:
        return None
    if not watchdog.saw_player0:
        return "player 0 exploitability"
    if not watchdog.saw_player1:
        return "player 1 exploitability"
    if not watchdog.saw_total:
        return "Total exploitability"
    return None


def run_board(index: int, board: str, args: argparse.Namespace) -> ReproResult:
    ensure_layout()
    config_path, result_path = write_config(index, board, args)
    log_path = LOG_DIR / f"{index:04d}_{board_to_name(board)}.log"
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

    start_time = time.time()
    process: subprocess.Popen[str] | None = None
    with log_path.open("w", encoding="utf-8") as log_handle:
        log_handle.write(f"index={index}\nboard={board}\ncmd={' '.join(cmd)}\n")
        log_handle.write(f"range_oop={args.range_oop}\nrange_ip={args.range_ip}\n")
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
            output_queue = _start_reader(process, log_handle)
            last_output_at = time.time()
            watchdog = IterationWatchdog()

            while True:
                try:
                    item = output_queue.get(timeout=1)
                except Empty:
                    if process.poll() is not None:
                        break
                    pending_stage = pending_iteration_stage(watchdog)
                    if (
                        args.iter_stage_timeout > 0
                        and pending_stage is not None
                        and watchdog.last_progress_at > 0
                        and time.time() - watchdog.last_progress_at > args.iter_stage_timeout
                    ):
                        _terminate_process(process)
                        elapsed = time.time() - start_time
                        log_handle.write(
                            "\n[iter-stage-stall] "
                            f"iter {watchdog.current_iter} stuck waiting for {pending_stage} "
                            f"for {args.iter_stage_timeout} seconds\n"
                        )
                        return ReproResult(
                            index=index,
                            board=board,
                            status="stalled",
                            elapsed=elapsed,
                            log_path=log_path,
                            config_path=config_path,
                            result_path=result_path,
                            note=f"iter {watchdog.current_iter} stuck waiting for {pending_stage}",
                        )
                    if args.stall_timeout > 0 and time.time() - last_output_at > args.stall_timeout:
                        _terminate_process(process)
                        elapsed = time.time() - start_time
                        log_handle.write(
                            f"\n[stall] no new output for {args.stall_timeout} seconds\n"
                        )
                        return ReproResult(
                            index=index,
                            board=board,
                            status="stalled",
                            elapsed=elapsed,
                            log_path=log_path,
                            config_path=config_path,
                            result_path=result_path,
                            note=f"no output for {args.stall_timeout}s",
                        )
                    continue

                if item is None:
                    break

                print(item, end="")
                now = time.time()
                last_output_at = now
                update_iteration_watchdog(watchdog, item, now)

            process.wait(timeout=5)
            elapsed = time.time() - start_time
            if process.returncode == 0:
                return ReproResult(
                    index=index,
                    board=board,
                    status="ok",
                    elapsed=elapsed,
                    log_path=log_path,
                    config_path=config_path,
                    result_path=result_path,
                )

            return ReproResult(
                index=index,
                board=board,
                status="failed",
                elapsed=elapsed,
                log_path=log_path,
                config_path=config_path,
                result_path=result_path,
                note=f"return code {process.returncode}",
            )
        except subprocess.TimeoutExpired:
            if process is not None:
                _terminate_process(process)
            elapsed = time.time() - start_time
            return ReproResult(
                index=index,
                board=board,
                status="failed",
                elapsed=elapsed,
                log_path=log_path,
                config_path=config_path,
                result_path=result_path,
                note="wait timeout after process exit",
            )
        except Exception as exc:
            if process is not None:
                _terminate_process(process)
            elapsed = time.time() - start_time
            return ReproResult(
                index=index,
                board=board,
                status="failed",
                elapsed=elapsed,
                log_path=log_path,
                config_path=config_path,
                result_path=result_path,
                note=str(exc),
            )


def format_summary(results: Iterable[ReproResult]) -> str:
    lines = ["", "=" * 80, "Summary", "=" * 80]
    for result in results:
        lines.append(
            f"[{result.index}] {result.board} -> {result.status} "
            f"({result.elapsed:.1f}s) {result.note}".rstrip()
        )
        lines.append(f"  config: {result.config_path}")
        lines.append(f"  log:    {result.log_path}")
        lines.append(f"  result: {result.result_path}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal repro runner for selected board indices")
    parser.add_argument("--indices", default="46,48", help="Board indices, for example: 46,48 or 46-48")
    parser.add_argument("--range-oop", default=None)
    parser.add_argument("--range-ip", default=None)
    parser.add_argument("--pot", type=int, default=5)
    parser.add_argument("--stack", type=int, default=98)
    parser.add_argument("--thread-num", type=int, default=-1)
    parser.add_argument("--use-isomorphism", type=int, choices=[0, 1], default=1)
    parser.add_argument("--raise-limit", type=int, default=1)
    parser.add_argument("--accuracy", type=float, default=1.0)
    parser.add_argument("--max-iteration", type=int, default=1)
    parser.add_argument("--print-interval", type=int, default=1)
    parser.add_argument("--stall-timeout", type=int, default=20)
    parser.add_argument("--iter-stage-timeout", type=int, default=8)
    parser.add_argument("--mode", default="holdem")
    parser.add_argument("--dump-format", choices=["json", "parquet", "parquet_native"], default="parquet")
    parser.add_argument("--estimate-memory", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if not SOLVER_EXE.exists():
        print(f"solver not found: {SOLVER_EXE}", file=sys.stderr)
        return 1
    if not CARDS_FILE.exists():
        print(f"cards file not found: {CARDS_FILE}", file=sys.stderr)
        return 1
    if not SIA_RANGE_FILE.exists():
        print(f"sia range file not found: {SIA_RANGE_FILE}", file=sys.stderr)
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
    print("Minimal stall repro")
    print("=" * 80)
    print(f"indices={indices}")
    print(f"range_oop={args.range_oop}")
    print(f"range_ip={args.range_ip}")
    print(f"thread_num={args.thread_num}")
    print(f"use_isomorphism={args.use_isomorphism}")
    print(f"max_iteration={args.max_iteration}")
    print(f"estimate_memory={args.estimate_memory}")
    print(f"stall_timeout={args.stall_timeout}")
    print(f"iter_stage_timeout={args.iter_stage_timeout}")
    print(f"dump_format={args.dump_format}")

    results: list[ReproResult] = []
    for index in indices:
        board = all_boards[index - 1]
        print("\n" + "-" * 80)
        print(f"[{index}] {board}")
        print("-" * 80)
        results.append(run_board(index, board, args))

    print(format_summary(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


