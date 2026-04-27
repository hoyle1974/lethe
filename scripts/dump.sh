#!/bin/bash
#
# Dump all tracked files (respecting .gitignore) in LLM-readable format.
# Usage: ./scripts/dump.sh [--source|-source|-s] [output_file]
#
# Options:
#   --source | -source | -s   Dump only Python source files (*.py)
#
# If output_file is given, writes there. Otherwise prints to stdout.
#
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SOURCE_ONLY=false
OUTPUT_FILE=""

for arg in "$@"; do
  case "$arg" in
    --source|-source|-s)
      SOURCE_ONLY=true
      ;;
    *)
      OUTPUT_FILE="$arg"
      ;;
  esac
done

output() {
  git ls-files --cached --others --exclude-standard | sort | while IFS= read -r file; do
    [ -f "$file" ] || continue
    if $SOURCE_ONLY && [[ "$file" != *.py ]]; then continue; fi
    echo "=== $file ==="
    cat "$file"
    echo ""
  done
}

if [ -n "$OUTPUT_FILE" ]; then
  output > "$OUTPUT_FILE"
  echo "Dumped to $OUTPUT_FILE ($(wc -l < "$OUTPUT_FILE") lines)" >&2
else
  output
fi
