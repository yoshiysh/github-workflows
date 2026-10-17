import unittest
from pathlib import Path

from support import GitRepoCase, default_env

import prepare
import review_options

REPEATED = 'const message = `Unsupported review kind: ${kind}`;'


class RepoCase(GitRepoCase):
  def collect(self, kind: str = "code", **values: str) -> tuple:
    settings = review_options.review_settings({**default_env(), **values})
    diff_dir = Path("diff")
    diff_dir.mkdir()
    rows, excluded, symlinks, lines = prepare.collect(
      kind,
      f"{self.base}...HEAD",
      diff_dir,
      Path("unused.attributes"),
      doc_suffixes=tuple(settings["doc_suffixes"]),
      source_suffixes=tuple(review_options.ignore_prefixes(settings["copy_sources"])),
      exclude_paths=settings["exclude_paths"],
    )
    return rows, excluded, symlinks, lines, settings

  def copies_after(self, path: str, added: str) -> list[str]:
    Path(path).write_text(added, encoding="utf-8")
    self.commit(path)
    _rows, _excluded, _symlinks, lines, settings = self.collect()
    prefixes = review_options.ignore_prefixes(settings["copy_sources"])
    return prepare.copies(
      lines, prepare.tracked_sources("HEAD", lines, tuple(prefixes)), prefixes
    )


class SourceCopyTests(RepoCase):
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

  def test_repeated_private_member_line_in_mjs_is_reported(self) -> None:
    line = "#cache = new Map(Object.entries(defaultReviewOptions));"
    Path("a.mjs").write_text(f"class A {{\n  {line}\n}}\n")
    self.base = self.commit("a.mjs")

    rows = self.copies_after("b.mjs", f"class B {{\n  {line}\n}}\n")

    self.assertEqual(rows, [f"2\ta.mjs:2 +b.mjs:2\t{line}"])

  def test_repeated_rust_attribute_line_is_not_reported(self) -> None:
    line = "#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]"
    Path("a.rs").write_text(f"{line}\nstruct A;\n")
    self.base = self.commit("a.rs")

    self.assertEqual(self.copies_after("b.rs", f"{line}\nstruct B;\n"), [])

  def test_repeated_comment_line_in_python_is_not_reported(self) -> None:
    line = "# The base branch decides which review policy applies here."
    Path("a.py").write_text(f"{line}\nA = 1\n")
    self.base = self.commit("a.py")

    self.assertEqual(self.copies_after("b.py", f"{line}\nB = 2\n"), [])


class ExcludePathTests(RepoCase):
  def test_matching_paths_are_listed_in_excluded_with_the_pattern(self) -> None:
    Path("keep.py").write_text("print('base')\n")
    self.base = self.commit("keep.py")
    Path("vendor/lib").mkdir(parents=True)
    Path("vendor/lib/x.py").write_text("print('vendored')\n")
    Path("app.generated.ts").write_text("export const x = 1;\n")
    Path("keep.py").write_text("print('changed')\n")
    self.commit("vendor/lib/x.py", "app.generated.ts", "keep.py")

    rows, excluded, _symlinks, _lines, _settings = self.collect(
      EXCLUDE_PATHS='["vendor/**","**/*.generated.ts"]'
    )

    self.assertEqual([row.split("\t", 1)[0] for row in rows], ["keep.py"])
    self.assertEqual(
      sorted(excluded),
      [
        "app.generated.ts\texclude-paths: **/*.generated.ts",
        "vendor/lib/x.py\texclude-paths: vendor/**",
      ],
    )


class DefaultSelectionTests(RepoCase):
  def changed(self, *paths: str) -> None:
    Path("base.txt").write_text("base\n")
    self.base = self.commit("base.txt")
    for path in paths:
      Path(path).parent.mkdir(parents=True, exist_ok=True)
      Path(path).write_text("changed\n")
    self.commit(*paths)

  def test_uppercase_markdown_suffix_goes_to_the_docs_job(self) -> None:
    self.changed("README.MD", "main.py")

    docs, *_ = self.collect("docs")
    self.assertEqual([row.split("\t", 1)[0] for row in docs], ["README.MD"])

  def test_lockfiles_are_excluded_by_default(self) -> None:
    self.changed("Cargo.lock", "web/package-lock.json", "web/pnpm-lock.yaml", "main.py")

    rows, excluded, *_ = self.collect()

    self.assertEqual([row.split("\t", 1)[0] for row in rows], ["main.py"])
    self.assertEqual(
      sorted(line.split("\t", 1)[0] for line in excluded),
      ["Cargo.lock", "web/package-lock.json", "web/pnpm-lock.yaml"],
    )


if __name__ == "__main__":
  unittest.main()
