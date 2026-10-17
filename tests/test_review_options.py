import json
import random
import re
import subprocess
import sys
import unittest
from pathlib import Path, PurePosixPath

from support import (
  ACTION, DEFAULTED_INPUTS, FIXTURE_INPUTS, REQUIRED_INPUTS, default_env, inputs_env,
  output_values, self_review_inputs, workflow_inputs,
)

import review_options


class ModelNameTests(unittest.TestCase):
  def test_accepts_documented_model_forms(self) -> None:
    for value in (
      "claude-opus-5-5",
      "claude-sonnet-4-5-20250929",
      "sonnet",
      "opus[1m]",
      "claude-opus-4-6[1m]",
      "claude-sonnet-4-5@20250929",
      "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
      "arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.anthropic.claude-x-v1:0",
      "anthropic/claude-opus-4-8",
    ):
      with self.subTest(value=value):
        self.assertTrue(review_options.is_model(value))

  def test_rejects_values_that_could_add_arguments(self) -> None:
    for value in (
      "",
      "x --allowedTools Bash",
      "--allowedTools=Bash",
      "-x",
      "opus\n--allowedTools Bash",
      "opus\tBash",
      "'opus'",
      '"opus"',
      "opus;id",
      "$MODEL",
      "${{ secrets.X }}",
      "opus`id`",
      "opus[1m] x",
      "opus[1m][2m]",
      "a" * 201,
    ):
      with self.subTest(value=value):
        self.assertFalse(review_options.is_model(value))


class KindOptionTests(unittest.TestCase):
  def env(self, **values: str) -> dict[str, str]:
    return {**default_env(), "SUBAGENT_MODEL": "claude-haiku-5-5", **values}

  def output(self, **values: str) -> dict[str, str]:
    return output_values(review_options.outputs(self.env(**values)))

  def test_selects_the_model_and_effort_for_each_kind(self) -> None:
    for kind in ("code", "docs"):
      with self.subTest(kind=kind):
        env = self.env(KIND=kind)
        output = self.output(KIND=kind)
        self.assertEqual(output["model"], env[f"{kind.upper()}_MODEL"])
        self.assertEqual(output["effort"], env[f"{kind.upper()}_EFFORT"])
        self.assertEqual(output["subagent_model"], "claude-haiku-5-5")

  def test_empty_model_for_this_kind_stops_instead_of_using_the_other(self) -> None:
    for kind, variable in (("code", "CODE_MODEL"), ("docs", "DOCS_MODEL")):
      with self.subTest(kind=kind):
        with self.assertRaisesRegex(review_options.RejectedOption, variable):
          review_options.outputs(self.env(KIND=kind, **{variable: ""}))

  def test_empty_effort_for_this_kind_stops_instead_of_using_the_other(self) -> None:
    for kind, variable in (("code", "CODE_EFFORT"), ("docs", "DOCS_EFFORT")):
      with self.subTest(kind=kind):
        with self.assertRaisesRegex(review_options.RejectedOption, variable):
          review_options.outputs(self.env(KIND=kind, **{variable: ""}))

  def test_effort_outside_the_documented_levels_stops(self) -> None:
    for value in ("ultracode", "HIGH", "high ", "high\nmax", "x --allowedTools Bash"):
      with self.subTest(value=value):
        with self.assertRaisesRegex(review_options.RejectedOption, "CODE_EFFORT"):
          review_options.outputs(self.env(KIND="code", CODE_EFFORT=value))

  def test_each_documented_effort_level_passes(self) -> None:
    for value in ("low", "medium", "high", "xhigh", "max", "auto"):
      with self.subTest(value=value):
        self.assertEqual(self.output(KIND="docs", DOCS_EFFORT=value)["effort"], value)

  def test_empty_subagent_model_stops(self) -> None:
    with self.assertRaisesRegex(review_options.RejectedOption, "SUBAGENT_MODEL"):
      review_options.outputs(self.env(KIND="code", SUBAGENT_MODEL=""))

  def test_unknown_kind_stops(self) -> None:
    with self.assertRaisesRegex(review_options.RejectedOption, "kind"):
      review_options.outputs(self.env(KIND="rust"))


PREVIOUS_LOCKFILE = re.compile(
  r"(^|/)(.*\.lock|package-lock\.json|yarn\.lock|pnpm-lock\.yaml|uv\.lock|poetry\.lock)$"
)


