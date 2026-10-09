## Working rules

### Language

Write code, comments, file names, and documentation in English. Respond to the user in Thai. Preserve Thai text when it is application data, such as transcripts, keywords, or test fixtures.

### Documentation: single source of truth (SSOT)

Follow these steps in order whenever writing or updating documentation:

1. Identify the topic and information to document.
2. Scan all project documentation, including root documents, `docs/`, feature documentation, and `.scratch/`, to determine whether that information already exists and locate its authoritative source.
3. Update the original authoritative file when the topic already exists. Add new documentation only when no existing source covers the topic. Reference the authoritative source from other documents instead of duplicating its content.

### Modular programming and feature-based folders

Organize program code into folders by feature. Within each feature, separate responsibilities into cohesive modules with clear interfaces. Keep feature-specific logic in its owning feature and place genuinely shared code in shared modules. Apply this structure to new code and changes within the task's scope.

## Agent skills

### Legacy codebase

The previous PoC lives in `legacies-poc/`. Run its commands from that directory. Keep new application code outside the archive; modify legacy code only when the task explicitly requires it. Repository-wide agent configuration and issues remain at the root.

### Issue tracker

Issues live as markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the five default triage labels. See `docs/agents/triage-labels.md`.

### Domain docs

Read domain vocabulary and ADRs for the codebase being changed. See `docs/agents/domain.md`.
