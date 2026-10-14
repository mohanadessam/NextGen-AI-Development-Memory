#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
printf 'NextGen AI Development Memory - macOS setup\n'
read -r -p 'Full path to an existing project: ' project
if [[ ! -d "$project" ]]; then
  printf 'Folder not found; no changes made.\n' >&2
  exit 1
fi
read -r -p 'Is this Nextory factory ERP? [y/N] ' yn
if [[ "$yn" =~ ^([yY]|[yY][eE][sS])$ ]]; then
  python3 install.py --project "$project" --preset nextory
else
  python3 install.py --project "$project"
fi
printf 'Done. Open a new session in your AI tools; verify the nextgen-memory MCP server.\n'
