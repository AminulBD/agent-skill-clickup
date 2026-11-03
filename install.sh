#!/usr/bin/env bash
# Symlink the skill into a skills directory (default: ~/.claude/skills, i.e. every project).
# Usage: ./install.sh [TARGET_SKILLS_DIR]     e.g. ./install.sh .claude/skills  (one project only)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
target="${1:-$HOME/.claude/skills}"
mkdir -p "$target"
if [ -e "$target/clickup" ] && [ ! -L "$target/clickup" ]; then
  echo "$target/clickup exists and is not a symlink; remove it first" >&2; exit 1
fi
ln -sfn "$here/skills/clickup" "$target/clickup"
echo "linked $target/clickup -> $here/skills/clickup"
