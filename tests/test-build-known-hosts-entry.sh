#!/usr/bin/env bash
set -Eeuo pipefail
set -o noclobber
tmp=$(mktemp -d)
cleanup() { [[ "$tmp" == /tmp/tmp.* && -d "$tmp" && ! -L "$tmp" ]] && rm -rf -- "$tmp"; }
trap cleanup EXIT
ssh-keygen -q -t ed25519 -N '' -f "$tmp/key" >/dev/null
ssh-keygen -q -t rsa -b 2048 -N '' -f "$tmp/rsa" >/dev/null
expect_fail() { if "$@" >/dev/null 2>&1; then echo "expected failure" >&2; exit 1; fi; }

builder_output=$(bash -o noclobber scripts/build-known-hosts-entry.sh --host managed-host --host-key-file "$tmp/key.pub" --output "$tmp/entry22")
[[ $(awk '{print $1}' "$tmp/entry22") == managed-host ]]
[[ $(awk '{print NF}' "$tmp/entry22") -eq 3 ]]
[[ $(stat -c '%a' "$tmp/entry22") == 600 ]]
key_material=$(awk '{print $2}' "$tmp/key.pub")
[[ "$builder_output" != *"$key_material"* ]]
bash -o noclobber scripts/build-known-hosts-entry.sh --host managed-host --port 2222 --host-key-file "$tmp/key.pub" --output "$tmp/entry2222"
[[ $(awk '{print $1}' "$tmp/entry2222") == '[managed-host]:2222' ]]
expect_fail bash -o noclobber scripts/build-known-hosts-entry.sh --host managed-host --host-key-file "$tmp/key" --output "$tmp/private"
expect_fail bash -o noclobber scripts/build-known-hosts-entry.sh --host managed-host --host-key-file "$tmp/key.pub" --output "$tmp/entry22"
cat "$tmp/key.pub" "$tmp/key.pub" > "$tmp/two.pub"
printf '%s\n' "from=restricted $(cat "$tmp/key.pub")" > "$tmp/options.pub"
: > "$tmp/empty.pub"
expect_fail bash -o noclobber scripts/build-known-hosts-entry.sh --host managed-host --host-key-file "$tmp/two.pub" --output "$tmp/two"
expect_fail bash -o noclobber scripts/build-known-hosts-entry.sh --host managed-host --host-key-file "$tmp/options.pub" --output "$tmp/options"
expect_fail bash -o noclobber scripts/build-known-hosts-entry.sh --host managed-host --host-key-file "$tmp/rsa.pub" --output "$tmp/rsa-entry"
expect_fail bash -o noclobber scripts/build-known-hosts-entry.sh --host managed-host --host-key-file "$tmp/empty.pub" --output "$tmp/empty"
for invalid_host in 'managed host' 'managed,host' '[managed-host]' '|1|hashed|value' $'managed\nhost'; do
  expect_fail bash -o noclobber scripts/build-known-hosts-entry.sh --host "$invalid_host" --host-key-file "$tmp/key.pub" --output "$tmp/invalid-host"
done
for invalid_port in 0 65536 text -1; do
  expect_fail bash -o noclobber scripts/build-known-hosts-entry.sh --host managed-host --port "$invalid_port" --host-key-file "$tmp/key.pub" --output "$tmp/invalid-port"
done
bash -o noclobber scripts/build-known-hosts-entry.sh --host managed-host --host-key-file "$tmp/key.pub" --output "$tmp/entry22" --force
echo "build-known-hosts-entry tests passed"
