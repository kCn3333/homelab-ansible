#!/usr/bin/env bash
set -Eeuo pipefail
set -o noclobber
tmp=$(mktemp -d)
cleanup() { [[ "$tmp" == /tmp/tmp.* && -d "$tmp" && ! -L "$tmp" ]] && rm -rf -- "$tmp"; }
trap cleanup EXIT
ssh-keygen -q -t ed25519 -N '' -f "$tmp/key1" >/dev/null
ssh-keygen -q -t ed25519 -N '' -f "$tmp/key2" >/dev/null
bash -o noclobber scripts/build-known-hosts-entry.sh --host managed-host --host-key-file "$tmp/key1.pub" --output "$tmp/entry"
bash -o noclobber scripts/build-known-hosts-entry.sh --host other-host --host-key-file "$tmp/key2.pub" --output "$tmp/other"
bash -o noclobber scripts/build-known-hosts-entry.sh --host port-host --port 2222 --host-key-file "$tmp/key2.pub" --output "$tmp/port-entry"

bash -o noclobber scripts/upsert-known-hosts-entry.sh --host managed-host --entry-file "$tmp/entry" --known-hosts "$tmp/known_hosts" --create
[[ ! -e "$tmp/known_hosts" ]]
if bash -o noclobber scripts/upsert-known-hosts-entry.sh --host managed-host --entry-file "$tmp/entry" --known-hosts "$tmp/missing-known-hosts" --apply; then exit 1; fi
[[ ! -e "$tmp/missing-known-hosts" ]]
bash -o noclobber scripts/upsert-known-hosts-entry.sh --host managed-host --entry-file "$tmp/entry" --known-hosts "$tmp/known_hosts" --create --apply
[[ $(stat -c '%a' "$tmp/known_hosts") == 600 ]]
cat "$tmp/other" >> "$tmp/known_hosts"
before=$(sha256sum "$tmp/known_hosts" | awk '{print $1}')
before_meta=$(stat -c '%u:%g:%a:%Y:%i' "$tmp/known_hosts")
other_before=$(grep '^other-host ' "$tmp/known_hosts")
bash -o noclobber scripts/upsert-known-hosts-entry.sh --host managed-host --entry-file "$tmp/entry" --known-hosts "$tmp/known_hosts"
[[ $(sha256sum "$tmp/known_hosts" | awk '{print $1}') == "$before" ]]
[[ $(stat -c '%u:%g:%a:%Y:%i' "$tmp/known_hosts") == "$before_meta" ]]
bash -o noclobber scripts/upsert-known-hosts-entry.sh --host managed-host --entry-file "$tmp/entry" --known-hosts "$tmp/known_hosts" --apply
ssh-keygen -F other-host -f "$tmp/known_hosts" >/dev/null
[[ $(grep '^other-host ' "$tmp/known_hosts") == "$other_before" ]]
[[ $(ssh-keygen -F managed-host -f "$tmp/known_hosts" | grep -c '^managed-host ') -eq 1 ]]
idempotent_snapshot=$(sha256sum "$tmp/known_hosts" | awk '{print $1}')
bash -o noclobber scripts/upsert-known-hosts-entry.sh --host managed-host --entry-file "$tmp/entry" --known-hosts "$tmp/known_hosts" --apply
[[ $(sha256sum "$tmp/known_hosts" | awk '{print $1}') == "$idempotent_snapshot" ]]
[[ -z $(find "$tmp" -name '*.old' -print -quit) ]]

bash -o noclobber scripts/upsert-known-hosts-entry.sh --host port-host --port 2222 --entry-file "$tmp/port-entry" --known-hosts "$tmp/port-known" --create --apply
ssh-keygen -F '[port-host]:2222' -f "$tmp/port-known" >/dev/null

printf '%s\n' "managed-host,other-host ssh-ed25519 $(awk '{print $2}' "$tmp/key1.pub")" > "$tmp/alias-entry"
if bash -o noclobber scripts/upsert-known-hosts-entry.sh --host managed-host --entry-file "$tmp/alias-entry" --known-hosts "$tmp/known_hosts" --apply; then exit 1; fi

printf '%s\n' 'invalid entry' > "$tmp/bad"
snapshot=$(sha256sum "$tmp/known_hosts" | awk '{print $1}')
if bash -o noclobber scripts/upsert-known-hosts-entry.sh --host managed-host --entry-file "$tmp/bad" --known-hosts "$tmp/known_hosts" --apply; then exit 1; fi
[[ $(sha256sum "$tmp/known_hosts" | awk '{print $1}') == "$snapshot" ]]

mkdir "$tmp/mock-bin"
real_ssh_keygen=$(command -v ssh-keygen)
cat > "$tmp/mock-bin/ssh-keygen" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
if [[ ${1:-} == -F ]]; then
  count=0
  [[ ! -e "$MOCK_COUNTER" ]] || read -r count < "$MOCK_COUNTER"
  count=$((count + 1))
  printf '%s\n' "$count" >| "$MOCK_COUNTER"
  if ((count >= 2)); then exit 1; fi
fi
exec "$REAL_SSH_KEYGEN" "$@"
EOF
chmod +x "$tmp/mock-bin/ssh-keygen"
rollback_snapshot=$(sha256sum "$tmp/known_hosts" | awk '{print $1}')
if PATH="$tmp/mock-bin:$PATH" REAL_SSH_KEYGEN="$real_ssh_keygen" MOCK_COUNTER="$tmp/counter" \
  bash -o noclobber scripts/upsert-known-hosts-entry.sh --host managed-host --entry-file "$tmp/entry" --known-hosts "$tmp/known_hosts" --apply; then
  exit 1
fi
[[ $(sha256sum "$tmp/known_hosts" | awk '{print $1}') == "$rollback_snapshot" ]]
echo "upsert-known-hosts-entry tests passed"
