# github-workflows

Reusable GitHub Actions workflows for repositories that share a centrally maintained review pipeline. Each caller keeps its own event filters, permissions, secrets, and review policy; this repository owns the repeatable execution pipeline.

## Claude review

`.github/workflows/claude-review.yml` is one reusable workflow with an internal matrix for code and documentation reviews. Each job selects its own changed files from the pull request diff and skips Claude when there are no files of that kind. Callers do not pass a review kind.

The caller controls when the reusable workflow starts. Its optional `paths` filter only gates the trigger; it does not select a review kind. The internal matrix always separates code from documentation, and a triggered pull request with both kinds of changes runs both matching jobs.

The calling repository owns its review policy file (`review-policy-path`). The shared action reads that file from the pull request's base branch, so the policy that applies does not change with the pull request's contents. A pull request that changes the caller workflow itself, including its inputs, still needs its own review.

Callers can add checked documentation hostnames to Claude's WebFetch allowlist, but cannot add tool kinds or widen any other permission. Every caller-supplied value is checked by the action's first step before it reaches the review scripts or Claude's arguments; a value that fails the check stops the job.

### Caller workflow

This example starts the shared workflow on a GitHub-hosted runner for the listed pull request events. With no `paths` filter, it can run for code-only, documentation-only, or mixed changes. The values under `with:` are examples for a repository with Markdown documentation, Python code, and lockfiles; the shared workflow has no defaults for them, and each caller states its own:

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
      runs-on: '"ubuntu-latest"'
      doc-domains: '["docs.python.org"]'
      doc-sources: '[{"suffixes":[".md",".markdown",".mdx"],"diff-driver":"markdown"}]'
      copy-sources: '[{"suffixes":[".py"],"ignore":["#","import ","from "]}]'
      exclude-paths: '["**/*.lock"]'
      review-policy-path: REVIEW.md
    secrets:
      claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

To start only for documentation changes with the example `doc-sources` above, callers can add this optional filter under `pull_request`, with patterns that match their own `doc-sources` suffixes:

```yaml
paths:
  - '**.md'
  - '**.markdown'
  - '**.mdx'
```

No `kind` input is needed. If this repository is the caller, use `uses: $/.github/workflows/claude-review.yml` to select the workflow from the caller's current commit. This repository's own review policy is its root `REVIEW.md`.

A job runs only for a `pull_request` event, and only when that event was triggered by a user rather than a bot (for `synchronize`, the user who pushed; for `ready_for_review`, the user who marked it ready), the pull request is not a draft, and its head branch is in the calling repository. Pull requests from forks are skipped and get no review.

Each job posts its summary as a new pull request comment, so a re-run (for example on `synchronize`) adds another one. After posting, the job hides that kind's earlier summaries written by the same token as outdated, leaving other comments alone. A hide that fails is reported as a workflow warning and does not fail the job. When Claude writes no summary, the job posts a failure notice, fails, and leaves the earlier summaries visible.

### Runner

The required `runs-on` input selects the runner; there is no default, so every caller states where the review runs. Its value is JSON passed through `fromJSON` to the job's `runs-on`: a quoted label string, an array of labels such as `'["self-hosted","linux"]'`, or a `group` / `labels` object such as `'{"group":"reviewers"}'`.

The caller example above uses `'"ubuntu-latest"'`; `'["ubuntu-latest"]'` is equivalent. A bare `ubuntu-latest` without the inner JSON quotes is not valid JSON and fails. A private caller with its own self-hosted runner passes its labels instead; the other required inputs are the same kind of example values as above:

