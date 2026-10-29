# Include and repair Gemma chat templates

Status: wontfix
Legacy outcome: Implemented in model source preparation; existing matching-revision Gemma sources missing the template are repaired safely.

## Scope

Include the upstream `chat_template.jinja` in Gemma source downloads and repair existing matching-revision sources that predate this file, so local Transformers processor loading and preparation verification succeed.

## Acceptance criteria

- New Gemma source downloads include `chat_template.jinja`.
- A matching-revision existing source missing the template fetches it from the inspected commit; inference-verified preparation publishes it only after staged verification succeeds.
- A source with conflicting metadata or a symlink at the template target is not modified.
- Update the authoritative model-management and Gemma contracts.
- Do not run tests unless requested; perform focused static validation.

## Legacy outcome

Added the template to the Gemma allowlist and source validation. Matching-revision source repair stages a hard-linked verification copy and publishes only the fetched template after inference verification; download-only preparation publishes the pinned template. Static syntax and diff checks passed. Actual model inference was not run.
