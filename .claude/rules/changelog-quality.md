---
paths:
  - "CHANGELOG.md"
  - "release-please-config.json"
  - ".release-please-manifest.json"
---

# Changelog Quality (release-please)

release-please builds `CHANGELOG.md` from conventional commit subjects. `feat`, `fix`, `perf`, `refactor` and `docs` appear in the changelog; `chore`, `ci` and `test` don't (see `release-please-config.json`). Use `chore:` for internal work so it stays out.

A `feat!:` or `fix!:` needs a `BREAKING CHANGE:` footer that says what the user must do. Without it, release-please only uses the subject line.

## Pre-release review

Before merging a release PR, run `/changelog-review`. It edits `CHANGELOG.md` on the release-please branch to drop internal entries, expand terse ones from the PR body, and make breaking changes actionable.

## After the merge

The `sync-release-notes` job in `.github/workflows/release-please.yml` copies the release's `CHANGELOG.md` section over the GitHub Release body, replacing release-please's raw commit list. The curated section is what users read, so it has to be right before the release PR is merged.

## Do NOT edit the release PR body

Edit only `CHANGELOG.md` on the release-please branch. release-please recognizes a merged release PR by its own body format (the `:robot: I have created a release *beep* *boop*` block). A rewritten body makes it treat the merge as an ordinary commit: no tag, no GitHub Release, no zip attached.

If that happens: create the tag at the merge commit, create the GitHub Release, then run `gh workflow run "Release Please" -f tag_name=v<version>` to attach the zip and sync the notes. Close any spurious release PR that opens afterwards.
