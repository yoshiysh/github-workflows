# github-workflows

Reusable GitHub Actions workflows for repositories that share a centrally maintained review pipeline. Each caller keeps its own event filters, permissions, secrets, and review policy; this repository owns the repeatable execution pipeline.

## Claude review

`.github/workflows/claude-review.yml` is one reusable workflow with an internal matrix for code and Markdown reviews. Each job selects its own changed files from the pull request diff and skips Claude when there are no files of that kind. Callers do not pass a review kind.

The caller controls when the reusable workflow starts. Its optional `paths` filter only gates the trigger; it does not select a review kind. The internal matrix always separates code from Markdown, and a triggered pull request with both kinds of changes runs both matching jobs.

The calling repository owns its root `REVIEW.md`. The shared action reads that file from the pull request's base branch, so a pull request cannot replace its own review policy. The shared workflow maintains a fixed allowlist of official documentation domains; callers cannot expand Claude's tool permissions through workflow inputs. Model inputs are checked against a model-name pattern before they reach Claude's arguments.

### Caller workflow

This example starts the shared workflow on a self-hosted Linux runner for the listed pull request events. With no `paths` filter, it can run for code-only, Markdown-only, or mixed changes:

```yaml
name: Claude review

on:
  pull_request:
    types: [opened, reopened, ready_for_review, synchronize]

permissions:
  contents: read
  pull-requests: write
  issues: write

jobs:
  review:
    # Replace this example hash with the reviewed full commit SHA from github-workflows.
    uses: yoshiysh/github-workflows/.github/workflows/claude-review.yml@0123456789abcdef0123456789abcdef01234567
    with:
      runs-on: '["self-hosted","linux"]'
    secrets:
      claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

To start only for Markdown changes, callers can add this optional filter under `pull_request`:

```yaml
paths:
  - '**.md'
  - '**.markdown'
  - '**.mdx'
```

No `kind` input is needed. If this repository is the caller, use `uses: $/.github/workflows/claude-review.yml` to select the workflow from the caller's current commit.

The job runs only for non-draft pull requests opened by a user from a branch in the calling repository. Pull requests from forks are skipped and get no review.

### Runner

The required `runs-on` input selects the runner; there is no default, so every caller states where the review runs. Its value is JSON passed through `fromJSON` to the job's `runs-on`: a quoted label string, an array of labels such as `'["self-hosted","linux"]'`, or a `group` / `labels` object such as `'{"group":"reviewers"}'`. Existing callers must add `runs-on` when they move their pin to a revision that requires it.

A public caller, or any caller without a self-hosted runner, passes a GitHub-hosted label:

```yaml
jobs:
  review:
    uses: yoshiysh/github-workflows/.github/workflows/claude-review.yml@0123456789abcdef0123456789abcdef01234567
    with:
      runs-on: '"ubuntu-latest"'
    secrets:
      claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

`'["ubuntu-latest"]'` is equivalent. A bare `ubuntu-latest` without the inner JSON quotes is not valid JSON and fails. A public repository should not route pull request code to self-hosted runners; this repository's own `claude-review-self.yml` uses `ubuntu-latest` for that reason.

### Models

Model inputs are optional. Their defaults are the `default:` values in `.github/workflows/claude-review.yml`.

| Input | Used for |
| --- | --- |
| `code-model` | `--model` for the code review job |
| `docs-model` | `--model` for the Markdown review job |
| `subagent-model` | `CLAUDE_CODE_SUBAGENT_MODEL`, the default model for the confirming agents in both jobs |

Each value must be a model ID or alias such as `claude-sonnet-5-5`, `opus[1m]`, or a provider form with `.`, `:`, `/` or `@`, and must start with a letter or digit. Values with spaces, quotes, `$`, or any other first character stop the job before Claude runs.

```yaml
    with:
      runs-on: '"ubuntu-latest"'
      code-model: claude-sonnet-5-5
      subagent-model: claude-haiku-5-5
```

### Repository setup

1. Merge a reviewed change, then pin callers to the full commit SHA of the merged revision. A tag such as `v1` can remain as a convenient moving release alias, but it is not an immutable pin.
2. In each caller repository, allow actions and reusable workflows in **Settings → Actions → General**, and, if the caller restricts which ones may run, allow `yoshiysh/github-workflows`. This repository is public, so it needs no **Access** setting for callers.
3. In each caller repository, add `CLAUDE_CODE_OAUTH_TOKEN` as a repository secret and grant the permissions shown above.
4. A self-hosted runner must be version [2.336.0](https://github.com/actions/runner/releases/tag/v2.336.0) or newer. The shared workflow uses the `$/` self-repository syntax to load its composite action from the exact workflow commit. GitHub maintains and upgrades GitHub-hosted runners.

The shared workflow resolves `.github/actions/claude-review` from its own running commit, so pinning the caller's workflow to a full SHA also pins the composite action and its helper scripts to that same revision.

## Shared components

- `.github/workflows/claude-review.yml` — reusable workflow with the internal `code` / `docs` matrix. It runs on the runner selected by the `runs-on` input and checks out the caller's repository.
- `.github/actions/claude-review/` — shared action and helper scripts for collecting diffs, prior review context, source-line copies, protected configuration snapshots, and read-coverage reports.

The reusable workflow does not define `pull_request` path filters. Callers own their event triggers and decide which pull requests start the matrix.
