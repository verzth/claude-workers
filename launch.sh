#!/bin/sh
# Preserve the caller's project directory; use a relocatable plugin path.
set -eu
plugin_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec claude --plugin-dir "$plugin_dir" "$@"
