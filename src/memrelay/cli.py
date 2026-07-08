"""memrelay command-line interface (SPEC §7).

E0 wires the command surface and a working ``config`` command; the daemon, MCP
server, and memory operations land in later epics, so those subcommands are
intentional stubs that exit cleanly with a clear message.
"""

from __future__ import annotations

import json

import click

from memrelay import __version__
from memrelay.config import Config, load_config

_NOT_YET = "not implemented yet in this E0 foundations build"


def _todo(command: str, epic: str) -> None:
    """Emit a uniform placeholder message for a not-yet-built subcommand."""
    click.echo(f"memrelay {command}: {_NOT_YET} (planned for {epic}).")


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="memrelay")
def main() -> None:
    """Portable, graph-based persistent memory for AI coding agents."""


@main.command()
def init() -> None:
    """First-time setup: create dirs, generate config, register MCP server."""
    _todo("init", "the daemon/registration epic")


@main.command()
def start() -> None:
    """Start the observation daemon (background process)."""
    _todo("start", "the daemon epic")


@main.command()
def stop() -> None:
    """Stop the daemon gracefully."""
    _todo("stop", "the daemon epic")


@main.command()
def status() -> None:
    """Show daemon health: sessions observed, episodes ingested, spool depth."""
    _todo("status", "the daemon epic")


@main.command()
@click.option("--repo", metavar="OWNER/NAME", help="Delete all episodes from a specific repo.")
@click.option("--namespace", metavar="NAME", help="Delete an entire namespace graph.")
def forget(repo: str | None, namespace: str | None) -> None:
    """Delete memories for a repo or namespace."""
    if not repo and not namespace:
        raise click.UsageError("provide --repo OWNER/NAME or --namespace NAME")
    _todo("forget", "the retrieval/lifecycle epic")


@main.command()
def seed() -> None:
    """Ingest git history as episodes (bootstrap memory for existing repos)."""
    _todo("seed", "the retrieval epic")


@main.command(name="config")
@click.option(
    "--path",
    "config_path",
    type=click.Path(dir_okay=False),
    default=None,
    help="Load a specific config file instead of the default search path.",
)
def config_cmd(config_path: str | None) -> None:
    """Show the current (resolved) configuration as JSON."""
    cfg: Config = load_config(path=config_path)
    resolved = cfg.to_dict()
    # Surface fully-expanded paths alongside the raw template values.
    resolved["home_path"] = str(cfg.home_path)
    resolved["graph"]["resolved_path"] = str(cfg.graph_path)
    click.echo(json.dumps(resolved, indent=2))


@main.command()
def mcp() -> None:
    """Start the MCP stdio server (invoked by the agent, not by users)."""
    _todo("mcp", "the MCP server epic")


if __name__ == "__main__":
    main()
