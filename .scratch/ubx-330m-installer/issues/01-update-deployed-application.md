# 01. Update an existing UBX-330M deployment

Status: ready-for-agent

## Request

Add one Bash command for updating the application already deployed on the UBX-330M.

## Acceptance criteria

- [x] Add executable `scripts/update_ubx330m.sh`, intended to run from the cloned checkout on the UBX-330M.
- [x] Require a clean Git checkout with a configured upstream and fast-forward the current branch before updating; stop before changing the checkout if it is dirty or cannot be fast-forwarded.
- [x] Refuse to update unless the installer-managed `data/ubx330m/compose.yaml` exists, so this command cannot silently act as a first-time installer.
- [x] Delegate the build, model/dependency cache reuse, Compose regeneration, restart, and readiness checks to `scripts/install_ubx330m.sh` instead of duplicating installer logic.
- [x] Preserve the existing profile database and local model/cache state.
- [x] Update the authoritative `docs/deployment.md` with the update command and behavior.
- [x] Validate shell syntax and whitespace; do not run deployment on the developer workstation or UBX-330M as part of code verification.

## Non-goals

- Do not add package-manager/OS upgrades, host cleanup, Docker pruning, backups, or rollback automation.
- Do not change the installer acceptance or deployment target.
