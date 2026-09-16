#!/usr/bin/env bash
# setup/backup.sh — copy the files a balancewheel setup will touch into a timestamped
# backup directory, before anything edits them. Never deletes, never moves.
#
# Usage:
#   setup/backup.sh                 back up every existing path in setup/paths.txt
#   setup/backup.sh --dry-run       print what would be copied, copy nothing
#   setup/backup.sh --paths FILE    use a different path list
#
# Backups land in $BALANCEWHEEL_BACKUP_ROOT (default ~/.balancewheel/backups) under a
# UTC timestamp, preserving each path's layout relative to $HOME, plus:
#   manifest.txt   one line per listed path: "present <path>" or "absent <path>"
#   paths.txt      the list that was used, so restore.sh needs nothing from the repo
# Prints the backup directory on stdout. Exit 0 even if nothing existed (the manifest
# records that), non-zero only on a copy failure.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
paths_file="$here/paths.txt"
dry_run=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run=1; shift ;;
    --paths)   paths_file="${2:?--paths needs a file}"; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "backup.sh: unknown argument '$1'" >&2; exit 2 ;;
  esac
done
[ -f "$paths_file" ] || { echo "backup.sh: no such path list: $paths_file" >&2; exit 2; }

root="${BALANCEWHEEL_BACKUP_ROOT:-$HOME/.balancewheel/backups}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
# Never reuse an existing directory. The stamp has one-second resolution, and a restore takes
# a safety backup of the current state before it writes: run backup → damage → restore inside
# the same second and that safety copy would land in the SAME directory as the backup being
# restored, overwriting the good copy with the damaged state. The restore then "succeeds" and
# puts the damage back, with the only good copy gone. Suffix instead of colliding.
if [ -e "$root/$stamp" ]; then
  n=2; while [ -e "$root/$stamp-$n" ]; do n=$((n + 1)); done
  stamp="$stamp-$n"
fi
dest="$root/$stamp"

# Expand a leading "~/" to $HOME; anything else is used as written. (The quoted tilde is
# a literal pattern match on the list's text, not a failed expansion.)
# shellcheck disable=SC2088
expand() { case "$1" in "~/"*) printf '%s\n' "$HOME/${1#"~/"}" ;; *) printf '%s\n' "$1" ;; esac; }
# The path relative to $HOME, or the absolute path minus its leading "/" if outside it.
relative() { case "$1" in "$HOME"/*) printf '%s\n' "${1#"$HOME"/}" ;; *) printf '%s\n' "${1#/}" ;; esac; }

present=0; absent=0
manifest=""
while IFS= read -r line || [ -n "$line" ]; do
  # Strip comments and blank lines. (grep is avoided on purpose: under pipefail a grep
  # that selects nothing exits 1 and would kill the script silently.)
  line="${line%%#*}"; line="${line#"${line%%[![:space:]]*}"}"; line="${line%"${line##*[![:space:]]}"}"
  [ -n "$line" ] || continue
  src="$(expand "$line")"
  if [ -e "$src" ]; then
    present=$((present + 1))
    manifest="$manifest""present $line"$'\n'
    rel="$(relative "$src")"
    if [ "$dry_run" -eq 1 ]; then
      echo "would copy  $src  ->  $dest/$rel" >&2
    else
      mkdir -p "$dest/$(dirname "$rel")"
      # -R copies directories recursively and keeps symlinks as symlinks; -p keeps modes.
      cp -Rp "$src" "$dest/$rel"
    fi
  else
    absent=$((absent + 1))
    manifest="$manifest""absent $line"$'\n'
  fi
done < "$paths_file"

if [ "$dry_run" -eq 1 ]; then
  echo "dry run: $present path(s) would be backed up, $absent listed path(s) do not exist" >&2
  exit 0
fi

mkdir -p "$dest"
printf '%s' "$manifest" > "$dest/manifest.txt"
cp "$paths_file" "$dest/paths.txt"
echo "backed up $present path(s) to $dest ($absent listed path(s) did not exist)" >&2
echo "$dest"
