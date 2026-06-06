#!/usr/bin/env python3
"""Jarvis — main CLI entry point."""

import asyncio
import logging
import os
import re
import sys
import threading
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule

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
- `voice on/off`       → Toggle voice output
- `reset`              → Clear conversation history
- `agents`             → List loaded agents
- `help`               → Show this help
- `exit` / `quit`      → Exit Jarvis

Or just type naturally — Jarvis understands plain language.
"""


# ── Voice engine ─────────────────────────────────────────────────────────────

class VoiceEngine:
    def __init__(self) -> None:
        self._engine = None
        self._enabled = False
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        try:
            import pyttsx3
            engine = pyttsx3.init()
            # Pick a clear, slightly slower rate
            engine.setProperty("rate", 165)
            engine.setProperty("volume", 1.0)
            # On Windows prefer a natural-sounding voice
            voices = engine.getProperty("voices")
            for v in voices:
                if "zira" in v.name.lower() or "david" in v.name.lower():
                    engine.setProperty("voice", v.id)
                    break
            self._engine = engine
            self._enabled = True
        except Exception:
            self._enabled = False

    @property
    def available(self) -> bool:
        return self._engine is not None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def toggle(self) -> bool:
        if not self.available:
            return False
        self._enabled = not self._enabled
        return self._enabled

    def speak(self, text: str) -> None:
        if not self._enabled or not self._engine:
            return
        # Strip markdown symbols so they aren't read aloud
        clean = re.sub(r"[*_`#>\-]+", " ", text)
        clean = re.sub(r"\s+", " ", clean).strip()
        if not clean:
            return
        with self._lock:
            try:
                self._engine.say(clean)
                self._engine.runAndWait()
            except Exception:
                pass


# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


# ── Build ─────────────────────────────────────────────────────────────────────

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


# ── Main chat loop ────────────────────────────────────────────────────────────

async def run_chat(
    orchestrator: JarvisOrchestrator,
    registry: AgentRegistry,
    voice: VoiceEngine,
) -> None:
    user_name = orchestrator.user_name

    console.print(BANNER, style="bold cyan", highlight=False)

    voice_status = "[green]ON[/green]" if voice.enabled else "[dim]OFF[/dim]"
    if not voice.available:
        voice_status = "[dim]unavailable — pip install pyttsx3[/dim]"

    console.print(
        Panel(
            f"[bold green]Jarvis online.[/bold green]  "
            f"Voice: {voice_status}\n"
            f"[dim]{len(registry.agents)} agents loaded: "
            f"{', '.join(registry.agent_names())}[/dim]",
            border_style="green",
        )
    )
    console.print('[dim]Type "help" for commands or just talk naturally. "exit" to quit.[/dim]\n')

    while True:
        try:
            user_input = Prompt.ask(f"[bold cyan]{user_name}[/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        if not user_input:
            continue

        cmd = user_input.lower().strip()

        if cmd in ("exit", "quit", "bye"):
            farewell = "Signing off. Goodbye, Boss."
            console.print(f"[dim]{farewell}[/dim]")
            voice.speak(farewell)
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

        if cmd in ("voice on", "voice off"):
            if not voice.available:
                console.print("[yellow]Voice not available. Run: pip install pyttsx3[/yellow]")
            else:
                now_on = voice.toggle()
                state = "ON" if now_on else "OFF"
                console.print(f"[dim]Voice output {state}.[/dim]")
            continue

        # ── Stream Jarvis's response ──────────────────────────────────────
        console.print(Rule(style="dim"))
        console.print("[bold green]Jarvis:[/bold green] ", end="")
        try:
            response_parts: list[str] = []
            async for chunk in orchestrator.chat(user_input):
                console.print(chunk, end="", markup=False, highlight=False)
                response_parts.append(chunk)
            console.print()

            full_response = "".join(response_parts)
            if full_response:
                # Speak in a background thread so the terminal stays responsive
                threading.Thread(
                    target=voice.speak, args=(full_response,), daemon=True
                ).start()

        except Exception as exc:
            console.print(f"\n[red]Error: {exc}[/red]")
            logging.getLogger(__name__).exception("Chat error")

        console.print()


# ── Entry point ───────────────────────────────────────────────────────────────

def anthropic_auth_error():
    try:
        from anthropic import AuthenticationError
        return AuthenticationError
    except ImportError:
        return type("_Never", (Exception,), {})


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Jarvis AI Assistant")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    parser.add_argument("--no-voice", action="store_true", help="Disable voice output")
    parser.add_argument(
        "--once", "-q", metavar="QUERY",
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

    voice = VoiceEngine()
    if args.no_voice:
        voice._enabled = False

    orchestrator, registry = build_jarvis(verbose=args.verbose)

    if args.once:
        async def _once() -> None:
            async for chunk in orchestrator.chat(args.once):
                print(chunk, end="", flush=True)
            print()
        asyncio.run(_once())
    else:
        asyncio.run(run_chat(orchestrator, registry, voice))


if __name__ == "__main__":
    main()
