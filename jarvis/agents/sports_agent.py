"""Sports agent — FIFA World Cup 2026 fixtures, scores, and standings via ESPN API (free, no key)."""

import json
from datetime import datetime, timezone
from typing import Any

import requests

from jarvis.agents.base_agent import BaseAgent

_ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world"
_HEADERS = {"User-Agent": "KamranAI/1.0"}


def _parse_date(iso_str: str) -> str:
    """Convert ISO date string to a human-readable format."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        local = dt.astimezone()
        return local.strftime("%d %b %Y %H:%M")
    except Exception:
        return iso_str


class SportsAgent(BaseAgent):
    name = "sports"
    description = "Fetches FIFA World Cup 2026 fixtures, live scores, and group standings via the free ESPN API."

    def __init__(self, agent_cfg: dict, global_cfg: dict) -> None:
        super().__init__(agent_cfg, global_cfg)

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "get_worldcup_fixtures",
                "description": (
                    "Get upcoming FIFA World Cup 2026 match fixtures. "
                    "Optionally filter by team name."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "days_ahead": {
                            "type": "integer",
                            "description": "Number of days ahead to look for fixtures. Default 7.",
                        },
                        "team": {
                            "type": "string",
                            "description": "Optional team name to filter fixtures, e.g. 'Brazil', 'England'.",
                        },
                    },
                    "required": [],
                },
            },
            {
                "name": "get_worldcup_scores",
                "description": "Get live or today's FIFA World Cup 2026 match scores.",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "get_worldcup_standings",
                "description": "Get FIFA World Cup 2026 group standings.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "group": {
                            "type": "string",
                            "description": "Optional group name to filter, e.g. 'Group A'. Returns all groups if omitted.",
                        },
                    },
                    "required": [],
                },
            },
        ]

    def handle(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "get_worldcup_fixtures":
            return self._get_fixtures(
                days_ahead=int(tool_input.get("days_ahead", 7)),
                team=tool_input.get("team"),
            )
        if tool_name == "get_worldcup_scores":
            return self._get_scores()
        if tool_name == "get_worldcup_standings":
            return self._get_standings(group=tool_input.get("group"))
        return f"Unknown tool: {tool_name}"

    # ── Private helpers ──────────────────────────────────────────────────────

    def _fetch_scoreboard(self) -> list[dict]:
        """Fetch raw event list from ESPN scoreboard endpoint."""
        try:
            r = requests.get(
                f"{_ESPN_BASE}/scoreboard",
                params={"limit": 50},
                headers=_HEADERS,
                timeout=8,
            )
            r.raise_for_status()
            return r.json().get("events", [])
        except Exception as exc:
            return []

    def _parse_event(self, event: dict) -> dict | None:
        """Parse a single ESPN event into a clean fixture dict."""
        try:
            comp = event.get("competitions", [{}])[0]
            competitors = comp.get("competitors", [])
            if len(competitors) < 2:
                return None

            home = competitors[0]
            away = competitors[1]

            status_obj = comp.get("status", {})
            status_type = status_obj.get("type", {})
            state = status_type.get("state", "pre")  # pre / in / post
            clock = status_obj.get("displayClock", "")

            home_score = home.get("score", "")
            away_score = away.get("score", "")
            score = None
            if state in ("in", "post") and home_score != "" and away_score != "":
                score = f"{home_score}-{away_score}"

            venue_info = comp.get("venue", {})
            venue = venue_info.get("fullName", "") or venue_info.get("address", {}).get("city", "")

            round_info = event.get("season", {}).get("slug", "") or event.get("name", "")

            return {
                "home": home.get("team", {}).get("displayName", ""),
                "away": away.get("team", {}).get("displayName", ""),
                "date": _parse_date(event.get("date", "")),
                "venue": venue,
                "round": round_info,
                "status": state,
                "clock": clock,
                "score": score,
            }
        except Exception:
            return None

    def _get_fixtures(self, days_ahead: int, team: str | None) -> str:
        events = self._fetch_scoreboard()
        if not events:
            return json.dumps({"error": "Could not fetch fixtures from ESPN.", "fixtures": []})

        fixtures = []
        for event in events:
            parsed = self._parse_event(event)
            if not parsed:
                continue
            # Filter to upcoming (pre) matches
            if parsed["status"] != "pre":
                continue
            # Optional team filter
            if team:
                t = team.lower()
                if t not in parsed["home"].lower() and t not in parsed["away"].lower():
                    continue
            fixtures.append(parsed)

        return json.dumps({"fixtures": fixtures, "count": len(fixtures)})

    def _get_scores(self) -> str:
        events = self._fetch_scoreboard()
        if not events:
            return json.dumps({"error": "Could not fetch scores from ESPN.", "matches": []})

        matches = []
        for event in events:
            parsed = self._parse_event(event)
            if not parsed:
                continue
            # Live or completed matches
            if parsed["status"] not in ("in", "post"):
                continue
            matches.append(parsed)

        return json.dumps({"matches": matches, "count": len(matches)})

    def _get_standings(self, group: str | None) -> str:
        try:
            r = requests.get(
                f"{_ESPN_BASE}/standings",
                headers=_HEADERS,
                timeout=8,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            return json.dumps({"error": f"ESPN standings API error: {exc}", "standings": []})

        raw_standings = data.get("standings", [])
        if not raw_standings:
            # Try alternate key structure
            raw_standings = data.get("children", [])

        result = []
        for grp in raw_standings:
            grp_name = grp.get("name", grp.get("abbreviation", ""))
            if group and group.lower() not in grp_name.lower():
                continue

            entries = []
            for entry in grp.get("entries", []):
                team_info = entry.get("team", {})
                stats_list = entry.get("stats", [])
                stats: dict[str, Any] = {s.get("name", ""): s.get("displayValue", s.get("value", "")) for s in stats_list}
                entries.append({
                    "team": team_info.get("displayName", team_info.get("name", "")),
                    "played": stats.get("gamesPlayed", stats.get("GP", "")),
                    "wins": stats.get("wins", stats.get("W", "")),
                    "draws": stats.get("ties", stats.get("D", "")),
                    "losses": stats.get("losses", stats.get("L", "")),
                    "gf": stats.get("pointsFor", stats.get("GF", "")),
                    "ga": stats.get("pointsAgainst", stats.get("GA", "")),
                    "gd": stats.get("pointDifferential", stats.get("GD", "")),
                    "points": stats.get("points", stats.get("PTS", "")),
                })
            result.append({"group": grp_name, "entries": entries})

        return json.dumps({"standings": result, "count": len(result)})