```yaml
jobs:
  review:
    uses: yoshiysh/github-workflows/.github/workflows/claude-review.yml@0123456789abcdef0123456789abcdef01234567
    with:
      runs-on: '["self-hosted","linux"]'
      doc-domains: '[]'
      doc-sources: '[{"suffixes":[".md"],"diff-driver":"markdown"}]'
      copy-sources: '[{"suffixes":[".go"],"ignore":["//","/*","import "]}]'
      exclude-paths: '[]'
      review-policy-path: docs/REVIEW.md
    secrets:
      claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

A public repository should not route pull request code to self-hosted runners; this repository's own `claude-review-self.yml` uses `ubuntu-latest` for that reason.

### Inputs

Inputs that describe the caller's repository — what to read and how to classify it — are required and have no default, so every caller declares its own stack. Inputs that tune how the review runs are optional; their defaults are the `default:` values in `.github/workflows/claude-review.yml`. List inputs take a JSON array as a string, like `runs-on`, and `[]` is an accepted explicit value for every list input. The accepted formats are enforced by `.github/actions/claude-review/review_options.py`.

| Input | Required | Used for | Accepted values |
| --- | --- | --- | --- |
| `runs-on` | Yes | The job's runner (see [Runner](#runner)) | JSON label string, label array, or `group` / `labels` object |
| `doc-domains` | Yes | Hostnames added to the shared WebFetch allowlist (`FIXED_DOC_DOMAINS` in `review_options.py`); `[]` adds none | Lowercase DNS hostnames; no scheme, path, port, wildcard, or IP address |
| `doc-sources` | Yes | File suffixes reviewed by the docs job, grouped with the Git diff driver that divides those files into sections; every other changed file goes to the code job, so with `[]` the docs job finds no files and skips Claude | A JSON array of groups, each `{"suffixes": [...], "diff-driver": ...}` with only those two keys: `suffixes` is a non-empty array of `.` followed by lowercase letters or digits, and `diff-driver` is a built-in Git diff driver name (`DIFF_DRIVERS` in `review_options.py`) or `null`. A suffix may appear in only one group and not in `copy-sources` |
| `copy-sources` | Yes | Code files whose added lines are compared for `copies.tsv`, and the line prefixes ignored in each, such as that language's comments and imports | A JSON array of groups, each `{"suffixes": [...], "ignore": [...]}` with only those two keys: `suffixes` is a non-empty array of file suffixes (`.` followed by letters or digits, matched case-sensitively), and `ignore` is an array of prefixes (non-empty text without tabs, newlines, or other control characters). A suffix may appear in only one group; `[]` leaves `copies.tsv` empty |
| `exclude-paths` | Yes | Changed paths left out of review and listed in `excluded.tsv` with the matching pattern; `[]` excludes none | Glob patterns built from literal characters, `*`, `?` and `**`; no `[` or `]`, no leading or trailing `/`, no empty, `.` or `..` segments, no control characters, and at most `MAX_TEXT_LENGTH` (in `review_options.py`) characters |
| `review-policy-path` | Yes | The review policy file read from the base branch | A repository-relative file path: no leading or trailing `/`, no empty, `.` or `..` segments, no control characters, and at most `MAX_TEXT_LENGTH` (in `review_options.py`) characters |
| `code-model` | No | `--model` for the code review job | Model ID or alias |
| `docs-model` | No | `--model` for the docs review job | Model ID or alias |
| `code-effort` | No | `CLAUDE_CODE_EFFORT_LEVEL` for the code review job | A level that [`CLAUDE_CODE_EFFORT_LEVEL` accepts](https://code.claude.com/docs/en/model-config) |
| `docs-effort` | No | `CLAUDE_CODE_EFFORT_LEVEL` for the docs review job | Same as `code-effort` |
| `subagent-model` | No | `CLAUDE_CODE_SUBAGENT_MODEL`, the default model for the confirming agents in both jobs | Model ID or alias |
| `timeout-minutes` | No | The job's `timeout-minutes` | Whole number from 1 to `MAX_TIMEOUT_MINUTES` in `review_options.py` |

Each job uses only the model and effort inputs for its own kind; an empty or rejected value stops that job instead of falling back to the other kind's value. A model value must start with a letter or digit and may contain letters, digits, `.`, `_`, `:`, `/`, `@`, `-`, and one trailing `[...]` such as `opus[1m]`; spaces, quotes, and `$` are rejected.

`exclude-paths` patterns are matched against the whole repository-relative path, case-sensitively, in time proportional to the pattern length times the path length. `*` matches any characters except `/`, `?` matches one such character, and `**` as a whole segment matches zero or more directories (one or more segments when it is the last segment), so `*.lock` matches only at the repository root, `**/*.lock` matches at any depth, and `vendor/**` matches everything under `vendor/`. These are the results of Python's [`PurePath.full_match`](https://docs.python.org/3.14/library/pathlib.html#pathlib.PurePath.full_match) for the same patterns; brackets are not supported. Lockfiles are reviewed unless a pattern such as `**/*.lock` excludes them. Excluded paths are still read as comparison sources for `copies.tsv`.

Each `copy-sources` group gives one language's suffixes and prefixes, so a prefix such as `#` can mark a comment in `.py` files without hiding JavaScript private members (`#cache = …`) in `.mjs` files. Only files whose suffix is listed in some group are compared.

