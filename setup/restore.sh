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

# '*T*Z*' not '*T*Z': backup.sh suffixes "-2", "-3" … when a stamp's second is already taken.
backups() { [ -d "$root" ] || return 0; find "$root" -mindepth 1 -maxdepth 1 -type d -name '*T*Z*' | sort; }

# A restore takes a safety backup of the CURRENT state first, which makes that safety copy
# the newest backup on disk. If "most recent" included it, the second bare restore in a row
# would restore the damage it had just captured — and report success while doing it. Safety
# copies are marked and skipped when picking a default; --list still shows them, and an
# explicit timestamp still restores one (that is how you undo a restore).
is_safety() { [ -e "$1/.safety" ]; }
restorable() { backups | while IFS= read -r b; do [ -n "$b" ] || continue; is_safety "$b" || echo "$b"; done; }

if [ "$list" -eq 1 ]; then
  found=0
  while IFS= read -r b; do
    [ -n "$b" ] || continue; found=1
    n=$( { grep -c '^present ' "$b/manifest.txt" 2>/dev/null || true; } | tr -d ' ')
    tag=""; is_safety "$b" && tag="  [safety copy taken by a restore — not chosen by default]"
    echo "$(basename "$b")  ${n:-0} path(s)$tag"
    { grep '^present ' "$b/manifest.txt" 2>/dev/null || true; } | sed 's/^present /    /'
  done < <(backups)
  [ "$found" -eq 1 ] || echo "no backups under $root"
  exit 0
fi

if [ -z "$stamp" ]; then
  stamp="$(restorable | tail -1)"; stamp="${stamp##*/}"
  if [ -z "$stamp" ]; then
    if [ -n "$(backups)" ]; then
      echo "restore.sh: the only backups under $root are safety copies taken by earlier restores." >&2
      echo "            Pick one explicitly if that is what you want (see --list)." >&2
    else
      echo "restore.sh: no backups under $root" >&2
    fi
    exit 1
  fi
fi
src="$root/$stamp"
[ -f "$src/manifest.txt" ] || { echo "restore.sh: no backup named '$stamp' under $root (see --list)" >&2; exit 1; }

# shellcheck disable=SC2088  # literal "~/" prefix match on the manifest text
expand() { case "$1" in "~/"*) printf '%s\n' "$HOME/${1#"~/"}" ;; *) printf '%s\n' "$1" ;; esac; }
relative() { case "$1" in "$HOME"/*) printf '%s\n' "${1#"$HOME"/}" ;; *) printf '%s\n' "${1#/}" ;; esac; }

if [ "$dry_run" -eq 0 ]; then
  safety="$(BALANCEWHEEL_BACKUP_ROOT="$root" "$here/backup.sh" --paths "$src/paths.txt" 2>/dev/null)"
  # Mark it so a later bare restore skips it rather than restoring this damaged state back.
  [ -d "$safety" ] && : > "$safety/.safety"
  echo "safety backup of the current state: $safety" >&2
  # backup.sh suffixes a stamp whose second is already taken, so this can never land in
  # $src and overwrite the copy being restored. It used to: backup, damage and restore
  # inside one second destroyed the good copy and then restored the damage over itself.
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
