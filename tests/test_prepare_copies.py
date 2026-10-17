import unittest
from pathlib import Path

from support import GitRepoCase, default_env, git

import prepare
import review_options

REPEATED = 'const message = `Unsupported review kind: ${kind}`;'


class RepoCase(GitRepoCase):
  def collect(self, kind: str = "code", **values: str) -> tuple:
    settings = review_options.review_settings({**default_env(), **values})
    drivers = review_options.diff_drivers(settings["doc_sources"])
    diff_dir = self.temp_root / f"diff-{kind}"
    diff_dir.mkdir(exist_ok=True)
    attributes = self.temp_root / "doc-sources.attributes"
    attributes.write_text(prepare.attribute_lines(drivers))
    rows, excluded, symlinks, lines = prepare.collect(
      kind,
      f"{self.base}...HEAD",
      diff_dir,
      attributes,
      diff_drivers=drivers,
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


class FixtureSelectionTests(RepoCase):
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

  def test_fixture_lockfile_patterns_exclude_lockfiles(self) -> None:
    self.changed("Cargo.lock", "web/package-lock.json", "web/pnpm-lock.yaml", "main.py")

    rows, excluded, *_ = self.collect()

    self.assertEqual([row.split("\t", 1)[0] for row in rows], ["main.py"])
    self.assertEqual(
      sorted(line.split("\t", 1)[0] for line in excluded),
      ["Cargo.lock", "web/package-lock.json", "web/pnpm-lock.yaml"],
    )


class DocDiffTests(RepoCase):
  MARKDOWN = (
    "# Intro\n" + "".join(f"intro {index}\n" for index in range(1, 6)) + "# Usage\n"
    + "".join(f"usage {index}\n" for index in range(1, 9))
  )
  # Git の既定の hunk header は英字始まりの行なので、-W を掛けると "Usage" まで文脈が伸びる。
  RESTRUCTURED = "Intro\n=====\n\nUsage\n=====\n\n" + "".join(f"  usage {index}\n" for index in range(1, 9))
  DOC_SOURCES = (
    '[{"suffixes":[".md"],"diff-driver":"markdown"},{"suffixes":[".rst"],"diff-driver":null}]'
  )

  def diffs(self, files: dict[str, str]) -> dict[str, tuple[str, str]]:
    self.base = self.commit(*self.write(files))
    self.commit(*self.write({path: text.replace("usage 8", "usage eight") for path, text in files.items()}))
    rows, *_ = self.collect("docs", DOC_SOURCES=self.DOC_SOURCES)
    result = {}
    for row in rows:
      path, _status, diff_file, _size, driver = row.split("\t")
      result[path] = (driver, Path(diff_file).read_text())
    return result

  def test_driver_groups_get_whole_sections_and_null_groups_get_plain_diffs(self) -> None:
    diffs = self.diffs(
      {"guide.md": self.MARKDOWN, "NOTES.MD": self.MARKDOWN, "guide.rst": self.RESTRUCTURED}
    )

    self.assertEqual(diffs["guide.md"][0], "markdown")
    self.assertIn("\n # Usage\n", diffs["guide.md"][1])
    self.assertEqual(diffs["NOTES.MD"][0], "markdown")
    self.assertIn("\n # Usage\n", diffs["NOTES.MD"][1])
    self.assertEqual(diffs["guide.rst"][0], "-")
    self.assertNotIn("\n Usage\n", diffs["guide.rst"][1])
    self.assertIn("\n   usage 5\n", diffs["guide.rst"][1])

  def test_markdown_driver_diff_has_only_the_changed_section(self) -> None:
    diffs = self.diffs({"guide.md": self.MARKDOWN})

    self.assertIn("\n # Usage\n", diffs["guide.md"][1])
    self.assertNotIn("\n # Intro\n", diffs["guide.md"][1])

  def test_repository_gitattributes_cannot_change_drivers_or_hide_files(self) -> None:
    for attributes in ("*.md -diff\n*.py -diff\n", "*.md diff=tex\n"):
      for side in ("base", "head"):
        with self.subTest(attributes=attributes, side=side):
          self.setUp()
          files = {"guide.md": self.MARKDOWN, "main.py": "usage 8 = 1\n"}
          if side == "base":
            files[".gitattributes"] = attributes
          self.base = self.commit(*self.write(files))
          changed = {path: text.replace("usage 8", "usage eight") for path, text in files.items()}
          if side == "head":
            changed[".gitattributes"] = attributes
          self.commit(*self.write(changed))

          docs, docs_excluded, *_ = self.collect("docs", DOC_SOURCES=self.DOC_SOURCES)
          code, code_excluded, *_ = self.collect("code", DOC_SOURCES=self.DOC_SOURCES)

          markdown = {row.split("\t")[0]: row.split("\t") for row in docs}
          self.assertEqual(markdown["guide.md"][4], "markdown")
          text = Path(markdown["guide.md"][2]).read_text()
          self.assertIn("\n # Usage\n", text)
          self.assertNotIn("\n # Intro\n", text)
          self.assertIn("main.py", [row.split("\t")[0] for row in code])
          self.assertEqual(docs_excluded + code_excluded, [])
          self.doCleanups()

  def test_repository_config_for_a_driver_cannot_convert_or_hide_files(self) -> None:
    for key, value in (("textconv", "sed s/usage/converted/"), ("binary", "true")):
      with self.subTest(key=key):
        self.setUp()
        git("config", f"diff.markdown.{key}", value)
        diffs = self.diffs({"guide.md": self.MARKDOWN})

        self.assertEqual(diffs["guide.md"][0], "markdown")
        self.assertIn("\n+usage eight\n", diffs["guide.md"][1])
        self.assertNotIn("converted", diffs["guide.md"][1])
        self.doCleanups()

  def test_files_with_nul_are_binary_whatever_the_driver_or_config(self) -> None:
    for config in (None, "true", "false"):
      with self.subTest(config=config):
        self.setUp()
        if config:
          git("config", "diff.markdown.binary", config)
        nul = "text\n\0binary\n"
        late_nul = "line\n" * 2000 + "\0\n"
        files = {
          "image.md": nul, "late.md": late_nul, "data.rst": nul, "blob.py": nul,
          "guide.md": self.MARKDOWN, "was-binary.md": nul,
        }
        self.base = self.commit(*self.write(files))
        changed = {path: text + "more\n" for path, text in files.items()}
        changed["guide.md"] = self.MARKDOWN.replace("usage 8", "usage eight")
        changed["was-binary.md"] = "now text\n"
        self.commit(*self.write(changed))

        docs, docs_excluded, *_ = self.collect("docs", DOC_SOURCES=self.DOC_SOURCES)
        code, code_excluded, *_ = self.collect("code", DOC_SOURCES=self.DOC_SOURCES)

        self.assertEqual(
          sorted(docs_excluded + code_excluded),
          [f"{path}\tbinary" for path in ("blob.py", "data.rst", "image.md", "late.md", "was-binary.md")],
        )
        self.assertEqual([row.split("\t")[0] for row in docs + code], ["guide.md"])
        guide = docs[0].split("\t")
        self.assertEqual(guide[4], "markdown")
        text = Path(guide[2]).read_text()
        self.assertIn("\n # Usage\n", text)
        self.assertNotIn("\0", text)
        self.doCleanups()

  def test_doc_suffixes_ignore_only_ascii_case(self) -> None:
    drivers = {".md": "markdown", ".k": None}
    self.assertEqual(prepare.doc_driver("docs/README.MD", drivers), (True, "markdown"))
    self.assertEqual(prepare.doc_driver("notes.\u212a", drivers), (False, None))

  def test_attribute_lines_cover_uppercase_suffixes_and_skip_null_drivers(self) -> None:
    self.assertEqual(
      prepare.attribute_lines({".md": "markdown", ".rst": None, ".tex2": "tex"}),
      "*.[mM][dD] diff=markdown\n*.[tT][eE][xX]2 diff=tex\n",
    )


if __name__ == "__main__":
  unittest.main()