Every checked value, such as a serialized list input, also has a size limit (`MAX_OUTPUT_BYTES` in `review_options.py`), because each checked value is passed to the next step as one environment variable and Linux limits a single environment string to 128 KiB; a larger value stops the job with an error.

For a `doc-sources` group with a `diff-driver`, the docs job writes `diff=<driver>` for its suffixes (in either ASCII case) to a Git attributes file and diffs those files with `--function-context`, so each changed section is included whole, as that built-in driver divides the file (see "Defining a custom hunk-header" in [gitattributes](https://git-scm.com/docs/gitattributes/2.55.0)). A group with `null` gets a plain diff, as code files do. Only built-in driver names are accepted; a caller cannot supply its own section pattern. The accepted names are the built-in drivers of Git 2.55.0; with a Git from 2.41 up to but not including 2.55.0, a driver that Git does not know falls back to Git's default section boundaries.

Every `git diff` the action runs passes Git's [`--attr-source`](https://git-scm.com/docs/git/2.55.0) an empty tree, so it does not read the `.gitattributes` files in the repository's tree, from the pull request or the base branch; they cannot change a driver, mark a file `-diff` to hide it as binary, or otherwise change the diffs. The action also turns off the system-wide attributes file (`GIT_ATTR_NOSYSTEM`), stops with an error when the checkout has a `$GIT_DIR/info/attributes` file (which would take precedence), and runs `git diff` with `--no-textconv` and `--text`, so repository Git configuration cannot convert a file or mark it binary. The action decides binary files itself: a changed file whose blob on either side contains a NUL byte anywhere is listed in `excluded.tsv` as `binary`, whatever its suffix, driver, attributes, or Git configuration. A driver's section pattern (`diff.<driver>.xfuncname`) in the checkout's Git configuration still applies. Use `exclude-paths` to leave generated files out of review. Doc suffixes are matched ignoring ASCII case, so a `copy-sources` suffix that equals a doc suffix in any case is rejected: those files would always go to the docs job.

`timeout-minutes` is applied by GitHub to the job before any step runs, so the range check can only fail the job once it has started. The upper bound is the 6-hour job limit for GitHub-hosted runners in [Actions limits](https://docs.github.com/en/actions/reference/limits), which keeps one value valid on both runner types.

This example, again with illustrative values, also tunes the optional inputs:

```yaml
    with:
      runs-on: '"ubuntu-latest"'
      doc-domains: '["docs.python.org","nodejs.org"]'
      doc-sources: '[{"suffixes":[".md"],"diff-driver":"markdown"},{"suffixes":[".rst"],"diff-driver":null}]'
      copy-sources: '[{"suffixes":[".py"],"ignore":["#","import ","from "]},{"suffixes":[".js",".mjs"],"ignore":["//","/*","*","import "]}]'
      exclude-paths: '["**/*.lock","**/package-lock.json","vendor/**"]'
      review-policy-path: REVIEW.md
      code-model: claude-sonnet-5-5
      code-effort: medium
```

### Recipes

These are values to copy and adapt, not defaults; the shared workflow applies none of them unless a caller passes them. Combine the groups for the languages a repository uses into one array.

`copy-sources` groups for common languages:

```json
[
  {"suffixes": [".py", ".pyi"], "ignore": ["#", "import ", "from "]},
  {"suffixes": [".rs"], "ignore": ["//", "/*", "*", "#", "use ", "pub use ", "pub(crate) use "]},
  {"suffixes": [".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx"], "ignore": ["//", "/*", "*", "#!", "import ", "} from ", "export * ", "export {"]},
  {"suffixes": [".go"], "ignore": ["//", "/*", "import "]},
  {"suffixes": [".sh", ".bash"], "ignore": ["#"]}
]
```

| Language | Prefix | Lines it leaves out |
| --- | --- | --- |
| Python | `#` | Comments |
| Python | `import `, `from ` | `import x` and `from x import y` |
| Rust | `//` | Line and doc comments (`//`, `///`, `//!`) |
| Rust | `/*`, `*` | Block comments and their continuation lines. `*` also hides any other line that starts with `*`, such as a dereference like `*count += 1;` |
| Rust | `#` | Attributes such as `#[derive(...)]` and `#![...]` |
| Rust | `use `, `pub use `, `pub(crate) use ` | Imports and re-exports |
| JavaScript / TypeScript | `//`, `/*`, `*` | Line comments, block and JSDoc comments, and their continuation lines. `*` also hides any other line that starts with `*`, such as a generator method like `*[Symbol.iterator]() {` |
| JavaScript / TypeScript | `#!` | A shebang line. A bare `#` is left out of this group because it would also hide private members such as `#cache = …` |
| JavaScript / TypeScript | `import ` | Imports |
| JavaScript / TypeScript | `} from `, `export * `, `export {` | The closing line of a multi-line import or re-export, `export * from …`, and export lists |
| Go | `//`, `/*` | Line and block comments |
| Go | `import ` | Single-line imports. Lines inside an `import ( … )` block start with a path or alias and cannot be told apart by prefix |
| Shell | `#` | Comments, including the shebang |

`exclude-paths` patterns for lockfiles and generated files:

```json
["**/*.lock", "**/package-lock.json", "**/pnpm-lock.yaml", "**/go.sum", "**/*.pb.go", "**/*_pb2.py", "**/*_pb2_grpc.py", "dist/**"]
```

`**/*.lock` covers lockfiles named `*.lock` at any depth, such as `Cargo.lock`, `yarn.lock`, `poetry.lock`, `uv.lock`, and `Gemfile.lock`. `**/package-lock.json` and `**/pnpm-lock.yaml` are the npm and pnpm lockfiles, `**/go.sum` holds Go module checksums, `**/*.pb.go`, `**/*_pb2.py`, and `**/*_pb2_grpc.py` are protocol buffer and gRPC output, and `dist/**` is the build output directory `dist/` at the repository root (use `**/dist/**` to leave out nested `dist/` directories too).

`doc-sources` groups for Markdown and reStructuredText:

```json
[
  {"suffixes": [".md", ".markdown"], "diff-driver": "markdown"},
  {"suffixes": [".rst"], "diff-driver": null}
]
```

Git has a built-in `markdown` driver, so Markdown diffs include whole sections. Git has no built-in reStructuredText driver, so `.rst` files get plain diffs.

### Repository setup

1. Merge a reviewed change, then pin callers to the full commit SHA of the merged revision. A tag such as `v1` can remain as a convenient moving release alias, but it is not an immutable pin.
2. In each caller repository, allow actions and reusable workflows in **Settings → Actions → General**, and, if the caller restricts which ones may run, allow `yoshiysh/github-workflows`. This repository is public, so it needs no **Access** setting for callers.
3. In each caller repository, add `CLAUDE_CODE_OAUTH_TOKEN` as a repository secret and grant the permissions shown above.
4. A self-hosted runner must be version [2.336.0](https://github.com/actions/runner/releases/tag/v2.336.0) or newer and have Git 2.41 or newer, the first release with `--attr-source` ([release notes](https://github.com/git/git/blob/v2.41.0/Documentation/RelNotes/2.41.0.txt)); with an older Git the action stops with an error instead of reviewing, because it could not keep the pull request's `.gitattributes` out of the diffs. The shared workflow uses the `$/` self-repository syntax to load its composite action from the exact workflow commit. GitHub maintains and upgrades GitHub-hosted runners.

The shared workflow resolves `.github/actions/claude-review` from its own running commit, so pinning the caller's workflow to a full SHA also pins the composite action and its helper scripts to that same revision.

## Shared components

- `.github/workflows/claude-review.yml` — reusable workflow with the internal `code` / `docs` matrix. It runs on the runner selected by the `runs-on` input and checks out the caller's repository.
- `.github/actions/claude-review/` — shared action and helper scripts for collecting diffs, prior review context, source-line copies, protected configuration snapshots, and read-coverage reports.

The reusable workflow does not define `pull_request` path filters. Callers own their event triggers and decide which pull requests start the matrix.
