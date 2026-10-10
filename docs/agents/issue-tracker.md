[← Back to README](../../README.md)

# Issue tracker: Local Markdown

Issues and specs for this repo live as markdown files in `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The spec is `.scratch/<feature-slug>/spec.md`
- Implementation issues are one file per ticket at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01` with a unique number within the feature, never a single combined tickets file
- Triage state is recorded as a plain `Status:` line near the top of each implementation issue and uses one of the five values in `triage-labels.md`
- When preserving a retired issue's historical completion or verification state, record it as `Legacy outcome:` and use `Status: wontfix` to show that the archived work will not be actioned in the current application
- Comments and conversation history append to the bottom of the file under a `## Comments` heading

## When a skill says "publish to the issue tracker"

Create a new file under `.scratch/<feature-slug>/` (creating the directory if needed).

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path. The user will normally pass the path or the issue number directly.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a file with one **child** file per ticket.

- **Map**: `.scratch/<effort>/map.md` (the Notes / Decisions-so-far / Fog body).
- **Child ticket**: `.scratch/<effort>/issues/NN-<slug>.md`, numbered from `01` with a unique number within the effort, with the question in the body. A `Type:` line records the ticket type (`research`/`prototype`/`grilling`/`task`); a `Work status:` line records `open`/`claimed`/`resolved`.
- **Blocking**: a `Blocked by: NN, NN` line near the top. A ticket is unblocked when every file it lists has `Work status: resolved`.
- **Frontier**: scan `.scratch/<effort>/issues/` for files with `Work status: open` that are unblocked; first by number wins.
- **Claim**: set `Work status: claimed` and save before any work.
- **Resolve**: append the answer under an `## Answer` heading, set `Work status: resolved`, then append a context pointer (gist + link) to the map's Decisions-so-far in `map.md`.

## See also

- [Contributing](../contributing.md) for documentation and feature change guidance.
- [Triage labels](triage-labels.md) for issue status values.
