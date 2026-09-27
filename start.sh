#!/bin/sh
# Boltjar for macOS and Linux. Finds Python 3.11 to 3.13 (or fetches a portable
# 3.12), prepares .venv, installs requirements.txt when it changed, builds the
# editor when it is missing, then runs the server. Arguments pass through to
# `python -m boltjar serve`, for example:  ./start.sh --port 8771 --no-browser
set -eu
cd "$(dirname "$0")"

# The portable Python used when none is installed: a pinned release of
# https://github.com/astral-sh/python-build-standalone. PBS_SUMS_SHA256 pins
# that release's SHA256SUMS file, and SHA256SUMS pins the archive.
PBS_TAG=20260924
PBS_PYTHON=3.12.14
PBS_SUMS_SHA256=f4c51660f65ce980a1113028ce6cb751d6251e17550bc2252bfd2be878c2cc1e
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_TAG"

VENV_PY=.venv/bin/python
PORTABLE_PY=.python/bin/python3
SUPPORTED='import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)'

say() { printf '%s\n' "$*"; }
fail() { printf '%s\n' "$*" >&2; exit 1; }

supported() { "$1" -c "$SUPPORTED" >/dev/null 2>&1; }

find_python() {
    for candidate in python3.12 python3.13 python3.11 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 && supported "$candidate"; then
            PY=$(command -v "$candidate")
            return 0
        fi
    done
    if [ -x "$PORTABLE_PY" ] && supported "$PORTABLE_PY"; then
        PY=$PORTABLE_PY
        return 0
    fi
    return 1
}

platform_triple() {
    case "$(uname -s)/$(uname -m)" in
        Darwin/arm64) echo aarch64-apple-darwin ;;
        Darwin/x86_64) echo x86_64-apple-darwin ;;
        Linux/x86_64) echo x86_64-unknown-linux-gnu ;;
        Linux/aarch64 | Linux/arm64) echo aarch64-unknown-linux-gnu ;;
        *) return 1 ;;
    esac
}

download() {  # url file
    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry 2 -o "$2" "$1"
    else
        wget -O "$2" "$1"
    fi
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

fetch_python() {
    triple=$(platform_triple) || fail "There is no portable Python for $(uname -s) $(uname -m): install Python 3.11 to 3.13 and run ./start.sh again."
    command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 \
        || fail "Fetching a portable Python needs curl or wget: install one, or install Python 3.12, and run ./start.sh again."
    command -v sha256sum >/dev/null 2>&1 || command -v shasum >/dev/null 2>&1 \
        || fail "Checking the download needs sha256sum or shasum: install one, or install Python 3.12, and run ./start.sh again."
    [ ! -e .python ] \
        || fail "The .python folder holds no working Python: delete it and run ./start.sh again to fetch it anew."
    asset="cpython-$PBS_PYTHON+$PBS_TAG-$triple-install_only.tar.gz"
    say "No Python 3.11 to 3.13 was found. Fetching a portable Python $PBS_PYTHON ($triple)..."
    tmp=$(mktemp -d)
    if ! fetch_into "$tmp" "$asset"; then
        rm -rf "$tmp" .python.partial
        fail "Could not set up the portable Python. Install Python 3.12 from https://www.python.org/downloads/ (or your package manager) and run ./start.sh again."
    fi
    rm -rf "$tmp"
}

fetch_into() {  # dir asset
    download "$PBS_URL/SHA256SUMS" "$1/SHA256SUMS" || return 1
    if [ "$(sha256_of "$1/SHA256SUMS")" != "$PBS_SUMS_SHA256" ]; then
        say "SHA256SUMS does not match the hash pinned in start.sh, so it is not used." >&2
        return 1
    fi
    want=$(awk -v name="$2" '$2 == name { print $1 }' "$1/SHA256SUMS")
    if [ -z "$want" ]; then
        say "$2 is not listed in SHA256SUMS." >&2
        return 1
    fi
    download "$PBS_URL/$(printf '%s' "$2" | sed 's/+/%2B/')" "$1/python.tar.gz" || return 1
    if [ "$(sha256_of "$1/python.tar.gz")" != "$want" ]; then
        say "The download does not match its SHA256, so it is not used." >&2
        return 1
    fi
    rm -rf .python.partial
    mkdir .python.partial || return 1
    tar -xzf "$1/python.tar.gz" -C .python.partial --strip-components=1 || return 1
    mv .python.partial .python
}

build_editor() {
    if ! command -v npm >/dev/null 2>&1; then
        say "The editor is not built and npm was not found: install Node.js 18+ from https://nodejs.org and run ./start.sh again. The server starts without the editor page."
        return 0
    fi
    say "Building the editor, first run only..."
    if [ ! -d editor/node_modules ] && ! npm ci --prefix editor; then
        say "npm ci failed: the server starts without the editor page."
        return 0
    fi
    npm run build --prefix editor || say "The editor build failed: the server starts without the editor page."
}

create_venv() {
    say "Creating .venv with $PY..."
    "$PY" -m venv .venv && return 0
    rm -rf .venv  # this run made it, and it is incomplete
    [ "$PY" != "$PORTABLE_PY" ] || fail "Could not create .venv."
    # Debian and Ubuntu ship the venv module as a separate package; the portable
    # Python carries its own, so it takes over instead of asking for an install.
    say "That Python cannot create a virtualenv. Using a portable Python instead."
    [ -x "$PORTABLE_PY" ] || fetch_python
    PY=$PORTABLE_PY
    "$PY" -m venv .venv || { rm -rf .venv; fail "Could not create .venv."; }
}

if [ ! -x "$VENV_PY" ]; then
    if [ -e .venv ]; then
        fail "The .venv folder has no bin/python (it was made on another system). Delete the .venv folder and run ./start.sh again to rebuild it."
    fi
    find_python || fetch_python
    [ -n "${PY:-}" ] || PY=$PORTABLE_PY
    create_venv
fi
supported "$VENV_PY" \
    || fail ".venv holds a Python that Boltjar does not support (it needs 3.11 to 3.13). Delete the .venv folder and run ./start.sh again to rebuild it."
"$VENV_PY" -m boltjar.deps || fail "Installing the requirements failed: the lines above say why."
[ -f editor/dist/index.html ] || build_editor
exec "$VENV_PY" -m boltjar serve "$@"
