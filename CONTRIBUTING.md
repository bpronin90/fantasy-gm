# Contributing

Thanks for contributing to `bpronin90/fantasy-gm`. This guide covers the contributor-facing process:
how work is tracked, how changes are scoped and reviewed, and what must be verified
before a change lands.

## Issue And PR Workflow

| Work | Contract | Branch |
| --- | --- | --- |
| Substantive change: behavior, interfaces, data, security, CI, dependencies | GitHub issue with **Goal, Allowed Files, Acceptance Criteria, Verification** | `issue/<n>-<scope>` |
| Doc correction or small maintenance with no behavior change | PR body with `Issue: none` and the same four headings (one line each is fine) | `chore/<scope>` or `docs/<scope>` |

If you are unsure whether work is substantive, treat it as substantive.

- Branch off `main` and open the pull request against `main`. Never commit or
  push to `main` directly, and never force-push a shared branch.
- One issue has one PR. Every non-Dependabot PR body contains exactly one standalone
  line `Issue: #<n>` or `Issue: none`.
- Change only the issue's Allowed Files, plus docs your change makes untrue. If necessary
  work falls outside them, ask for a scope amendment first.
- No adjacent refactors, cleanup, renames, dependency upgrades, or redesigns. Never
  silently change requirements; raise the conflict in the issue instead.
- Stage explicit paths only (no `git add -A`, `git add .`, or `git commit -a`), and keep
  commits focused.
- In the PR, summarize what changed, why, and exactly how it was verified.

## Testing And Verification

- `python3 -m unittest discover -s tests -v` (Python 3 and Node.js, no installs). While
  working, run the affected module, for example `python3 -m unittest tests.test_phase2 -v`.
- Pull requests run `.github/workflows/ci.yml`; report local and CI results at the exact head.
- Review the full staged diff for personal data before committing. Ignore rules cannot
  detect it inside source files.
- Test failure modes, not only happy paths.
- Report exactly which checks ran and which did not. If you cannot run a check that
  would normally apply, say so in the PR and explain why. Never describe unexecuted
  integration, deployment, or live/native validation as proven.

## Review And Merge

- Substantive changes need an independent review of the exact PR head before merge;
  the author of a head cannot approve it. For issue-free doc or maintenance PRs, review
  is at the owner's discretion.
- Review findings cite `file:line` and a concrete failing input or state, and separate
  blocking defects from optional suggestions. A new commit makes earlier reviews stale.
- An agent may merge a PR only when CI passes on the exact head; for substantive changes,
  an independent reviewer who is not the head's author has posted `VERDICT=APPROVED` on
  that exact head with no unresolved blocking findings; and the PR does not deploy,
  release, migrate data, touch credentials, secrets, or repository settings, or cause
  external side effects. Green CI alone is not permission to merge.
- Only the owner merges Dependabot PRs, merges with `--admin`, or closes issues other
  than through a merged PR's `Closes #n`.
- Only the owner authorizes deploys and releases; production data, migrations, and
  schedules; credentials, secrets, and access; repository settings and branch
  protection; new paid services; and external side effects, including any n8n workflow change, execution or publication, Data Table writes, and Yahoo access. Roster transactions are never automated. Ambiguous
  approval such as "looks good" is not authorization.

## Project Rules

- GitHub is the source of truth; current definitions are `workflows/*.json`. SDK files in
  `workflows/reference/` are historical and may differ.
- Never commit credentials, authentication headers, tokens, environment files, real
  rosters, personal league snapshots, execution output, or Data Table contents. Private
  visibility does not relax this rule.
- Only fictional, clearly marked synthetic data belongs in `data/samples/`. Keep personal
  data outside Git or in ignored `data/private/`.
- Exported workflows have credentials, pinned data, runtime data, and instance webhook
  identifiers removed; behavior is retained and environment remapping is documented.
- Do not add paid AI APIs or unnecessary dependencies. Never implement automatic roster
  transactions.
- Recommendations cite sources, state evidence freshness and uncertainty, and stay
  advisory. Missing critical information blocks a report; it is never filled in by
  assumption.
- The Decision Ledger is append-only: identical retries are idempotent and conflicting
  rewrites fail. Never rewrite or delete recorded decisions.
- Report, evaluator, and ledger contracts (`src/fantasy_gm/`) are substantive to change
  and need tests for the rejection and blocked paths. A breaking schema change is
  versioned and noted in the PR.

## Public source and private configuration

The tracked `AGENTS.md`, `CLAUDE.md`, guard hook, and this guide are the complete
contributor baseline for a fresh checkout. Private agent overrides are optional;
contributors do not need the maintainer's environment or operating protocols.
Keep personal settings, service identifiers, infrastructure relationships, real
data, and full private protocols in ignored local files or a separate private
backup. Review staged content: ignore rules do not remove sensitive text inside
tracked files or earlier commits.

Use a GitHub noreply email for commits intended for publication. Keep review and
handoff examples free of personal paths and instance identifiers.

For a solo maintainer, branch protection should require PRs and passing CI with
zero required GitHub approving reviews; an author cannot approve their own PR.
This does not waive the independent agent-review and exact-head handoff rules
above. The owner may review and merge their own PR after the applicable checks;
agents must still follow the agent merge conditions. Repository protection and
visibility changes require separate owner approval.
