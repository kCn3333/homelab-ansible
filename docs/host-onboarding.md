# Managed host onboarding

These optional tools support a two-channel onboarding process. Public scripts
define repeatable checks and changes; private host identity, inventory, and
credentials remain in Semaphore or on the administrator workstation.

## Procedure

1. From a trusted console, run `scripts/audit-managed-host.sh` on the new host.
2. Transfer only the intended public key from `<PUBLIC_KEY_PATH>` through a
   trusted channel. The matching private key belongs only in Semaphore Key
   Store or on the administrator workstation.
3. Review the bootstrap plan, then explicitly apply it when appropriate:

   ```text
   scripts/bootstrap-managed-host.sh --public-key <PUBLIC_KEY_PATH>
   scripts/bootstrap-managed-host.sh --public-key <PUBLIC_KEY_PATH> --apply
   ```

   Add `--install-missing`, `--repair`, or `--allow-passwordless-sudo` only
   after reviewing their effects. Full passwordless sudo makes `ansible` an
   administrative account. Without `--allow-passwordless-sudo`, bootstrap
   deliberately leaves `/etc/sudoers.d/90-semaphore-ansible` absent and the
   account is not ready for privileged Semaphore tasks.
4. Read the public SSH host key from a trusted local console. It is not fetched
   automatically because an unauthenticated network fetch cannot establish the
   host's identity.
5. Build a temporary known-hosts entry without making a network connection:

   ```text
   scripts/build-known-hosts-entry.sh --host <HOST> --port <PORT> --host-key-file <PUBLIC_KEY_PATH> --output <KNOWN_HOSTS_PATH>.entry
   ```

6. Test SSH directly with strict verification:

   ```text
   ssh -p <PORT> -o StrictHostKeyChecking=yes -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no -o UserKnownHostsFile=<KNOWN_HOSTS_PATH> ansible@<HOST>
   ```

7. Atomically add or replace only this host entry after a dry run:

   ```text
   scripts/upsert-known-hosts-entry.sh --host <HOST> --port <PORT> --entry-file <KNOWN_HOSTS_PATH>.entry --known-hosts <KNOWN_HOSTS_PATH>
   scripts/upsert-known-hosts-entry.sh --host <HOST> --port <PORT> --entry-file <KNOWN_HOSTS_PATH>.entry --known-hosts <KNOWN_HOSTS_PATH> --apply
   ```

8. Manually add the host to the private Semaphore inventory. Inventory is not
   stored here because it discloses identities, access settings, and topology.
9. Initially assign only `homelab_managed`.
10. Run Connectivity.
11. Run Preflight.
12. Run APT Preview.
13. Deliberately choose either `update_standard` or `update_automatic` only
    after reviewing the host. New hosts never enter automatic maintenance by
    implication.

Remove temporary public-key and known-hosts entry files after onboarding. Do
not delete the private key managed by Key Store or the administrator. After a
host reinstall or host-key change, stop using the old entry, verify the new
public host key through a trusted console, rebuild the entry, test strict SSH,
and atomically replace only that host's record.
