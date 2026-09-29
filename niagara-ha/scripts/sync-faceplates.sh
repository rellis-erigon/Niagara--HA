#!/bin/sh
# Refresh the vendored faceplate catalogue from the cards repository.
#
# The add-on offers a faceplate picker, so it has to know which faces the
# installed cards can render. Vendoring the list keeps the add-on working
# offline; this script is how it stops being stale.
set -eu
SRC="${1:-../HA-Cards/faceplates/index.json}"
DEST="$(dirname "$0")/../faceplates.json"
[ -f "$SRC" ] || { echo "no catalogue at $SRC" >&2; exit 1; }
cp "$SRC" "$DEST"
echo "faceplates.json <- $SRC"
