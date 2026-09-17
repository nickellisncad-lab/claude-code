#!/bin/bash
# Installs the test dependencies so pytest works from the first turn of a
# Claude Code on the web session. Without this every session rediscovers that
# pytest is missing, installs it, and retries -- wasted turns on every run.
set -euo pipefail

# Local checkouts already have whatever venv the developer set up; only the
# fresh remote containers need provisioning.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR/letterboxd-radarr-sync"

# Matches the CI install step. pip is idempotent, so re-running on resume or
# clear is a no-op against the cached container state.
python3 -m pip install --quiet --disable-pip-version-check -r requirements-dev.txt
