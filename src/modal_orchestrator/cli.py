"""Command-line entry point: run | status."""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from .scheduler import Scheduler, SchedulerConfig
from .state import StateStore, Status
from .tokens import load_tokens, MalformedTokenLine

DEFAULT_CMD_TEMPLATE = ["modal", "run", "{config}"]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="modal-orchestrator",
        description="Run a Modal workload across many workspace tokens.",
    )
    sub = p.add_subparsers(dest="subcommand", required=True)

    pr = sub.add_parser("run", help="Run the orchestrator over a token pool.")
    pr.add_argument("--tokens", required=True, type=Path,
                    help="Path to a CSV file of token_id,token_secret pairs.")
    pr.add_argument("--config", required=True,
                    help="Path or module reference passed to `modal run`.")
    pr.add_argument("--state", default=Path("state.json"), type=Path,
                    help="Path to the per-token JSON state file (default: ./state.json).")
    pr.add_argument("--logs", default=Path("logs"), type=Path,
                    help="Directory for per-token log files (default: ./logs).")
    pr.add_argument("--max-parallel", type=int, default=20,
                    help="Maximum number of concurrent subprocesses (default: 20).")
    pr.add_argument("--cmd", action="append", default=None,
                    help=("Override the launch command. Each --cmd appends one argv "
                          "element. Use '{config}' as a placeholder for --config. "
                          "Default: ['modal', 'run', '{config}']."))
    pr.add_argument("--retry-aborted", action="store_true",
                    help="On resume, treat in_flight/aborted tokens as available again.")
    pr.add_argument("--shutdown-grace", type=float, default=60.0,
                    help="Seconds to wait for in-flight workers on Ctrl+C (default: 60).")
    pr.add_argument("-v", "--verbose", action="store_true")

    ps = sub.add_parser("status", help="Print current counts from a state file.")
    ps.add_argument("--state", default=Path("state.json"), type=Path)
    return p


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def _run_async(args: argparse.Namespace) -> int:
    try:
        tokens = load_tokens(args.tokens)
    except FileNotFoundError:
        print(f"tokens file not found: {args.tokens}", file=sys.stderr)
        return 2
    except MalformedTokenLine as e:
        print(f"malformed tokens file: {e}", file=sys.stderr)
        return 2

    if not tokens:
        print("token file is empty; nothing to do", file=sys.stderr)
        return 2

    store = StateStore(args.state, retry_aborted=args.retry_aborted)
    store.ensure_available([t.token_id for t in tokens])

    cmd_template = args.cmd if args.cmd else DEFAULT_CMD_TEMPLATE

    scheduler = Scheduler(
        store=store,
        tokens=tokens,
        config=SchedulerConfig(
            config_path=args.config,
            cmd_template=cmd_template,
            log_dir=args.logs,
            max_parallel=args.max_parallel,
            shutdown_grace_seconds=args.shutdown_grace,
        ),
    )

    # Cross-platform Ctrl+C: install a signal handler that asks the scheduler
    # to wind down. asyncio.run on Windows would otherwise convert SIGINT into
    # CancelledError, skipping the graceful-shutdown grace period.
    loop = asyncio.get_running_loop()
    shutdown_announced = False

    def _on_sigint() -> None:
        nonlocal shutdown_announced
        if not shutdown_announced:
            print(
                "Ctrl+C received; finishing in-flight work then exiting...",
                file=sys.stderr,
            )
            shutdown_announced = True
            scheduler.request_shutdown()

    previous_handler = None
    used_loop_handler = False
    if sys.platform != "win32":
        try:
            loop.add_signal_handler(signal.SIGINT, _on_sigint)
            used_loop_handler = True
        except NotImplementedError:
            previous_handler = signal.signal(
                signal.SIGINT,
                lambda s, f: loop.call_soon_threadsafe(_on_sigint),
            )
    else:
        previous_handler = signal.signal(
            signal.SIGINT,
            lambda s, f: loop.call_soon_threadsafe(_on_sigint),
        )

    try:
        await scheduler.run()
    finally:
        if used_loop_handler:
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except (NotImplementedError, RuntimeError):
                pass
        elif previous_handler is not None:
            try:
                signal.signal(signal.SIGINT, previous_handler)
            except (ValueError, TypeError):
                pass

    counts = store.counts()
    print("Final counts:")
    for status, n in counts.items():
        if n:
            print(f"  {status}: {n}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "run":
        _setup_logging(args.verbose)
        try:
            return asyncio.run(_run_async(args))
        except KeyboardInterrupt:
            print("interrupted", file=sys.stderr)
            return 130
    elif args.subcommand == "status":
        if not args.state.exists():
            print(f"state file not found: {args.state}", file=sys.stderr)
            return 2
        store = StateStore(args.state)
        counts = store.counts()
        total = sum(counts.values())
        print(f"State file: {args.state}")
        print(f"Total tokens: {total}")
        for status, n in counts.items():
            print(f"  {status}: {n}")
        return 0
    else:
        parser.print_help()
        return 2