class WorkflowInputTests(unittest.TestCase):
  def test_repository_specific_inputs_are_required_without_a_default(self) -> None:
    inputs = workflow_inputs()
    for name in REQUIRED_INPUTS:
      with self.subTest(name=name):
        self.assertEqual(inputs[name].get("required"), "true")
        self.assertNotIn("default", inputs[name])

  def test_operational_inputs_have_a_default(self) -> None:
    inputs = workflow_inputs()
    for name in DEFAULTED_INPUTS:
      with self.subTest(name=name):
        self.assertIn("default", inputs[name])
        self.assertNotEqual(inputs[name].get("required"), "true")

  def test_self_review_passes_every_required_input_and_validates(self) -> None:
    inputs = self_review_inputs()
    self.assertLessEqual(set(inputs), REQUIRED_INPUTS | DEFAULTED_INPUTS)
    self.assertEqual(set(inputs) & REQUIRED_INPUTS, REQUIRED_INPUTS)
    for kind in ("code", "docs"):
      with self.subTest(kind=kind):
        review_options.outputs({**inputs_env(inputs), "KIND": kind})

  def test_fixture_lockfile_patterns_match_the_previous_lockfile_regex(self) -> None:
    patterns = json.loads(FIXTURE_INPUTS["exclude-paths"])
    for path in (
      "Cargo.lock", "yarn.lock", "uv.lock", "poetry.lock", "flake.lock", ".lock",
      "a/b/Gemfile.lock", "web/.lock", "package-lock.json", "web/package-lock.json",
      "pnpm-lock.yaml", "a/b/pnpm-lock.yaml", "Cargo.lock.bak", "lock", "a.lock/x.py",
      "package-lock.json5", "my-package-lock.json", "xpnpm-lock.yaml", "pnpm-lock.yml",
      "main.py", "docs/lock.md", "Cargo.LOCK",
    ):
      with self.subTest(path=path):
        self.assertEqual(
          any(review_options.path_matches(pattern, path) for pattern in patterns),
          PREVIOUS_LOCKFILE.search(path) is not None,
        )

  def test_operational_defaults_with_the_fixture_pass_validation_for_both_kinds(self) -> None:
    for kind in ("code", "docs"):
      with self.subTest(kind=kind):
        review_options.outputs({**default_env(), "KIND": kind})


class DocDomainTests(unittest.TestCase):
  def domains(self, value: str) -> list[str]:
    return review_options.doc_domains({"DOC_DOMAINS": value})

  def test_caller_domains_follow_the_fixed_set_without_duplicates(self) -> None:
    self.assertEqual(
      self.domains('["docs.python.org","docs.github.com","docs.python.org"]'),
      [*review_options.FIXED_DOC_DOMAINS, "docs.python.org"],
    )

  def test_empty_array_keeps_only_the_fixed_set(self) -> None:
    self.assertEqual(self.domains("[]"), list(review_options.FIXED_DOC_DOMAINS))

  def test_accepts_lowercase_hostnames(self) -> None:
    for value in ("nodejs.org", "python-jsonschema.readthedocs.io", "a.b.c.d.example"):
      with self.subTest(value=value):
        self.assertTrue(review_options.is_hostname(value))

  def test_rejects_values_that_are_not_plain_hostnames(self) -> None:
    for value in (
      "", "localhost", "Docs.Python.org", "*.python.org", "https://docs.python.org",
      "docs.python.org/3/", "docs.python.org:443", "docs.python.org.", "docs python.org",
      "-docs.python.org", "docs-.python.org", "docs..python.org", "127.0.0.1",
      "docs.python.org)", "docs.python.org,Bash", "a" * 64 + ".org",
    ):
      with self.subTest(value=value):
        self.assertFalse(review_options.is_hostname(value))

  def test_rejects_arrays_that_are_not_hostname_lists(self) -> None:
    for value in ("", "docs.python.org", '"docs.python.org"', '{"a":1}', "[1]", '["x y"]'):
      with self.subTest(value=value):
        with self.assertRaisesRegex(review_options.RejectedOption, "DOC_DOMAINS"):
          self.domains(value)

  def test_rejects_too_many_domains(self) -> None:
    value = "[" + ",".join(
      f'"d{index}.example"' for index in range(review_options.MAX_LIST_ITEMS + 1)
    ) + "]"
    with self.assertRaisesRegex(review_options.RejectedOption, "more than"):
      self.domains(value)

  def test_prompt_and_allowed_tools_list_the_same_domains(self) -> None:
    env = {**default_env(), "KIND": "code", "DOC_DOMAINS": '["nodejs.org"]'}
    lines = output_values(review_options.outputs(env))
    prose = lines["doc_domains"].replace(", and ", ", ").split(", ")
    tools = [
      tool.removeprefix("WebFetch(domain:").removesuffix(")")
      for tool in lines["webfetch_tools"].split(",")
    ]
    expected = [*review_options.FIXED_DOC_DOMAINS, "nodejs.org"]
    self.assertEqual(prose, expected)
    self.assertEqual(tools, expected)


