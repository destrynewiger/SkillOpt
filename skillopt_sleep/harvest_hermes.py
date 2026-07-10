"""SkillOpt-Sleep Hermes session harvesting.

Hermes is a single global assistant (cron jobs, phone line, iMessage/Telegram
channels) rather than a per-repo coding agent. Each session is one JSON file at
``~/.hermes/sessions/session_*.json`` with the shape::

    {
      "session_id": "...",
      "model": "...",
      "platform": "...",
      "session_start": "<iso>",
      "last_updated": "<iso>",
      "system_prompt": "...",          # skipped (developer instructions)
      "tools": [...],
      "message_count": N,
      "messages": [{"role": "user"|"assistant"|"system"|"tool", "content": ...}]
    }

We normalize into ``SessionDigest`` without copying the system prompt, tool
arguments, or raw tool outputs. Because Hermes is not project-scoped, every
digest is tagged with the invoked project so it flows through the same
consolidation gate as the other sources.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional

from skillopt_sleep.harvest import (
    _detect_feedback,
    _is_meta_prompt,
    _text_from_content,
)
from skillopt_sleep.staging import _SECRET_PATTERNS
from skillopt_sleep.types import SessionDigest

# Hermes injects a large delivery preamble on scheduled runs, e.g.
# "[IMPORTANT: You are running as a scheduled cron job. DELIVERY: ... [SILENT] ...]".
# It is machinery, not user intent. The block contains nested brackets, so a
# non-greedy regex mis-stops at the first inner "]"; walk bracket depth instead
# and drop the whole leading balanced group when it opens the cron preamble.
_CRON_PREAMBLE_HEAD = re.compile(r"^\s*\[IMPORTANT\b")


def _strip_leading_bracket_block(text: str) -> str:
    s = text.lstrip()
    if not _CRON_PREAMBLE_HEAD.match(s):
        return text.strip()
    depth = 0
    for i, ch in enumerate(s):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return s[i + 1:].strip()
    # Unbalanced (truncated preamble): nothing usable remains.
    return ""


def _sanitize_text(text: str) -> str:
    stripped = _strip_leading_bracket_block(text or "").replace("\x00", "").strip()
    if not stripped or _is_meta_prompt(stripped):
        return ""
    for pattern, replacement in _SECRET_PATTERNS:
        stripped = pattern.sub(replacement, stripped)
    return stripped


def _sanitize_tool_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]+", "_", name)[:80]


def _tool_names(msg: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    calls = msg.get("tool_calls")
    if isinstance(calls, list):
        for call in calls:
            if not isinstance(call, dict):
                continue
            fn = call.get("function") if isinstance(call.get("function"), dict) else call
            name = fn.get("name") if isinstance(fn, dict) else None
            if isinstance(name, str) and name:
                out.append(_sanitize_tool_name(name))
    name = msg.get("name")
    if msg.get("role") == "tool" and isinstance(name, str) and name:
        out.append(_sanitize_tool_name(name))
    return out


def _dedup(xs: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _is_cron_session(data: Dict[str, Any]) -> bool:
    if data.get("platform") == "cron":
        return True
    sid = str(data.get("session_id") or "")
    return sid.startswith("cron")


def digest_hermes_session(
    path: str, project: str = "", include_cron: bool = False
) -> Optional[SessionDigest]:
    """Build a ``SessionDigest`` from one Hermes session JSON file.

    Scheduled ``cron`` sessions are skipped by default: they are automated
    machinery (94% of the corpus) wrapped in a fixed delivery preamble, and
    mining them would swamp the real human-channel tasks. Set
    ``include_cron=True`` (or ``HERMES_INCLUDE_CRON=1``) to keep them.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not include_cron and _is_cron_session(data):
        return None

    session_id = str(data.get("session_id") or os.path.splitext(os.path.basename(path))[0])
    started = str(data.get("session_start") or "")
    ended = str(data.get("last_updated") or started)

    user_prompts: List[str] = []
    assistant_finals: List[str] = []
    tools: List[str] = []
    feedback: List[str] = []
    n_user = 0
    n_asst = 0

    for msg in data.get("messages", []):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        tools.extend(_tool_names(msg))
        if role not in {"user", "assistant"}:
            continue
        sanitized = _sanitize_text(_text_from_content(msg.get("content")))
        if not sanitized:
            continue
        if role == "user":
            n_user += 1
            user_prompts.append(sanitized)
            feedback.extend(_detect_feedback(sanitized))
        else:
            n_asst += 1
            assistant_finals.append(sanitized)

    # Fall back to the declared tool list when messages carry no inline calls.
    if not tools and isinstance(data.get("tools"), list):
        for t in data["tools"]:
            if isinstance(t, str) and t:
                tools.append(_sanitize_tool_name(t))
            elif isinstance(t, dict):
                name = t.get("name") or (t.get("function") or {}).get("name")
                if isinstance(name, str) and name:
                    tools.append(_sanitize_tool_name(name))

    if n_user == 0 and n_asst == 0:
        return None

    return SessionDigest(
        session_id=session_id,
        project=project,  # Hermes is global; tagged with the invoked project
        started_at=started,
        ended_at=ended,
        user_prompts=user_prompts,
        assistant_finals=assistant_finals[-5:],
        tools_used=_dedup(tools),
        files_touched=[],
        feedback_signals=feedback,
        n_user_turns=n_user,
        n_assistant_turns=n_asst,
        raw_path=path,
    )


def harvest_hermes(
    sessions_dir: str,
    *,
    scope: Any = "all",
    invoked_project: str = "",
    since_iso: Optional[str] = None,
    limit: int = 0,
    include_cron: Optional[bool] = None,
) -> List[SessionDigest]:
    """Walk ``~/.hermes/sessions`` and return matching digests.

    Hermes has no per-repo separation, so ``scope`` is not used to filter by
    path; digests are tagged with ``invoked_project`` so downstream stages
    target the right learned skill/memory. Scheduled ``cron`` sessions are
    excluded unless ``include_cron`` (or ``HERMES_INCLUDE_CRON=1``) is set.
    """
    digests: List[SessionDigest] = []
    if not os.path.isdir(sessions_dir):
        return digests

    if include_cron is None:
        include_cron = os.environ.get("HERMES_INCLUDE_CRON", "").strip() in {"1", "true", "yes"}

    paths = [
        os.path.join(sessions_dir, fn)
        for fn in os.listdir(sessions_dir)
        if fn.startswith("session_") and fn.endswith(".json")
    ]
    paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)

    for path in paths:
        digest = digest_hermes_session(path, project=invoked_project, include_cron=include_cron)
        if digest is None:
            continue
        if since_iso and digest.ended_at and digest.ended_at < since_iso:
            continue
        digests.append(digest)
        if limit and len(digests) >= limit:
            break
    return digests
