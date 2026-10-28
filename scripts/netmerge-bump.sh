#!/usr/bin/env bash
# Move the pinned net-merge version (deploy/netmerge/install.sh) to a new release.
#
# Looks up the latest net-merge release (or takes VERSION as argument), and if
# it is newer than NM_VERSION:
#   1. checks the signed apt repository carries it for amd64, arm64 and armhf
#      (install.sh installs exactly that version from there),
#   2. downloads the release tarballs, computes their SHA-256 and checks the
#      x86_64 binary reports that version,
#   3. rewrites NM_VERSION, the hashes and the version named in
#      docs/NETZWERK-REDUNDANZ.md.
#
# Run from the repository root. Used by .github/workflows/netmerge-bump.yml.
# Writes old=/new=/changed= to $GITHUB_OUTPUT when set.
#
# Usage: scripts/netmerge-bump.sh [VERSION]
set -euo pipefail

SCRIPT="deploy/netmerge/install.sh"
DOC="docs/NETZWERK-REDUNDANZ.md"
UPSTREAM="net-merge/net-merge"
APT="https://pkg.net-merge.org/apt/dists/stable/main"

out() { [[ -n "${GITHUB_OUTPUT:-}" ]] && echo "$1=$2" >> "$GITHUB_OUTPUT"; echo "$1=$2"; }

old="$(sed -n 's/^NM_VERSION="\(.*\)"$/\1/p' "$SCRIPT")"
[[ -n "$old" ]] || { echo "NM_VERSION not found in $SCRIPT" >&2; exit 1; }

new="${1:-}"
if [[ -z "$new" ]]; then
  new="$(gh api "repos/$UPSTREAM/releases/latest" --jq .tag_name)"
fi
new="${new#v}"
[[ "$new" =~ ^[0-9]+(\.[0-9]+)*$ ]] || { echo "Unexpected version: $new" >&2; exit 1; }

out old "$old"
out new "$new"
if [[ "$old" == "$new" || "$(printf '%s\n%s\n' "$old" "$new" | sort -V | tail -1)" == "$old" ]]; then
  echo "net-merge $old is current."
  out changed false
  exit 0
fi

# 1. apt repository: install.sh asks for net-merge=$new, so all three must have it.
for arch in amd64 arm64 armhf; do
  if ! curl -fsSL "$APT/binary-$arch/Packages" | grep -qx "Version: $new"; then
    echo "::warning::net-merge $new is not in the apt repository for $arch yet — trying again next run."
    out changed false
    exit 0
  fi
done

# 2. Tarballs: hashes for the pin, plus a smoke test of the x86_64 build.
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
declare -A sums
for arch in x86_64 aarch64 armv7l; do
  curl -fsSL -o "$tmp/$arch.tgz" \
    "https://github.com/$UPSTREAM/releases/download/v$new/net-merge-linux-$arch.tar.gz"
  sums[$arch]="$(sha256sum "$tmp/$arch.tgz" | cut -d' ' -f1)"
done
tar -xzf "$tmp/x86_64.tgz" -C "$tmp" net-merge
reported="$("$tmp/net-merge" version 2>/dev/null | head -1)"
if [[ "$reported" != "net-merge $new" ]]; then
  echo "x86_64 binary reports '$reported', expected 'net-merge $new'." >&2
  exit 1
fi

# 3. Rewrite the pin.
sed -i "s/^NM_VERSION=\"$old\"$/NM_VERSION=\"$new\"/" "$SCRIPT"
for arch in x86_64 aarch64 armv7l; do
  sed -i "s/^  \[$arch\]=\"[0-9a-f]\{64\}\"$/  [$arch]=\"${sums[$arch]}\"/" "$SCRIPT"
  grep -q "^  \[$arch\]=\"${sums[$arch]}\"$" "$SCRIPT" \
    || { echo "Could not update the $arch hash in $SCRIPT" >&2; exit 1; }
done
sed -i "s/derzeit $old)/derzeit $new)/" "$DOC"
out changed true