class ReviewSettingTests(unittest.TestCase):
  def settings(self, **values: str) -> dict:
    return review_options.review_settings({**default_env(), **values})

  def rejects(self, variable: str, value: str) -> None:
    with self.subTest(variable=variable, value=value):
      with self.assertRaisesRegex(review_options.RejectedOption, variable):
        self.settings(**{variable: value})

  def test_settings_round_trip_through_the_written_outputs(self) -> None:
    env = {**default_env(), "KIND": "code", "EXCLUDE_PATHS": '["vendor/**"]'}
    lines = output_values(review_options.outputs(env))
    passed = {name: lines[name.lower()] for name in (
      "DOC_SOURCES", "COPY_SOURCES", "EXCLUDE_PATHS", "REVIEW_POLICY_PATH",
    )}
    self.assertEqual(review_options.review_settings(passed), review_options.review_settings(env))

  def test_doc_sources(self) -> None:
    value = '[{"suffixes":[".md",".markdown",".md"],"diff-driver":"markdown"},{"suffixes":[".rst"],"diff-driver":null}]'
    self.assertEqual(
      self.settings(DOC_SOURCES=value)["doc_sources"],
      [
        {"suffixes": [".md", ".markdown"], "diff-driver": "markdown"},
        {"suffixes": [".rst"], "diff-driver": None},
      ],
    )
    self.assertEqual(self.settings(DOC_SOURCES="[]")["doc_sources"], [])
    for driver in sorted(review_options.DIFF_DRIVERS):
      with self.subTest(driver=driver):
        groups = [{"suffixes": [".doc"], "diff-driver": driver}]
        self.assertEqual(self.settings(DOC_SOURCES=json.dumps(groups))["doc_sources"], groups)
    for value in (
      "", "{}", '[".md"]', '[{"suffixes":[".md"]}]', '[{"diff-driver":"markdown"}]',
      '[{"suffixes":[".md"],"diff-driver":"markdown","extra":1}]',
      '[{"suffixes":".md","diff-driver":"markdown"}]', '[{"suffixes":[],"diff-driver":null}]',
      '[{"suffixes":[".md"],"diff-driver":"rst"}]', '[{"suffixes":[".md"],"diff-driver":"Markdown"}]',
      '[{"suffixes":[".md"],"diff-driver":"^#+ .*$"}]', '[{"suffixes":[".md"],"diff-driver":""}]',
      '[{"suffixes":[".md"],"diff-driver":["markdown"]}]', '[{"suffixes":[".md"],"diff-driver":1}]',
      '[{"suffixes":[".md"],"diff-driver":"markdown"},{"suffixes":[".md"],"diff-driver":null}]',
      '[{"suffixes":[".MD"],"diff-driver":"markdown"}]', '[{"suffixes":["md"],"diff-driver":null}]',
      '[{"suffixes":["*.md"],"diff-driver":null}]', '[{"suffixes":[".m d"],"diff-driver":null}]',
    ):
      self.rejects("DOC_SOURCES", value)

  def test_a_suffix_cannot_be_both_documentation_and_copy_source(self) -> None:
    docs = '[{"suffixes":[".txt"],"diff-driver":null}]'
    for copies in ('[{"suffixes":[".txt"],"ignore":[]}]', '[{"suffixes":[".TXT"],"ignore":[]}]'):
      with self.subTest(copies=copies):
        with self.assertRaisesRegex(review_options.RejectedOption, "both"):
          self.settings(DOC_SOURCES=docs, COPY_SOURCES=copies)

  def test_copy_sources(self) -> None:
    value = '[{"suffixes":[".R",".r",".R"],"ignore":["#","library("]},{"suffixes":[".sql"],"ignore":[]}]'
    self.assertEqual(
      self.settings(COPY_SOURCES=value)["copy_sources"],
      [{"suffixes": [".R", ".r"], "ignore": ["#", "library("]}, {"suffixes": [".sql"], "ignore": []}],
    )
    self.assertEqual(self.settings(COPY_SOURCES="[]")["copy_sources"], [])
    for value in (
      "", "{}", '{".py":["#"]}', '[".py"]', "[[]]",
      '[{"suffixes":[".py"]}]', '[{"ignore":["#"]}]',
      '[{"suffixes":[".py"],"ignore":["#"],"extra":[]}]',
      '[{"suffixes":".py","ignore":["#"]}]', '[{"suffixes":[".py"],"ignore":"#"}]',
      '[{"suffixes":[],"ignore":["#"]}]',
      '[{"suffixes":[".py"],"ignore":["#"]},{"suffixes":[".sh",".py"],"ignore":["#"]}]',
      '[{"suffixes":["py"],"ignore":[]}]', '[{"suffixes":[".p-y"],"ignore":[]}]',
      '[{"suffixes":["*.py"],"ignore":[]}]', '[{"suffixes":[".py\\n"],"ignore":[]}]',
      '[{"suffixes":[".py"],"ignore":[1]}]', '[{"suffixes":[".py"],"ignore":[""]}]',
      '[{"suffixes":[".py"],"ignore":["a\\nb"]}]', '[{"suffixes":[".py"],"ignore":["a\\tb"]}]',
      '[{"suffixes":[".py"],"ignore":["a\\u0000b"]}]',
      '[{"suffixes":[".py"],"ignore":["' + "x" * 201 + '"]}]',
    ):
      self.rejects("COPY_SOURCES", value)

  def test_copy_sources_limit_groups_suffixes_and_prefixes(self) -> None:
    limit = review_options.MAX_LIST_ITEMS
    groups = [{"suffixes": [f".s{index}"], "ignore": []} for index in range(limit + 1)]
    self.rejects("COPY_SOURCES", json.dumps(groups))
    suffixes = [f".s{index}" for index in range(limit + 1)]
    self.rejects("COPY_SOURCES", json.dumps([{"suffixes": suffixes, "ignore": []}]))
    prefixes = [f"p{index}" for index in range(limit + 1)]
    self.rejects("COPY_SOURCES", json.dumps([{"suffixes": [".py"], "ignore": prefixes}]))

  def test_exclude_paths(self) -> None:
    self.assertEqual(
      self.settings(EXCLUDE_PATHS='["vendor/**","**/*.generated.ts"]')["exclude_paths"],
      ["vendor/**", "**/*.generated.ts"],
    )
    for value in (
      '["/vendor/**"]', '["../x"]', '["a/../b"]', '["vendor/"]', '["a//b"]', '["./a"]',
      '["a\\nb"]', '["a\\tb"]', '[""]',
    ):
      self.rejects("EXCLUDE_PATHS", value)

  def test_exclude_paths_reject_brackets(self) -> None:
    for value in ('["a[!x]b"]', '["[id].tsx"]', '["a]"]'):
      self.rejects("EXCLUDE_PATHS", value)

  def test_review_policy_path(self) -> None:
    self.assertEqual(self.settings(REVIEW_POLICY_PATH="docs/REVIEW.md")["review_policy_path"], "docs/REVIEW.md")
    for value in ("", "/REVIEW.md", "../REVIEW.md", "docs/../REVIEW.md", "docs/", "a\nb"):
      self.rejects("REVIEW_POLICY_PATH", value)


