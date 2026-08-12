#!/usr/bin/env bash
set -Eeuo pipefail

umask 077
host=""
port=22
key_file=""
output=""
force=false
tmp=""
cleanup() { [[ -z "$tmp" ]] || rm -f -- "$tmp"; }
trap cleanup EXIT

usage() {
  echo "Usage: $0 --host HOST [--port PORT] --host-key-file PATH --output PATH [--force]"
}

die() { echo "Error: $1" >&2; exit "${2:-2}"; }

while (($#)); do
  case "$1" in
    --host) (($# >= 2)) || die "--host requires a value"; host=$2; shift 2 ;;
    --port) (($# >= 2)) || die "--port requires a value"; port=$2; shift 2 ;;
    --host-key-file) (($# >= 2)) || die "--host-key-file requires a value"; key_file=$2; shift 2 ;;
    --output) (($# >= 2)) || die "--output requires a value"; output=$2; shift 2 ;;
    --force) force=true; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown argument" ;;
  esac
done

[[ -n "$host" && -n "$key_file" && -n "$output" ]] || { usage >&2; exit 2; }
[[ "$host" != *[[:space:],\[\]\|]* && "$host" != \|* ]] || die "invalid host"
if [[ ! "$port" =~ ^[0-9]+$ || ${#port} -gt 5 ]]; then
  die "invalid port"
fi

port_number=$((10#$port))

if ((port_number < 1 || port_number > 65535)); then
  die "invalid port"
fi
[[ -f "$key_file" && ! -L "$key_file" ]] || die "host key file must be a regular file"
[[ ! -e "$output" || "$force" == true ]] || die "output already exists" 3

mapfile -t key_lines < "$key_file"
[[ ${#key_lines[@]} -eq 1 && -n "${key_lines[0]}" && "${key_lines[0]}" != *$'\r'* ]] || die "host key file must contain exactly one nonempty line"
read -r key_type key_data extra <<< "${key_lines[0]}" || die "unable to read host key"
[[ "$key_type" == ssh-ed25519 && -n "$key_data" ]] || die "only one ssh-ed25519 public host key is accepted"
[[ -z "${extra:-}" || "$extra" != "PRIVATE" ]] || die "private keys are not accepted"
if grep -q 'PRIVATE KEY' "$key_file" 2>/dev/null; then die "private keys are not accepted"; fi
ssh-keygen -lf "$key_file" >/dev/null 2>&1 || die "invalid public host key"

output_dir=$(dirname -- "$output")
[[ -d "$output_dir" ]] || die "output directory does not exist"
tmp=$(mktemp "$output_dir/.known-hosts-entry.XXXXXX")
chmod 0600 "$tmp"
if ((10#$port == 22)); then
  printf '%s %s %s\n' "$host" "$key_type" "$key_data" >| "$tmp"
else
  printf '[%s]:%s %s %s\n' "$host" "$port" "$key_type" "$key_data" >| "$tmp"
fi
if [[ "$force" == true ]]; then
  mv -f -- "$tmp" "$output"
else
  mv -n -- "$tmp" "$output"
  [[ ! -e "$tmp" ]] || die "output appeared during creation" 3
fi
tmp=""
chmod 0600 "$output"
echo "Known-hosts entry created."
