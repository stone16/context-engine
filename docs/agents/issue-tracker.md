# Issue tracker: GitHub

Issues and PRDs for this repository live in GitHub Issues at
`stone16/context-engine`. Use the `gh` CLI from this clone so the repository is
resolved from `origin`.

## Conventions

- Create a work item with `gh issue create`.
- Read the full body, labels, and comments before acting on an existing issue.
- Apply the labels defined in `docs/agents/triage-labels.md`.
- Do not close or rewrite a parent issue while creating child implementation
  issues.
- Publish blockers before dependants so dependency references use real issue
  identifiers.

## Pull requests as a triage surface

No. External pull requests are not treated as incoming feature requests by the
triage workflow. Collaborator pull requests remain ordinary implementation work.

## Skill routing

When an engineering skill says "publish to the issue tracker", create a GitHub
issue in `stone16/context-engine`. When it says "fetch the relevant ticket", read
the GitHub issue body, labels, and comments.
