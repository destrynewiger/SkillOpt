"""SkillOpt-Sleep VS Code GitHub Copilot Chat session harvesting.

VS Code stores each chat session as an append-only JSONL operation log under
``User/workspaceStorage/<workspace-id>/chatSessions``. This module reconstructs
that log and normalizes only user-entered prompts, visible assistant Markdown,
and tool names into ``SessionDigest`` records. It deliberately excludes
reasoning, tool inputs/outputs, rendered context, and account/model metadata.

This module performs no writes and no network calls.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import unquote, urlparse

from skillopt_sleep.harvest import (
    _detect_feedback,
    _is_agent_session,
    _is_headless_replay,
    _is_meta_prompt,
    _project_matches,
)
from skillopt_sleep.staging import _SECRET_PATTERNS
from skillopt_sleep.types import SessionDigest

_COPILOT_EXTENSION_ID = "github.copilot-chat"
_TOOL_NAME_RE = re.compile(r"[^A-Za-z0-9_.:-]+")


@dataclass(frozen=True)
class CopilotSessionFile:
    """One VS Code chat session and its workspace-derived project path."""

    path: str
    project: str


def default_vscode_workspace_storage_roots(
    *,
    platform: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    home: Optional[str] = None,
) -> List[str]:
    """Return stable, Insiders, and portable ``workspaceStorage`` candidates."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = os.path.expanduser(home or "~")

    if platform == "win32":
        appdata = env.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        user_roots = [os.path.join(appdata, "Code"), os.path.join(appdata, "Code - Insiders")]
    elif platform == "darwin":
        app_support = os.path.join(home, "Library", "Application Support")
        user_roots = [os.path.join(app_support, "Code"), os.path.join(app_support, "Code - Insiders")]
    else:
        config_home = env.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
        user_roots = [os.path.join(config_home, "Code"), os.path.join(config_home, "Code - Insiders")]

    portable = env.get("VSCODE_PORTABLE")
    if portable:
        # VS Code sets VSCODE_PORTABLE to the portable data directory itself;
        # user data lives directly below it, not under another data/ segment.
        user_roots.insert(0, os.path.join(os.path.expanduser(portable), "user-data"))
    return [os.path.join(root, "User", "workspaceStorage") for root in user_roots]