class PathMatchTests(unittest.TestCase):
  def test_examples(self) -> None:
    for pattern, path, expected in (
      ("vendor/**", "vendor/x/y.js", True),
      ("vendor/**", "vendor", False),
      ("vendor/*", "vendor/x/y.js", False),
      ("**", "a", True),
      ("**/*.lock", "Cargo.lock", True),
      ("**/*.lock", "a/b/Cargo.lock", True),
      ("*.lock", "a/Cargo.lock", False),
      ("a/**/b", "a/b", True),
      ("a/**/b", "a/x/y/b", True),
      ("a?c", "abc", True),
      ("a?c", "a/c", False),
      ("*.LOCK", "a.lock", False),
      ("a**b", "axxb", True),
    ):
      with self.subTest(pattern=pattern, path=path):
        self.assertEqual(review_options.path_matches(pattern, path), expected)

  @unittest.skipUnless(hasattr(PurePosixPath, "full_match"), "PurePath.full_match needs Python 3.13")
  def test_agrees_with_pure_path_full_match(self) -> None:
    generator = random.Random(20261017)
    segments = ("**", "*", "a", "b", "a*", "*b", "?", "a?b", "*a*", ".a", "a.b", "*.*", "a**b")
    names = ("a", "b", "ab", "ba", "aab", ".a", "a.b", "abb", "x")
    for _ in range(4000):
      pattern = "/".join(generator.choice(segments) for _ in range(generator.randint(1, 4)))
      path = "/".join(generator.choice(names) for _ in range(generator.randint(1, 5)))
      with self.subTest(pattern=pattern, path=path):
        self.assertEqual(
          review_options.path_matches(pattern, path),
          PurePosixPath(path).full_match(pattern),
        )

  def test_pathological_patterns_stay_fast(self) -> None:
    code = (
      "import review_options\n"
      "deep = '/'.join(['d'] * 2000) + '/x.y'\n"
      "assert not review_options.path_matches('**/d/**/d/**/d/**/d/**/z', deep)\n"
      "assert not review_options.path_matches('*a' * 12 + '*b', 'a' * 5000)\n"
    )
    try:
      subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        timeout=10,
        cwd=Path(review_options.__file__).parent,
      )
    except subprocess.TimeoutExpired:
      self.fail("path_matches did not finish the pathological patterns within 10 seconds")


