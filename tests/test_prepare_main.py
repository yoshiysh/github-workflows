import os
import unittest
from pathlib import Path
from unittest import mock

from support import GitRepoCase, default_env, git, output_values

import prepare
import review_options


class PrepareMainTests(GitRepoCase):
  def setUp(self) -> None:
    super().setUp()
    git("remote", "add", "origin", str(self.root))

  def run_main(self, head: str, **options: str) -> Path:
    checked = review_options.outputs({**default_env(), "KIND": "code", **options})
    settings = output_values(checked)
    runner_temp = self.temp_root / "runner"
    runner_temp.mkdir(exist_ok=True)
    env = {
      "KIND": "code",
      "OUT": ".claude-review/code",
      "GITHUB_WORKSPACE": str(self.root),
      "RUNNER_TEMP": str(runner_temp),
      "GITHUB_OUTPUT": str(runner_temp / "output"),
      "BASE_REF": "main",
      "HEAD_SHA": head,
      "REPO": "owner/repo",
      "PR": "1",
      "HEADER": "header",
      "DOC_SUFFIXES": settings["doc_suffixes"],
      "COPY_SOURCES": settings["copy_sources"],
      "EXCLUDE_PATHS": settings["exclude_paths"],
      "REVIEW_POLICY_PATH": settings["review_policy_path"],
    }
    with mock.patch.dict(os.environ, env), mock.patch.object(prepare, "prior_reviews", return_value=""):
      prepare.main()
    return self.root / ".claude-review/code"

  def test_review_policy_path_reads_that_file_from_the_base_branch(self) -> None:
    self.commit(*self.write({"REVIEW.md": "root policy\n", "docs/policy.md": "base policy\n"}))
    git("checkout", "--quiet", "-b", "feature")
    head = self.commit(*self.write({"docs/policy.md": "pull request policy\n"}))

    out = self.run_main(head, REVIEW_POLICY_PATH="docs/policy.md")

    self.assertEqual((out / "review-policy.md").read_text(encoding="utf-8"), "base policy\n")

  def test_changed_control_files_are_snapshotted_and_listed(self) -> None:
    self.commit(*self.write({".claude/x": "base rule\n", "CLAUDE.md": "base memory\n"}))
    git("checkout", "--quiet", "-b", "feature")
    head = self.commit(
      *self.write({".claude/x": "pull request rule\n", "CLAUDE.md": "pull request memory\n"})
    )

    out = self.run_main(head)

    listed = (out / "control-files.txt").read_text().splitlines()
    for path, pull_request, base in (
      (".claude/x", "pull request rule\n", "base rule\n"),
      ("CLAUDE.md", "pull request memory\n", "base memory\n"),
    ):
      with self.subTest(path=path):
        self.assertEqual((self.root / ".claude-pr" / path).read_text(), pull_request)
        self.assertEqual((self.root / path).read_text(), base)
        self.assertTrue(
          any(path == line or (line.endswith("/") and path.startswith(line)) for line in listed)
        )


if __name__ == "__main__":
  unittest.main()
