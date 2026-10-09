## Working rules

### Language

Write code, comments, file names, and documentation in English. Respond to the user in Thai. Preserve Thai text when it is application data, such as transcripts, keywords, or test fixtures.

### Documentation: single source of truth (SSOT)

Follow these steps in order whenever writing or updating documentation:

1. Identify the topic and information to document.
2. Scan all project documentation, including root documents, `docs/`, feature documentation, and `.scratch/`, to determine whether that information already exists and locate its authoritative source.
3. Update the original authoritative file when the topic already exists. Add new documentation only when no existing source covers the topic. Reference the authoritative source from other documents instead of duplicating its content.

### Modular programming and feature-based folders

Organize program code into folders by feature. Each feature module exposes a callable interface for other systems, with explicit inputs, outputs, errors, and configuration. Give feature-specific settings documented defaults and let callers adjust them through configuration. Keep implementation details behind the interface, separate responsibilities into cohesive modules, and place genuinely shared code in shared modules. Apply this structure to new code and changes within the task's scope.

### Feature development through sub-agents

Develop each documented feature through a separate sub-agent assigned to that feature's specification and acceptance criteria. The parent agent coordinates work, reviews the result, and verifies integration.

Use `gpt-6-luna` with `high` reasoning effort for every feature-development sub-agent unless the user explicitly selects another model or reasoning effort. When spawning with this default, set `model="gpt-6-luna"`, `reasoning_effort="high"`, and `fork_turns="none"`; include the relevant specification, repository rules, scope, and validation requirements in the task message. Use the user's override for its stated scope. If the required model is unavailable, report that limitation before using a different model.

## Agent skills

### Legacy codebase

The previous PoC lives in `legacies-poc/`. Run its commands from that directory. Keep new application code outside the archive; modify legacy code only when the task explicitly requires it. Repository-wide agent configuration and issues remain at the root.

### Issue tracker

Issues live as markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the five default triage labels. See `docs/agents/triage-labels.md`.

### Domain docs

Read domain vocabulary and ADRs for the codebase being changed. See `docs/agents/domain.md`.
