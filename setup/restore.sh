#!/usr/bin/env bash
# setup/restore.sh — put the files from a setup/backup.sh backup back in place.
#
# Usage:
#   setup/restore.sh                restore the most recent backup
#   setup/restore.sh TIMESTAMP      restore that backup (see --list)
#   setup/restore.sh --list         show available backups and what each holds
#   setup/restore.sh --dry-run [T]  print what would be restored, change nothing
#
# A restore is itself reversible: before touching anything it takes a fresh backup of the
# current state with setup/backup.sh and prints where it went. Only paths the manifest
# marks "present" are restored; a path that did not exist when the backup was taken is
# left as it is now and reported, so you can delete it by hand for a full rollback.
# Restoring a directory replaces the current directory wholesale.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="${BALANCEWHEEL_BACKUP_ROOT:-$HOME/.balancewheel/backups}"
dry_run=0; list=0; stamp=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run=1; shift ;;
    --list)    list=1; shift ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "restore.sh: unknown option '$1'" >&2; exit 2 ;;
    *)  stamp="$1"; shift ;;
  esac
done

backups() { [ -d "$root" ] || return 0; find "$root" -mindepth 1 -maxdepth 1 -type d -name '*T*Z' | sort; }

if [ "$list" -eq 1 ]; then
  found=0
  while IFS= read -r b; do
    [ -n "$b" ] || continue; found=1
    n=$( { grep -c '^present ' "$b/manifest.txt" 2>/dev/null || true; } | tr -d ' ')
    echo "$(basename "$b")  ${n:-0} path(s)"
    { grep '^present ' "$b/manifest.txt" 2>/dev/null || true; } | sed 's/^present /    /'
  done < <(backups)
  [ "$found" -eq 1 ] || echo "no backups under $root"
  exit 0
fi

if [ -z "$stamp" ]; then
  stamp="$(backups | tail -1)"; stamp="${stamp##*/}"
  [ -n "$stamp" ] || { echo "restore.sh: no backups under $root" >&2; exit 1; }
fi
src="$root/$stamp"
[ -f "$src/manifest.txt" ] || { echo "restore.sh: no backup named '$stamp' under $root (see --list)" >&2; exit 1; }

# shellcheck disable=SC2088  # literal "~/" prefix match on the manifest text
expand() { case "$1" in "~/"*) printf '%s\n' "$HOME/${1#"~/"}" ;; *) printf '%s\n' "$1" ;; esac; }
relative() { case "$1" in "$HOME"/*) printf '%s\n' "${1#"$HOME"/}" ;; *) printf '%s\n' "${1#/}" ;; esac; }

if [ "$dry_run" -eq 0 ]; then
  safety="$(BALANCEWHEEL_BACKUP_ROOT="$root" "$here/backup.sh" --paths "$src/paths.txt" 2>/dev/null)"
  echo "safety backup of the current state: $safety" >&2
  # The safety backup can share a second with $stamp only if you restore within a second
  # of backing up; then it is the same directory and there is nothing to undo anyway.
fi

restored=0; skipped=0
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    "present "*)
      p="${line#present }"; target="$(expand "$p")"; rel="$(relative "$target")"; from="$src/$rel"
      [ -e "$from" ] || { echo "restore.sh: $p is in the manifest but missing from the backup; skipped" >&2; skipped=$((skipped + 1)); continue; }
      if [ "$dry_run" -eq 1 ]; then
        echo "would restore  $from  ->  $target" >&2
      else
        mkdir -p "$(dirname "$target")"
        [ -d "$target" ] && [ ! -L "$target" ] && rm -rf "$target"
        cp -Rp "$from" "$target"
      fi
      restored=$((restored + 1)) ;;
    "absent "*)
      p="${line#absent }"; target="$(expand "$p")"
      if [ -e "$target" ]; then
        echo "note: $p did not exist when this backup was taken; it exists now and was left in place" >&2
      fi ;;
  esac
done < "$src/manifest.txt"

if [ "$dry_run" -eq 1 ]; then
  echo "dry run: $restored path(s) would be restored from $stamp" >&2
else
  msg="restored $restored path(s) from $stamp"
  [ "$skipped" -gt 0 ] && msg="$msg ($skipped skipped)"
  echo "$msg" >&2
fi
