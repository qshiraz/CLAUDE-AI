#!/usr/bin/env python3
"""Jarvis — main CLI entry point."""

import asyncio
import logging
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.spinner import Spinner
from rich.live import Live

# Make sure the project root is on the path so we can import jarvis.*
sys.path.insert(0, str(Path(__file__).parent))

from jarvis.core.config import load_config
from jarvis.core.agent_registry import AgentRegistry
from jarvis.core.orchestrator import JarvisOrchestrator

console = Console()

BANNER = """
     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗
     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝
     ██║███████║██████╔╝██║   ██║██║███████╗
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝
"""

HELP_TEXT = """\
**Commands:**
- `weather`            → Current weather & forecast
- `news`               → Local & international headlines
- `home`               → Smart home device status & control
- `emails`             → Check inbox summary
- `flights`            → Nearby flights & airport departures
- `traffic`            → Live traffic & route times
- `morning briefing`   → Full daily briefing (all agents)
- `reset`              → Clear conversation history
- `agents`             → List loaded agents
- `help`               → Show this help
- `exit` / `quit`      → Exit Jarvis

Or just type naturally — Jarvis understands plain language.
"""


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def build_jarvis(verbose: bool = False) -> tuple[JarvisOrchestrator, AgentRegistry]:
    setup_logging(verbose)
    root = Path(__file__).parent
    cfg = load_config(root)

    registry = AgentRegistry()
    registry.load_builtin_agents(cfg)

    plugins_cfg = cfg.get("plugins", {})
    if plugins_cfg.get("enabled", True) and plugins_cfg.get("autoload", True):
        plugin_dir = root / plugins_cfg.get("directory", "jarvis/plugins")
        registry.load_plugins(plugin_dir)

    orchestrator = JarvisOrchestrator(cfg, registry)
    return orchestrator, registry


async def run_chat(orchestrator: JarvisOrchestrator, registry: AgentRegistry) -> None:
    user_name = orchestrator.user_name

    # Banner
    console.print(BANNER, style="bold cyan", highlight=False)
    console.print(
        Panel(
            f"[bold green]Jarvis online.[/bold green] "
            f"[dim]{len(registry.agents)} agents loaded: "
            f"{', '.join(registry.agent_names())}[/dim]",
            border_style="green",
        )
    )
    console.print('[dim]Type "help" for commands or just ask naturally. "exit" to quit.[/dim]\n')

    while True:
        try:
            user_input = Prompt.ask(f"[bold cyan]{user_name}[/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        if not user_input:
            continue

        cmd = user_input.lower()

        if cmd in ("exit", "quit", "bye"):
            console.print("[dim]Signing off. Goodbye, Boss.[/dim]")
            break

        if cmd == "help":
            console.print(Markdown(HELP_TEXT))
            continue

        if cmd == "reset":
            orchestrator.reset()
            console.print("[dim]Conversation history cleared.[/dim]")
            continue

        if cmd == "agents":
            names = registry.agent_names()
            console.print(Panel("\n".join(f"• {n}" for n in names), title="Loaded Agents"))
            continue

        # Stream Jarvis's response
        console.print(Rule(style="dim"))
        console.print("[bold green]Jarvis:[/bold green] ", end="")
        try:
            response_parts: list[str] = []
            async for chunk in orchestrator.chat(user_input):
                console.print(chunk, end="", markup=False, highlight=False)
                response_parts.append(chunk)
            console.print()  # newline after stream
        except anthropic_import_error():
            console.print("[red]Anthropic API key not set. Set ANTHROPIC_API_KEY env var.[/red]")
        except Exception as exc:
            console.print(f"\n[red]Error: {exc}[/red]")
            logging.getLogger(__name__).exception("Chat error")
        console.print()


def anthropic_import_error():
    """Return the AuthenticationError class if available, else a dummy."""
    try:
        from anthropic import AuthenticationError
        return AuthenticationError
    except ImportError:
        return type("_Never", (Exception,), {})


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Jarvis AI Assistant")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--once",
        "-q",
        metavar="QUERY",
        help="Run a single query non-interactively and exit",
    )
    args = parser.parse_args()

    if not os.getenv("ANTHROPIC_API_KEY"):
        console.print(
            Panel(
                "[yellow]ANTHROPIC_API_KEY is not set.[/yellow]\n"
                "Export it: [bold]export ANTHROPIC_API_KEY=sk-ant-...[/bold]",
                border_style="yellow",
                title="Setup Required",
            )
        )
        sys.exit(1)

    orchestrator, registry = build_jarvis(verbose=args.verbose)

    if args.once:
        async def _once() -> None:
            async for chunk in orchestrator.chat(args.once):
                print(chunk, end="", flush=True)
            print()

        asyncio.run(_once())
    else:
        asyncio.run(run_chat(orchestrator, registry))


if __name__ == "__main__":
    main()
