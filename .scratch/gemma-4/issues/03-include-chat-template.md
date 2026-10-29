# Include and repair Gemma chat templates

Status: wontfix

## Scope

Include the upstream `chat_template.jinja` in Gemma source downloads and repair existing matching-revision sources that predate this file, so local Transformers processor loading and preparation verification succeed.

## Acceptance criteria

- New Gemma source downloads include `chat_template.jinja`.
- A matching-revision existing source missing the template fetches it from the inspected commit; inference-verified preparation publishes it only after staged verification succeeds.
- A source with conflicting metadata or a symlink at the template target is not modified.
- Update the authoritative model-management and Gemma contracts.
- Do not run tests unless requested; perform focused static validation.

## Legacy outcome

Added the template to the Gemma allowlist and source validation. Matching-revision source repair stages a hard-linked verification copy and publishes only the fetched template after inference verification; download-only preparation publishes the pinned template. The adapter also passes prompt token IDs to `parse_response` and extracts Gemma's parsed `content`. Focused regression checks passed. On the UBX-330M, E2B BF16 preparation and inference both completed; a 29-second WAV produced a Thai transcript with RTF 2.46. The UBX run used the model-management API and the deployed container remained healthy.
