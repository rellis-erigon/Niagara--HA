#!/bin/sh
# Refresh the vendored faceplate catalogue from the cards repository.
#
# The add-on offers a faceplate picker, so it has to know which faces the
# installed cards can render. Vendoring the list keeps the add-on working
# offline; this script is how it stops being stale.
set -eu
# Default: a clone of the cards repo beside this one, under either name.
SRC="${1:-}"
if [ -z "$SRC" ]; then
  for d in ../HA-rellis-erigon-Cards ../HA-Cards; do
    [ -f "$d/faceplates/index.json" ] && { SRC="$d/faceplates/index.json"; break; }
  done
fi
DEST="$(dirname "$0")/../faceplates.json"
[ -n "$SRC" ] && [ -f "$SRC" ] || { echo "no catalogue found; pass its path" >&2; exit 1; }
cp "$SRC" "$DEST"
echo "faceplates.json <- $SRC"
