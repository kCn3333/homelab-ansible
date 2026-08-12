#!/usr/bin/env bash
set -Eeuo pipefail
set -o noclobber
tmp=$(mktemp -d)
cleanup() { [[ "$tmp" == /tmp/tmp.* && -d "$tmp" && ! -L "$tmp" ]] && rm -rf -- "$tmp"; }
trap cleanup EXIT
mkdir "$tmp/bin"
for command_name in apt-get getent passwd stat systemctl sudo visudo; do
  printf '#!/usr/bin/env bash\nexit 1\n' > "$tmp/bin/$command_name"
  chmod +x "$tmp/bin/$command_name"
done
rm -- "$tmp/bin/apt-get"
cat > "$tmp/bin/apt-get" <<'EOF'
#!/usr/bin/env bash
echo 'Inst example-package [1] (2 repository)'
exit 0
EOF
chmod +x "$tmp/bin/apt-get"
if ! PATH="$tmp/bin:/usr/bin:/bin" bash -o noclobber scripts/audit-managed-host.sh > "$tmp/report"; then
  echo "audit script unexpectedly failed" >&2
  exit 1
fi
rg -q '^apt_simulation_rc=0$' "$tmp/report"
rg -q '^apt_planned_installations_or_updates=1$' "$tmp/report"
! rg -q 'repository|Inst example-package' "$tmp/report"
PATH="$tmp/bin:/usr/bin:/bin" bash -o noclobber scripts/audit-managed-host.sh --show-packages > "$tmp/packages"
rg -q '^apt_planned_packages=example-package$' "$tmp/packages"
cat >| "$tmp/bin/apt-get" <<'EOF'
#!/usr/bin/env bash
echo 'simulation failed' >&2
exit 42
EOF
chmod +x "$tmp/bin/apt-get"
set +e
PATH="$tmp/bin:/usr/bin:/bin" bash -o noclobber scripts/audit-managed-host.sh > "$tmp/failure-report"
failure_rc=$?
set -e
[[ $failure_rc -eq 1 ]]
rg -q '^apt_simulation_rc=42$' "$tmp/failure-report"
! rg -q 'simulation failed' "$tmp/failure-report"
echo "audit-managed-host tests passed"
