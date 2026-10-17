import json
import re
import unittest

from support import DEFAULTED_INPUTS, REQUIRED_INPUTS, ROOT, inputs_env

import review_options

README = ROOT / "README.md"
FENCE = re.compile(r"^```(\w+)\n(.*?)^```$", re.MULTILINE | re.DOTALL)
WITH_VALUE = re.compile(r"^\s+([a-z][a-z-]*): (.+)$")


RECIPE_INPUTS = {"`copy-sources`": "COPY_SOURCES", "`exclude-paths`": "EXCLUDE_PATHS", "`doc-sources`": "DOC_SOURCES"}
TABLE_ROW = re.compile(r"^\| ([^|]+?) \| ([^|]+?) \| .+ \|$", re.MULTILINE)
TABLE_LANGUAGES = {
  "Python": ".py", "Rust": ".rs", "JavaScript / TypeScript": ".js", "Go": ".go", "Shell": ".sh",
}


def recipes() -> dict[str, list]:
  """Recipes 節の json ブロックを、直前の段落の先頭が名指す input ごとに返す。"""
  text = README.read_text(encoding="utf-8")
  section = text[text.index("### Recipes"):text.index("### Repository setup")]
  found = {}
  for match in FENCE.finditer(section):
    if match.group(1) != "json":
      continue
    before = section[:match.start()].rstrip().splitlines()[-1]
    name = next(input_name for label, input_name in RECIPE_INPUTS.items() if before.startswith(label))
    found[name] = json.loads(match.group(2))
  return found


def blocks(language: str) -> list[str]:
  return [body for name, body in FENCE.findall(README.read_text(encoding="utf-8")) if name == language]


def unquote(text: str) -> str:
  if text.startswith("'") and text.endswith("'"):
    return text[1:-1].replace("''", "'")
  return text


class ReadmeExampleTests(unittest.TestCase):
  def test_every_with_example_passes_validation(self) -> None:
    examples = []
    for body in blocks("yaml"):
      lines = body.splitlines()
      if not any(line.strip() == "with:" for line in lines):
        continue
      start = next(index for index, line in enumerate(lines) if line.strip() == "with:")
      indent = len(lines[start + 1]) - len(lines[start + 1].lstrip())
      values = {}
      for line in lines[start + 1:]:
        if len(line) - len(line.lstrip()) != indent or not (match := WITH_VALUE.match(line)):
          break
        values[match.group(1)] = unquote(match.group(2))
      examples.append(values)
    self.assertGreaterEqual(len(examples), 3)
    for values in examples:
      with self.subTest(values=values):
        self.assertLessEqual(set(values), REQUIRED_INPUTS | DEFAULTED_INPUTS)
        self.assertEqual(set(values) & REQUIRED_INPUTS, REQUIRED_INPUTS)
        for kind in ("code", "docs"):
          review_options.outputs({**inputs_env(values), "KIND": kind})

  def test_recipes_pass_validation_together(self) -> None:
    found = recipes()
    self.assertEqual(set(found), set(RECIPE_INPUTS.values()))
    settings = review_options.review_settings(
      {name: json.dumps(value) for name, value in found.items()} | {"REVIEW_POLICY_PATH": "REVIEW.md"}
    )
    suffixes = review_options.ignore_prefixes(settings["copy_sources"])
    for suffix in TABLE_LANGUAGES.values():
      self.assertIn(suffix, suffixes)

  def test_prefix_table_lists_exactly_the_recipe_prefixes(self) -> None:
    text = README.read_text(encoding="utf-8")
    section = text[text.index("| Language | Prefix |"):text.index("`exclude-paths` patterns for")]
    listed = {}
    for language, prefixes in TABLE_ROW.findall(section):
      if language in TABLE_LANGUAGES:
        listed.setdefault(language, set()).update(re.findall(r"`([^`]+)`", prefixes))
    groups = review_options.ignore_prefixes(
      review_options.copy_sources({"COPY_SOURCES": json.dumps(recipes()["COPY_SOURCES"])})
    )
    self.assertEqual(set(listed), set(TABLE_LANGUAGES))
    for language, suffix in TABLE_LANGUAGES.items():
      with self.subTest(language=language):
        self.assertEqual(listed[language], set(groups[suffix]))


class RemovedNameTests(unittest.TestCase):
  def test_no_file_outside_tests_names_the_removed_doc_suffixes_input(self) -> None:
    removed = re.compile(r"doc-suffixes|doc_suffixes|DOC_SUFFIXES")
    paths = [ROOT / "README.md", ROOT / "REVIEW.md", *(ROOT / ".github").rglob("*")]
    hits = [
      str(path.relative_to(ROOT))
      for path in paths
      if path.is_file()
      and path.suffix in {".md", ".py", ".yml", ".json"}
      and removed.search(path.read_text(encoding="utf-8"))
    ]
    self.assertEqual(hits, [])


if __name__ == "__main__":
  unittest.main()