class OutputSizeTests(unittest.TestCase):
  def test_rejects_a_checked_value_longer_than_the_output_limit(self) -> None:
    prefix = "x" * review_options.MAX_TEXT_LENGTH
    count = review_options.MAX_OUTPUT_BYTES // review_options.MAX_TEXT_LENGTH // 8 + 1
    groups = [
      {"suffixes": [f".s{group}"], "ignore": [f"{index:03d}{prefix[3:]}" for index in range(count)]}
      for group in range(8)
    ]
    env = {**default_env(), "KIND": "code", "COPY_SOURCES": json.dumps(groups)}
    self.assertEqual(review_options.copy_sources(env), groups)
    with self.assertRaisesRegex(review_options.RejectedOption, "copy_sources.*bytes"):
      review_options.outputs(env)

  def test_default_outputs_fit_the_limit(self) -> None:
    for value in output_values(review_options.outputs({**default_env(), "KIND": "code"})).values():
      self.assertLessEqual(len(value.encode()), review_options.MAX_OUTPUT_BYTES)


class TimeoutTests(unittest.TestCase):
  def test_accepts_whole_minutes_up_to_the_hosted_job_limit(self) -> None:
    for value in ("1", "60", str(review_options.MAX_TIMEOUT_MINUTES)):
      with self.subTest(value=value):
        self.assertEqual(review_options.timeout_minutes({"TIMEOUT_MINUTES": value}), int(value))

  def test_rejects_other_values(self) -> None:
    for value in ("", "0", "-5", "1.5", "060", " 60", str(review_options.MAX_TIMEOUT_MINUTES + 1), "1e3"):
      with self.subTest(value=value):
        with self.assertRaisesRegex(review_options.RejectedOption, "TIMEOUT_MINUTES"):
          review_options.timeout_minutes({"TIMEOUT_MINUTES": value})


class ActionWiringTests(unittest.TestCase):
  def test_caller_inputs_reach_only_the_validation_step(self) -> None:
    text = ACTION.read_text(encoding="utf-8")
    steps = text.split("\n    - ")
    options = [step for step in steps if "\n      id: options\n" in step]
    self.assertEqual(len(options), 1)
    allowed_elsewhere = {"kind", "claude_code_oauth_token", "github_token"}
    for step in steps:
      if step is options[0]:
        continue
      used = set(re.findall(r"inputs(?:\.|\[\s*['\"])([A-Za-z0-9_-]+)", step))
      self.assertLessEqual(used, allowed_elsewhere, step.splitlines()[0])

  def test_claude_has_no_shell_and_every_allowed_tool_is_available(self) -> None:
    text = ACTION.read_text(encoding="utf-8")
    flags = dict(
      re.findall(r'^          --(tools|disallowedTools|allowedTools) "([^"]*)"$', text, re.MULTILINE)
    )
    tools = set(flags["tools"].split(","))
    shells = {"Bash", "Monitor"}
    self.assertFalse(tools & shells)
    self.assertLessEqual(shells, set(flags["disallowedTools"].split(",")))
    rules = re.sub(r"\$\{\{[^}]*\}\}", "", flags["allowedTools"]).split(",")
    allowed = {rule.split("(", 1)[0] for rule in rules if rule}
    self.assertFalse(allowed & shells)
    self.assertLessEqual({tool for tool in allowed if not tool.startswith("mcp__")}, tools)
    # Write は summary.md を作り（Edit の allow 規則が Write にも効く）、ToolSearch は遅延読み込みの MCP ツールを読み込む。
    self.assertLessEqual({"WebFetch", "Write", "ToolSearch"}, tools)


if __name__ == "__main__":
  unittest.main()
