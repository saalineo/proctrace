from __future__ import annotations

import argparse
import sys

from proctrace.cli.commands import cmd_dump, cmd_guide, cmd_run, cmd_watch


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser for the proctrace CLI."""
    parser = argparse.ArgumentParser(
        prog="proctrace",
        description="Non-invasive process introspection tool",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    run_p = subparsers.add_parser(
        "run", help="Run a command and report resource usage"
    )
    run_p.add_argument("--memory", action="store_true", default=True)
    run_p.add_argument("--fds", action="store_true", default=False)
    run_p.add_argument(
        "--json", action="store_true", default=False, help="Output as JSON to stdout"
    )
    run_p.add_argument("command", nargs=argparse.REMAINDER)

    dump_p = subparsers.add_parser(
        "dump", help="Send a signal to trigger a stack dump"
    )
    dump_p.add_argument("--pid", type=int, required=True)
    dump_p.add_argument(
        "--signal", default="SIGUSR1", choices=["SIGUSR1", "SIGUSR2"]
    )

    subparsers.add_parser("guide", help="Print the developer guide")

    watch_p = subparsers.add_parser("watch", help="Live-poll a process's memory")
    watch_p.add_argument("--pid", type=int, required=True)
    watch_p.add_argument(
        "--interval", type=float, default=1.0, help="Poll interval in seconds"
    )
    watch_p.add_argument(
        "--duration",
        type=float,
        default=10.0,
        help="Total watch duration in seconds",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.subcommand == "run" and args.command and args.command[0] == "--":
        args.command = args.command[1:]

    dispatch = {
        "run": cmd_run,
        "dump": cmd_dump,
        "watch": cmd_watch,
        "guide": cmd_guide,
    }
    sys.exit(dispatch[args.subcommand](args))


if __name__ == "__main__":
    main()


__all__ = ["build_parser", "main"]