def _file_uri_to_path(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    parsed = urlparse(value)
    if parsed.scheme != "file":
        return ""
    path = unquote(parsed.path)
    if parsed.netloc and parsed.netloc not in {"", "localhost"}:
        path = "//" + parsed.netloc + path
    if re.match(r"^/[A-Za-z]:/", path):
        path = path[1:]
    return os.path.abspath(path) if path else ""


def _workspace_project(workspace_dir: str) -> str:
    """Resolve a best-effort local project path from adjacent ``workspace.json``."""
    path = os.path.join(workspace_dir, "workspace.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    folder = _file_uri_to_path(data.get("folder"))
    if folder:
        return folder
    workspace = _file_uri_to_path(data.get("workspace"))
    if workspace:
        return os.path.dirname(workspace)
    return ""


def discover_vscode_sessions(workspace_storage: str = "") -> List[CopilotSessionFile]:
    """Discover chat logs newest first and read adjacent project mappings.

    Session content remains unopened during discovery; only the neighboring
    ``workspace.json`` is read to establish project scope.
    """
    roots = [os.path.abspath(os.path.expanduser(workspace_storage))] if workspace_storage else (
        default_vscode_workspace_storage_roots()
    )
    sessions: List[CopilotSessionFile] = []
    seen = set()
    for root in roots:
        try:
            workspace_entries = list(os.scandir(root))
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
            continue
        for entry in workspace_entries:
            if not entry.is_dir(follow_symlinks=False):
                continue
            project = _workspace_project(entry.path)
            sessions_dir = os.path.join(entry.path, "chatSessions")
            try:
                chat_entries = list(os.scandir(sessions_dir))
            except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
                continue
            for chat in chat_entries:
                if not chat.is_file(follow_symlinks=False) or not chat.name.endswith(".jsonl"):
                    continue
                normalized = os.path.normcase(os.path.abspath(chat.path))
                if normalized in seen:
                    continue
                seen.add(normalized)
                sessions.append(CopilotSessionFile(path=chat.path, project=project))
    sessions.sort(key=lambda item: _safe_mtime(item.path), reverse=True)
    return sessions


def _safe_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _read_jsonl_snapshot(path: str) -> Iterable[Dict[str, Any]]:
    """Read a fixed-size snapshot so a live VS Code writer cannot extend the scan."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            remaining = size
            while remaining > 0:
                line = f.readline(remaining)
                if not line:
                    break
                remaining -= len(line)
                if not line.strip():
                    continue
                try:
                    record = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError, TypeError):
                    continue
                if isinstance(record, dict):
                    yield record
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
        return


def _resolve_parent(root: Any, path: Any) -> tuple[Any, Any] | tuple[None, None]:
    if not isinstance(path, list) or not path:
        return None, None
    current = root
    for component in path[:-1]:
        if isinstance(current, dict) and isinstance(component, str) and component in current:
            current = current[component]
        elif isinstance(current, list) and isinstance(component, int) and 0 <= component < len(current):
            current = current[component]
        else:
            return None, None
    return current, path[-1]


def _replace_at_path(root: Any, path: Any, value: Any) -> bool:
    parent, key = _resolve_parent(root, path)
    if isinstance(parent, dict) and isinstance(key, str):
        parent[key] = value
        return True
    if isinstance(parent, list) and isinstance(key, int) and 0 <= key < len(parent):
        parent[key] = value
        return True
    return False


def _delete_at_path(root: Any, path: Any) -> bool:
    parent, key = _resolve_parent(root, path)
    if isinstance(parent, dict) and isinstance(key, str) and key in parent:
        del parent[key]
        return True
    if isinstance(parent, list) and isinstance(key, int) and 0 <= key < len(parent):
        del parent[key]
        return True
    return False


def _patch_array(root: Any, path: Any, values: Any, index: Any) -> bool:
    if values is not None and not isinstance(values, list):
        return False
    parent, key = _resolve_parent(root, path)
    target: Any = None
    if isinstance(parent, dict) and isinstance(key, str):
        target = parent.get(key)
    elif isinstance(parent, list) and isinstance(key, int) and 0 <= key < len(parent):
        target = parent[key]
    if not isinstance(target, list):
        return False
    if index is None:
        if values is None:
            return False
        target.extend(values)
        return True
    if not isinstance(index, int) or index < 0 or index > len(target):
        return False
    target[index:] = values or []
    return True


def reconstruct_chat_session(path: str) -> Optional[Dict[str, Any]]:
    """Replay a VS Code JSONL operation log into its current session object."""
    state: Optional[Dict[str, Any]] = None
    for record in _read_jsonl_snapshot(path):
        kind = record.get("kind")
        if kind == 0 and isinstance(record.get("v"), dict):
            state = record["v"]
        elif state is not None and kind == 1:
            _replace_at_path(state, record.get("k"), record.get("v"))
        elif state is not None and kind == 2:
            _patch_array(state, record.get("k"), record.get("v"), record.get("i"))
        elif state is not None and kind == 3:
            _delete_at_path(state, record.get("k"))
    return state


def _extension_id(request: Dict[str, Any]) -> str:
    agent = request.get("agent")
    if not isinstance(agent, dict):
        return ""
    extension_id = agent.get("extensionId")
    if isinstance(extension_id, str):
        return extension_id.lower()
    if isinstance(extension_id, dict):
        for key in ("_lower", "value"):
            value = extension_id.get(key)
            if isinstance(value, str) and value:
                return value.lower()
    return ""


def _sanitize_text(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    sanitized = text.replace("\x00", "").strip()
    if not sanitized or _is_meta_prompt(sanitized):
        return ""
    for pattern, replacement in _SECRET_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def _assistant_text(response: Any) -> str:
    if not isinstance(response, list):
        return ""
    chunks: List[str] = []
    for part in response:
        if not isinstance(part, dict):
            continue
        # Serialized MarkdownString objects have no kind. Some VS Code builds
        # label the same payload explicitly as markdownContent.
        if part.get("kind") not in {None, "markdownContent"}:
            continue
        value = part.get("value")
        if isinstance(value, str):
            chunks.append(value)
    return _sanitize_text("".join(chunks))


def _tool_names(response: Any) -> List[str]:
    names: List[str] = []
    if not isinstance(response, list):
        return names
    for part in response:
        if not isinstance(part, dict) or part.get("kind") != "toolInvocationSerialized":
            continue
        name = part.get("toolId")
        if isinstance(name, str) and name:
            sanitized = _TOOL_NAME_RE.sub("_", name)[:80]
            if sanitized:
                names.append(sanitized)
    return names


def _dedup(values: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _iso_timestamp(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, (int, float)):
        return ""
    seconds = float(value) / 1000.0 if value > 10_000_000_000 else float(value)
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return ""


def _iso_epoch(value: Optional[str]) -> Optional[float]:
    """Parse an ISO-8601 timestamp for chronological comparisons."""
    if not value:
        return None
    try:
        normalized = value[:-1] + "+00:00" if value[-1:] in {"Z", "z"} else value
        parsed = datetime.fromisoformat(normalized)
        # Sleep state checkpoints are local-time strings without an offset.
        # ``timestamp()`` intentionally interprets those in the host timezone,
        # matching the existing Cursor harvester and the producer in state.py.
        return parsed.timestamp()
    except (OverflowError, OSError, TypeError, ValueError):
        return None


def digest_copilot_session(path: str, project: str = "") -> Optional[SessionDigest]:
    """Build a privacy-bounded digest from one reconstructed VS Code session."""
    session = reconstruct_chat_session(path)
    if not isinstance(session, dict):
        return None

    session_id = session.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        session_id = os.path.splitext(os.path.basename(path))[0]
    started = _iso_timestamp(session.get("creationDate"))
    ended = started
    prompts: List[str] = []
    finals: List[str] = []
    tools: List[str] = []
    feedback: List[str] = []

    requests = session.get("requests")
    if not isinstance(requests, list):
        requests = []
    for request in requests:
        if (
            not isinstance(request, dict)
            or _extension_id(request) != _COPILOT_EXTENSION_ID
            or request.get("isSystemInitiated") is True
        ):
            continue
        message = request.get("message")
        prompt = _sanitize_text(message.get("text") if isinstance(message, dict) else "")
        if not prompt:
            continue
        prompts.append(prompt)
        feedback.extend(_detect_feedback(prompt))

        timestamp = _iso_timestamp(request.get("timestamp"))
        if timestamp:
            if not started:
                started = timestamp
            ended = timestamp
        model_state = request.get("modelState")
        completed = _iso_timestamp(model_state.get("completedAt") if isinstance(model_state, dict) else None)
        if completed:
            ended = completed

        response = request.get("response")
        final = _assistant_text(response)
        if final:
            finals.append(final)
        tools.extend(_tool_names(response))

    if not prompts:
        return None

    return SessionDigest(
        session_id=session_id,
        project=project,
        started_at=started,
        ended_at=ended,
        user_prompts=prompts,
        assistant_finals=finals[-5:],
        tools_used=_dedup(tools),
        files_touched=[],
        feedback_signals=feedback,
        n_user_turns=len(prompts),
        n_assistant_turns=len(finals),
        raw_path=path,
    )


def harvest_copilot(
    workspace_storage: str = "",
    *,
    scope: Any = "all",
    invoked_project: str = "",
    since_iso: Optional[str] = None,
    limit: int = 0,
) -> List[SessionDigest]:
    """Discover and normalize matching VS Code GitHub Copilot Chat sessions."""
    digests: List[SessionDigest] = []
    since_epoch = _iso_epoch(since_iso)
    for session_file in discover_vscode_sessions(workspace_storage):
        digest = digest_copilot_session(session_file.path, project=session_file.project)
        if digest is None or _is_headless_replay(digest) or _is_agent_session(digest):
            continue
        if not digest.project:
            # Empty-window and stale workspace entries cannot be scoped safely.
            continue
        if not _project_matches(digest.project or "", scope, invoked_project):
            continue
        if since_iso and digest.ended_at:
            ended_epoch = _iso_epoch(digest.ended_at)
            if since_epoch is not None and ended_epoch is not None:
                before_cutoff = ended_epoch < since_epoch
            else:
                # Preserve the previous best-effort behavior for malformed or
                # legacy timestamps that ``datetime.fromisoformat`` cannot parse.
                before_cutoff = digest.ended_at < since_iso
            if before_cutoff:
                continue
        digests.append(digest)
        if limit and len(digests) >= limit:
            break
    return digests
