# Add explicit UBX deployment flavor switching commands

Status: wontfix

## Scope

Implement the deployment lifecycle in the [specification](../spec.md): add separate OpenVINO deploy, Gemma deploy, and service stop scripts; add the UBX Gemma Compose override; and update deployment documentation and the project structure listing.

## Acceptance criteria

- Fulfill the safety, persistence, exclusivity, and operator documentation requirements in [spec.md](../spec.md).
- Keep the OpenVINO installer and existing deployment defaults intact.
- Follow root `AGENTS.md`, especially the `uv`, documentation SSOT, and project structure rules.
- Keep code and documentation in English; do not edit `legacies-poc/`.
- Do not add or run tests. Run `bash -n` for the new scripts, Compose config validation if feasible, and `git diff --check`.

## Legacy outcome

Implemented explicit OpenVINO/Gemma deployment and stop scripts, the UBX Gemma network/data Compose override, and operator documentation. Shell syntax and Gemma Compose merging were checked; no tests or deployment runs were performed.
