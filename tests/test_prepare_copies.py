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

REPEATED = 'const message = `Unsupported review kind: ${kind}`;'


class SourceCopyTests(unittest.TestCase):
  def setUp(self) -> None:
    self.original_cwd = Path.cwd()
    self.temp_dir = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp_dir.cleanup)
    self.addCleanup(os.chdir, self.original_cwd)
    os.chdir(self.temp_dir.name)
    subprocess.run(["git", "init", "--quiet"], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], check=True)

  def commit(self, *paths: str) -> str:
    subprocess.run(["git", "add", *paths], check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "snapshot"], check=True)
    return subprocess.run(
      ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()

  def copies_after(self, path: str, added: str) -> list[str]:
    Path(path).write_text(added, encoding="utf-8")
    self.commit(path)
    diff_dir = Path("diff")
    diff_dir.mkdir()
    _rows, _excluded, _symlinks, lines = prepare.collect(
      "code", f"{self.base}...HEAD", diff_dir, Path("unused.attributes")
    )
    return prepare.copies(lines, prepare.tracked_sources("HEAD", lines))

  def test_repeated_mjs_line_is_reported(self) -> None:
    Path("a.mjs").write_text(f"export function a(kind) {{\n  {REPEATED}\n}}\n")
    self.base = self.commit("a.mjs")

    rows = self.copies_after("b.mjs", f"function b(kind) {{\n  {REPEATED}\n}}\n")

    self.assertEqual(rows, [f"2\ta.mjs:2 +b.mjs:2\t{REPEATED}"])

  def test_repeated_reexport_line_is_not_reported(self) -> None:
    reexport = '} from "./runtime/source-loader-with-a-long-name.mjs";'
    Path("a.mjs").write_text(f"export {{\n  load,\n{reexport}\n")
    self.base = self.commit("a.mjs")

    rows = self.copies_after("b.mjs", f"import {{\n  load,\n{reexport}\n")

    self.assertEqual(rows, [])

  def test_repeated_export_list_reexport_is_not_reported(self) -> None:
    line = 'export { loadSource, runSource } from "./runtime/source-loader.mjs";'
    Path("a.mjs").write_text(f"{line}\n")
    self.base = self.commit("a.mjs")

    self.assertEqual(self.copies_after("b.mjs", f"{line}\n"), [])

  def test_repeated_export_star_reexport_is_not_reported(self) -> None:
    line = 'export * from "./runtime/source-loader-with-a-long-name.mjs";'
    Path("a.mjs").write_text(f"{line}\n")
    self.base = self.commit("a.mjs")

    self.assertEqual(self.copies_after("b.mjs", f"{line}\n"), [])

  def test_repeated_export_definitions_are_reported(self) -> None:
    function = "export function normalizeReviewKind(kind, fallback) {"
    constant = 'export const DEFAULT_REVIEW_KIND_LABEL = "code-review";'
    Path("a.mjs").write_text(f"{function}\n}}\n{constant}\n")
    self.base = self.commit("a.mjs")

    rows = self.copies_after("b.mjs", f"{function}\n}}\n{constant}\n")

    self.assertEqual(
      rows,
      [f"2\ta.mjs:3 +b.mjs:3\t{constant}", f"2\ta.mjs:1 +b.mjs:1\t{function}"],
    )


class DocDomainOutputTests(unittest.TestCase):
  def test_prompt_and_allowed_tools_list_the_same_domains(self) -> None:
    lines = dict(
      line.split("=", 1) for line in prepare.doc_domain_outputs().splitlines()
    )

    prose = lines["doc_domains"].replace(", and ", ", ").split(", ")
    tools = [
      tool.removeprefix("WebFetch(domain:").removesuffix(")")
      for tool in lines["webfetch_tools"].split(",")
    ]
    self.assertEqual(prose, list(prepare.DOC_DOMAINS))
    self.assertEqual(tools, list(prepare.DOC_DOMAINS))


if __name__ == "__main__":
  unittest.main()
