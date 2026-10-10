[← Back to README](../../README.md)

# Triage Labels

Canonical triage roles map directly to the `Status:` strings on implementation issues. Wayfinding child tickets use the separate `Work status:` lifecycle defined in the [issue tracker guide](issue-tracker.md).

| Role | Tracker string | Meaning |
| --- | --- | --- |
| needs-triage | needs-triage | Maintainer needs to evaluate this issue |
| needs-info | needs-info | Waiting on reporter for more information |
| ready-for-agent | ready-for-agent | Fully specified, ready for an agent |
| ready-for-human | ready-for-human | Requires human implementation |
| wontfix | wontfix | Will not be actioned |

When a skill mentions a triage role, use its tracker string in the issue file's plain `Status:` line. Keep historical completion or verification details in `Legacy outcome:` when an archived issue is marked `wontfix`.

Edit the tracker column to customize the vocabulary.

## See also

- [Issue tracker guide](issue-tracker.md) for where status is recorded.
- [Contributing](../contributing.md) for project change workflow.
