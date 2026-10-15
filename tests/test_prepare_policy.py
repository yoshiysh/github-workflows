import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(
  0, str(Path(__file__).resolve().parents[1] / ".github/actions/claude-review")
)
import prepare


class BaseReviewPolicyTests(unittest.TestCase):
  def setUp(self) -> None:
    self.original_cwd = Path.cwd()
    self.temp_dir = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp_dir.cleanup)
    self.addCleanup(os.chdir, self.original_cwd)
    os.chdir(self.temp_dir.name)
    subprocess.run(["git", "init", "--quiet"], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], check=True)

  def commit(self, path: str) -> None:
    subprocess.run(["git", "add", path], check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "snapshot"], check=True)

  def test_missing_policy_returns_empty_text(self) -> None:
    Path("README.md").write_text("No policy yet.\n", encoding="utf-8")
    self.commit("README.md")

    self.assertEqual(prepare.base_review_policy("HEAD", Path("REVIEW.md")), "")

  def test_existing_policy_returns_base_branch_contents(self) -> None:
    policy = "Use severity labels P0 through P3.\n"
    Path("REVIEW.md").write_text(policy, encoding="utf-8")
    self.commit("REVIEW.md")

    self.assertEqual(prepare.base_review_policy("HEAD", Path("REVIEW.md")), policy)

  def test_invalid_base_ref_fails_instead_of_becoming_empty_policy(self) -> None:
    with self.assertRaisesRegex(SystemExit, "Could not inspect the base review policy"):
      prepare.base_review_policy("missing-ref", Path("REVIEW.md"))


if __name__ == "__main__":
  unittest.main()
