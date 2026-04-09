#!/bin/bash
#
# Dump all tracked files (respecting .gitignore) in LLM-readable format.
# Usage: ./scripts/dump.sh [output_file]
#
# If output_file is given, writes there. Otherwise prints to stdout.
#
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

output() {
  git ls-files --cached --others --exclude-standard | sort | while IFS= read -r file; do
    [ -f "$file" ] || continue
    echo "=== $file ==="
    cat "$file"
    echo ""
  done
}

if [ -n "${1:-}" ]; then
  output > "$1"
  echo "Dumped to $1 ($(wc -l < "$1") lines)" >&2
else
  output
fi
