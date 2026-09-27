#!/bin/sh
# Boltjar update for macOS and Linux. Pulls the latest code (fast-forward only),
# rebuilds the editor when its source changed, then runs ./start.sh with the same
# arguments. start.sh installs requirements.txt again if it changed.
set -eu
cd "$(dirname "$0")"

command -v git >/dev/null 2>&1 \
    || { echo "git was not found: install it and run ./update.sh again." >&2; exit 1; }
git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || { echo "This folder is not a git checkout, so it cannot pull: download the latest release instead." >&2; exit 1; }

before=$(git rev-parse HEAD)
git pull --ff-only \
    || { echo "The update stopped: git could not fast-forward (local changes, or a branch that has diverged)." >&2; exit 1; }

if ! git diff --quiet "$before" HEAD -- editor; then
    if command -v npm >/dev/null 2>&1; then
        echo "The editor changed: rebuilding it..."
        if [ ! -d editor/node_modules ] || ! git diff --quiet "$before" HEAD -- editor/package-lock.json; then
            npm ci --prefix editor || true
        fi
        npm run build --prefix editor || echo "The editor build failed: run \"npm run build --prefix editor\" to see why."
    else
        echo "The editor changed but npm was not found: install Node.js 18+ from https://nodejs.org to rebuild it."
    fi
fi

exec sh ./start.sh "$@"
