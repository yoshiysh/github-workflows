import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(
  0, str(Path(__file__).resolve().parents[1] / ".github/actions/claude-review")
)
import prepare
import review_options
from workflow_defaults import default_env


def git(*args: str) -> str:
  return subprocess.run(
    ["git", *args], capture_output=True, text=True, check=True
  ).stdout.strip()


class PrepareMainTests(unittest.TestCase):
  def setUp(self) -> None:
    self.original_cwd = Path.cwd()
    self.temp_dir = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp_dir.cleanup)
    self.addCleanup(os.chdir, self.original_cwd)
    self.root = Path(self.temp_dir.name) / "repo"
    self.root.mkdir()
    os.chdir(self.root)
    git("init", "--quiet", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    git("remote", "add", "origin", str(self.root))

  def commit(self, files: dict[str, str]) -> str:
    for path, text in files.items():
      Path(path).parent.mkdir(parents=True, exist_ok=True)
      Path(path).write_text(text, encoding="utf-8")
    git("add", *files)
    git("commit", "--quiet", "-m", "snapshot")
    return git("rev-parse", "HEAD")

  def run_main(self, head: str, **options: str) -> Path:
    checked = review_options.outputs({**default_env(), "KIND": "code", **options})
    settings = dict(line.split("=", 1) for line in checked.splitlines())
    runner_temp = Path(self.temp_dir.name) / "runner"
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
      "SOURCE_SUFFIXES": settings["source_suffixes"],
      "COPY_IGNORE_PREFIXES": settings["copy_ignore_prefixes"],
      "EXCLUDE_PATHS": settings["exclude_paths"],
      "REVIEW_POLICY_PATH": settings["review_policy_path"],
    }
    with mock.patch.dict(os.environ, env), mock.patch.object(prepare, "prior_reviews", return_value=""):
      prepare.main()
    return self.root / ".claude-review/code"

  def test_review_policy_path_reads_that_file_from_the_base_branch(self) -> None:
    self.commit({"REVIEW.md": "root policy\n", "docs/policy.md": "base policy\n"})
    git("checkout", "--quiet", "-b", "feature")
    head = self.commit({"docs/policy.md": "pull request policy\n"})

    out = self.run_main(head, REVIEW_POLICY_PATH="docs/policy.md")

    self.assertEqual((out / "review-policy.md").read_text(encoding="utf-8"), "base policy\n")

  def test_changed_control_files_are_snapshotted_and_listed(self) -> None:
    self.commit({".claude/x": "base rule\n", "CLAUDE.md": "base memory\n"})
    git("checkout", "--quiet", "-b", "feature")
    head = self.commit({".claude/x": "pull request rule\n", "CLAUDE.md": "pull request memory\n"})

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
