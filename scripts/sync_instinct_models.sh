#!/usr/bin/env bash
# Vendor the shared model layer (uditakankananonononono/shared-models) into meemee/_vendor at a pinned commit.
# Usage: scripts/sync_instinct_models.sh <commit> [repo-url]
set -euo pipefail
commit="${1:?usage: sync_instinct_models.sh <commit> [repo-url]}"
repo="${2:-git@github.com:uditakankananonononono/shared-models.git}"
root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
git clone -q "$repo" "$tmp/src"
git -C "$tmp/src" checkout -q "$commit"
full="$(git -C "$tmp/src" rev-parse HEAD)"
dest="$root/meemee/_vendor/instinct_models"
rm -rf "$dest"
mkdir -p "$(dirname "$dest")"
cp -R "$tmp/src/instinct_models" "$dest"
find "$dest" -name '__pycache__' -prune -exec rm -rf {} +
digest="$(cd "$dest" && find . -type f -name '*.py' | LC_ALL=C sort | xargs sha256sum | sha256sum | cut -d' ' -f1)"
cat > "$root/meemee/_vendor/INSTINCT_MODELS_PIN" <<PIN
repo=https://github.com/uditakankananonononono/shared-models
commit=$full
tree_sha256=$digest
PIN
echo "vendored instinct_models @ $full"
