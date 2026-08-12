#!/usr/bin/env bash
set -Eeuo pipefail

umask 077
host=""
port=22
entry_file=""
known_hosts=""
apply=false
create=false
tmp=""
backup=""
old_file=""
known_hosts_existed=false
cleanup() { [[ -z "$tmp" ]] || rm -f -- "$tmp"; [[ -z "$backup" ]] || rm -f -- "$backup"; [[ -z "$old_file" ]] || rm -f -- "$old_file"; }
trap cleanup EXIT
usage() { echo "Usage: $0 --host HOST [--port PORT] --entry-file PATH --known-hosts PATH [--apply] [--create]"; }
die() { echo "Error: $1" >&2; exit "${2:-2}"; }

while (($#)); do
  case "$1" in
    --host) (($# >= 2)) || die "--host requires a value"; host=$2; shift 2 ;;
    --port) (($# >= 2)) || die "--port requires a value"; port=$2; shift 2 ;;
    --entry-file) (($# >= 2)) || die "--entry-file requires a value"; entry_file=$2; shift 2 ;;
    --known-hosts) (($# >= 2)) || die "--known-hosts requires a value"; known_hosts=$2; shift 2 ;;
    --apply) apply=true; shift ;;
    --create) create=true; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown argument" ;;
  esac
done
[[ -n "$host" && -n "$entry_file" && -n "$known_hosts" ]] || { usage >&2; exit 2; }
[[ "$host" != *[[:space:],\[\]\|]* && "$host" != \|* ]] || die "invalid host"
[[ "$port" =~ ^[0-9]+$ && ${#port} -le 5 ]] && ((10#$port >= 1 && 10#$port <= 65535)) || die "invalid port"
[[ -f "$entry_file" && ! -L "$entry_file" ]] || die "entry file must be a regular file"
ssh-keygen -lf "$entry_file" >/dev/null 2>&1 || die "invalid entry file"
lookup=$host
((10#$port == 22)) || lookup="[$host]:$port"
mapfile -t entry_lines < "$entry_file"
[[ ${#entry_lines[@]} -eq 1 && -n "${entry_lines[0]}" && "${entry_lines[0]}" != *$'\r'* ]] || die "entry file must contain exactly one nonempty line"
read -r entry_lookup entry_type entry_key extra <<< "${entry_lines[0]}" || die "unable to read entry file"
[[ "$entry_lookup" == "$lookup" && "$entry_type" == ssh-ed25519 && -n "$entry_key" ]] || die "entry does not match host and port"
[[ -z "${extra:-}" ]] || die "entry file must not contain comments"

if [[ ! -e "$known_hosts" ]]; then
  [[ "$create" == true ]] || die "known_hosts does not exist; use --create" 3
  [[ "$apply" == true ]] || { echo "Would create known_hosts and add the entry."; exit 0; }
else
  known_hosts_existed=true
  [[ -f "$known_hosts" && ! -L "$known_hosts" ]] || die "known_hosts must be a regular file"
  ssh-keygen -lf "$known_hosts" >/dev/null 2>&1 || die "invalid known_hosts file"
  if ssh-keygen -F "$lookup" -f "$known_hosts" >/dev/null 2>&1; then action=replaced; else action=added; fi
  [[ "$apply" == true ]] || { echo "Entry would be $action."; exit 0; }
fi

known_dir=$(dirname -- "$known_hosts")
[[ -d "$known_dir" ]] || die "known_hosts directory does not exist"
tmp=$(mktemp "$known_dir/.known-hosts.XXXXXX")
backup=$(mktemp "$known_dir/.known-hosts-backup.XXXXXX")
if [[ -e "$known_hosts" ]]; then
  cp -p -- "$known_hosts" "$backup"
  cp -p -- "$known_hosts" "$tmp"
  old_file="$tmp.old"
  ssh-keygen -R "$lookup" -f "$tmp" >/dev/null 2>&1 || die "unable to remove existing lookup" 4
  rm -f -- "$old_file"
  old_file=""
  chmod --reference="$known_hosts" "$tmp"
  chown --reference="$known_hosts" "$tmp"
else
  : >| "$backup"
  chmod 0600 "$tmp"
fi
printf '%s %s %s\n' "$entry_lookup" "$entry_type" "$entry_key" >> "$tmp"
ssh-keygen -lf "$tmp" >/dev/null 2>&1 || { [[ "$known_hosts_existed" == true ]] && cp -p -- "$backup" "$known_hosts"; die "updated known_hosts failed validation" 4; }
mv -f -- "$tmp" "$known_hosts"
tmp=""
ssh-keygen -F "$lookup" -f "$known_hosts" >/dev/null 2>&1 || {
  if [[ "$known_hosts_existed" == true ]]; then cp -p -- "$backup" "$known_hosts"; else rm -f -- "$known_hosts"; fi
  die "entry verification failed" 4
}
echo "Known-hosts entry updated."
