#!/bin/sh
# Karta installer: one command on an Apple silicon Mac.
#
#   curl -fsSL https://raw.githubusercontent.com/sp-22/karta/main/install.sh | sh
#   ./install.sh                  or from a clone of this repo
#
# It installs uv if missing, installs the `karta` command (with Laya and push-to-talk voice), then runs
# `karta setup`, which checks permissions, installs whisper.cpp (Homebrew) and downloads the models.
# Afterwards:  karta start
set -e

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "Karta needs an Apple silicon Mac (M1 or later)."
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (the Python tool manager Karta uses)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

HERE="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
if [ -z "$KARTA_SOURCE" ] && grep -qs '^name = "karta"' "$HERE/pyproject.toml"; then
  SOURCE="$HERE"                                    # run from a clone
else
  SOURCE="${KARTA_SOURCE:-git+https://github.com/sp-22/karta}"   # piped from the web
fi
echo "Installing Karta from $SOURCE …"
uv tool install --python 3.12 --force "$SOURCE"

BIN="$(uv tool dir --bin)"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "Adding $BIN to your PATH (new terminals pick it up)…"; uv tool update-shell >/dev/null 2>&1 || true
     export PATH="$BIN:$PATH" ;;
esac

karta setup
