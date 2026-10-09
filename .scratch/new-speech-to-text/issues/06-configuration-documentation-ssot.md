# 06. Configuration documentation single source of truth

Status: ready-for-agent
Execution: completed
Blocked by: none

## Scope

Make the Feature 01 specification the authoritative source for model and flow configuration defaults, supported options, valid ranges, and compatibility. Verify each documented contract against the current configuration validator and runtime adapter while preserving existing behavior. Remove repeated option values and ranges from the user-facing configuration and getting-started guides, while retaining unique environment-variable, setup, and usage guidance.

## Plan

1. Compare the current Feature 01 configuration contract with `config.py` and runtime adapter behavior.
2. Add a complete, accurate model and flow configuration reference to the Feature 01 spec.
3. Replace duplicated defaults and ranges in `docs/configuration.md` and `docs/getting-started.md` with links to the spec plus practical usage guidance.
4. Preserve environment-variable reference material and validate relative Markdown links and whitespace on owned files.

## Acceptance checklist

- [x] The Feature 01 spec documents each model and flow option, default, valid range or choices, and applicable runtime/adapter compatibility accurately against `config.py` and the initial runtime adapters.
- [x] User guides link to the spec as the sole detailed source for model and flow defaults/options/ranges.
- [x] Unique environment-variable documentation and setup instructions remain available.
- [x] All changed relative Markdown links and heading anchors resolve; `git diff --check` passes for tracked owned changes, and no-index whitespace checks pass for new owned files.
- [x] No unrelated files were changed by this ticket.

## Comments

Model and flow detail is centralized in the Feature 01 spec. The configuration guide retains environment variables, CLI usage, and API entry points; Getting Started links to the contract while keeping runtime installation guidance.

Validation: local relative Markdown link/anchor checker passed for the spec, this ticket, `docs/configuration.md`, and `docs/getting-started.md`. `git diff --check` reported no whitespace errors on tracked owned changes; `git diff --no-index --check /dev/null <new-owned-file>` reported no whitespace errors on the new guide files and this ticket.
