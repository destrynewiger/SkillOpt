#!/usr/bin/env bash
# SkillOpt-Sleep SessionEnd hook for Devin (best-effort, NON-BLOCKING).
#
# This does NOT run the optimizer. It only appends a tiny marker for local
# inspection or external automation. The current sleep engine uses transcript
# timestamps rather than this marker. The hook must never fail the session or
# spend API budget.
#
# Install this script as .devin/hooks/skillopt-sleep-on-session-end.sh and the
# config at .devin/hooks.v1.json. Devin CLI reads it automatically.
set -uo pipefail

[ -n "${HOME:-}" ] || exit 0
STATE_DIR="${HOME}/.skillopt-sleep"
mkdir -p "$STATE_DIR" 2>/dev/null || exit 0

# Record that a session just ended (cheap local activity signal).
printf '%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${DEVIN_PROJECT_DIR:-${PWD}}" \
  >> "$STATE_DIR/session-end.log" 2>/dev/null || true

exit 0
