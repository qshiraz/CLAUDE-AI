"""Email agent — IMAP-based email reading and summarisation."""

import email
import imaplib
import json
import re
from email.header import decode_header
from typing import Any

from jarvis.agents.base_agent import BaseAgent


def _decode_str(val: str | bytes | None) -> str:
    if val is None:
        return ""
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return val


def _decode_header_val(raw: str) -> str:
    parts = decode_header(raw)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class EmailAgent(BaseAgent):
    name = "email"
    description = "Reads and summarises emails from Gmail or any IMAP server."

    def __init__(self, agent_cfg: dict, global_cfg: dict) -> None:
        super().__init__(agent_cfg, global_cfg)
        self._address: str = agent_cfg.get("address", "")
        self._password: str = agent_cfg.get("password", "")
        self._imap_host: str = agent_cfg.get("imap_host", "imap.gmail.com")
        self._imap_port: int = int(agent_cfg.get("imap_port", 993))
        self._max: int = int(agent_cfg.get("max_emails", 10))

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "check_inbox",
                "description": (
                    "Checks the inbox for unread emails and returns their subject, "
                    "sender, date, and a short body excerpt."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "count": {
                            "type": "integer",
                            "description": "How many emails to fetch (default 5, max 10).",
                        },
                        "unread_only": {
                            "type": "boolean",
                            "description": "If true, only fetch unread emails.",
                        },
                    },
                    "required": [],
                },
            },
            {
                "name": "search_emails",
                "description": "Searches inbox for emails matching a keyword in subject or sender.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search keyword."},
                        "count": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            },
        ]

    def handle(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "check_inbox":
            return self._check_inbox(
                count=min(int(tool_input.get("count", 5)), self._max),
                unread_only=bool(tool_input.get("unread_only", True)),
            )
        if tool_name == "search_emails":
            return self._search(
                query=tool_input["query"],
                count=min(int(tool_input.get("count", 5)), self._max),
            )
        return f"Unknown tool: {tool_name}"

    # ── IMAP helpers ────────────────────────────────────────────────────────

    def _connect(self) -> imaplib.IMAP4_SSL:
        conn = imaplib.IMAP4_SSL(self._imap_host, self._imap_port)
        conn.login(self._address, self._password)
        return conn

    def _fetch_emails(self, conn: imaplib.IMAP4_SSL, ids: list[bytes], count: int) -> list[dict]:
        results = []
        for eid in ids[-count:]:
            _, data = conn.fetch(eid, "(RFC822)")
            raw = data[0][1] if isinstance(data[0], tuple) else b""
            msg = email.message_from_bytes(raw)

            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    ct = part.get_content_type()
                    if ct == "text/plain":
                        payload = part.get_payload(decode=True)
                        body = _decode_str(payload)[:500]
                        break
                    if ct == "text/html" and not body:
                        payload = part.get_payload(decode=True)
                        body = _strip_html(_decode_str(payload))[:500]
            else:
                payload = msg.get_payload(decode=True)
                body = _decode_str(payload)[:500]

            results.append(
                {
                    "subject": _decode_header_val(msg.get("Subject", "(no subject)")),
                    "from": _decode_header_val(msg.get("From", "")),
                    "date": msg.get("Date", ""),
                    "snippet": body.strip()[:300],
                }
            )
        return results

    def _check_inbox(self, count: int, unread_only: bool) -> str:
        if not self._address or not self._password or self._password.startswith("YOUR_"):
            return self._demo_emails(count)
        try:
            conn = self._connect()
            conn.select("INBOX")
            criteria = "(UNSEEN)" if unread_only else "ALL"
            _, ids = conn.search(None, criteria)
            email_ids = ids[0].split() if ids[0] else []
            emails = self._fetch_emails(conn, email_ids, count)
            conn.logout()
            return json.dumps({"count": len(email_ids), "emails": emails})
        except Exception as exc:
            return f"Email error: {exc}"

    def _search(self, query: str, count: int) -> str:
        if not self._address or not self._password or self._password.startswith("YOUR_"):
            return self._demo_emails(count, query)
        try:
            conn = self._connect()
            conn.select("INBOX")
            _, ids = conn.search(None, f'(OR SUBJECT "{query}" FROM "{query}")')
            email_ids = ids[0].split() if ids[0] else []
            emails = self._fetch_emails(conn, email_ids, count)
            conn.logout()
            return json.dumps({"query": query, "count": len(email_ids), "emails": emails})
        except Exception as exc:
            return f"Email search error: {exc}"

    def _demo_emails(self, count: int, query: str = "") -> str:
        emails = [
            {
                "subject": f"[Demo] Email #{i+1}" + (f" — {query}" if query else ""),
                "from": "demo@example.com",
                "date": "Thu, 5 Jun 2026 10:00:00 +0500",
                "snippet": "Configure email credentials in config.local.yaml to see real emails.",
            }
            for i in range(count)
        ]
        return json.dumps({"note": "Demo mode", "count": count, "emails": emails})
