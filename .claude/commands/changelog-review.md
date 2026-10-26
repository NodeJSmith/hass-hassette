# Changelog Review

Review and rewrite the release-please changelog PR before merging. Rules: `.claude/rules/changelog-quality.md`.

## Context

- release-please config: !`cat release-please-config.json 2>/dev/null`
- Changelog quality rule: !`cat .claude/rules/changelog-quality.md 2>/dev/null`

## Step 1: Find the release PR

Run `gh pr list --search "chore(main): release" --state open --json number,title,headRefName --limit 5`.

If none is open, stop and tell the user. If several, ask which one.

## Step 2: Checkout and read

1. Fetch and check out the release-please branch.
2. Read the new release section in `CHANGELOG.md` (the topmost `## [x.y.z]` heading).
3. List the commits in the release: `git log --oneline <prev-tag>..<base-commit>`.

## Step 3: Gather PR context

For each commit that produced an entry, fetch its PR with `gh pr view <number> --json title,body` using the `(#NNN)` in the subject. Look for what users of the integration can now do or see, what a breaking change requires of them, and whether the change is internal only.

## Step 4: Rewrite

Rewrite the new release section only. Keep the `## [x.y.z](compare-link) (date)` heading exactly as generated.

- Remove entries users never see: internal refactors, tooling, tests, dependency bumps with no effect on what users install.
- Rewrite what remains in terms of what changed for the person running Home Assistant.
- A breaking change states what changed, who is affected, and what to do. For a hassette floor bump, name the minimum hassette version and say the integration shows a repair until the server is upgraded.
- Use `### Breaking Changes` first, then topic sections, then `### Bug Fixes`. With fewer than 5 entries, a flat list is fine.
- Reference PRs as `(#NNN)`, no commit SHAs.

## Step 5: Push

Show a summary (entries removed, rewritten, breaking changes), then use `AskUserQuestion` to confirm before pushing. Commit with `docs: rewrite changelog with user-facing descriptions` and push to the release-please branch.

Edit `CHANGELOG.md` only. Never edit the PR description (see the rule).
