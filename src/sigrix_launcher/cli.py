"""``sigrix-launcher run OWNER/LISTING-ID`` — what an MCP client's configuration starts."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

from sigrix_launcher import __version__
from sigrix_launcher.launcher import LaunchError, prepare, say, settings_from_environment, start_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sigrix-launcher",
        description="Start a Sigrix-delivered MCP server you bought, on this process's stdio.",
    )
    parser.add_argument("--version", action="version", version=f"sigrix-launcher {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "run",
        help="check the purchase, fetch and install the package, and start the server",
        description=(
            "Check SIGRIX_TOKEN's purchase of the listing, fetch and verify its package, install it into an "
            "environment of its own and start it. Arguments after the identifier go to the server."
        ),
    )
    run.add_argument("agent_id", metavar="OWNER/LISTING-ID", help="the identifier on the listing's page")
    run.add_argument("server_args", nargs=argparse.REMAINDER, help="passed to the seller's server as they are")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    server_args = list(args.server_args)
    if server_args[:1] == ["--"]:
        server_args = server_args[1:]
    try:
        settings = settings_from_environment(args.agent_id)
        installed = prepare(settings)
    except LaunchError as exc:
        say(str(exc))
        return 1
    return start_server(installed, server_args, os.environ)


__all__ = ["build_parser", "main"]
