#!/bin/bash
# Installs the TCG Order Printer helper into a private Python environment and
# starts the setup wizard. Re-run it any time to upgrade or change settings.
set -euo pipefail
cd "$(dirname "$0")"
STATE="$HOME/Library/Application Support/TCGOrderPrinter"

if [[ "$(uname)" != Darwin ]]; then echo "The helper currently supports macOS only." >&2; exit 1; fi

# Prefer interpreters that survive package-manager upgrades; need 3.10+.
PY=""
for candidate in /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
                 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [[ -x "$candidate" ]] && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$candidate"; break
  fi
done
if [[ -z "$PY" ]]; then
  echo "Python 3.10 or newer is required. Install it from https://www.python.org/downloads/macos/ and run this again." >&2
  exit 1
fi

echo "Installing helper with $("$PY" --version) ..."
mkdir -p "$STATE"; chmod 700 "$STATE"
"$PY" -m venv --clear "$STATE/runtime"
"$STATE/runtime/bin/python" -m pip install --quiet --upgrade pip
"$STATE/runtime/bin/python" -m pip install --quiet .
exec "$STATE/runtime/bin/python" -m tcg_order_printer_helper setup "$@"
