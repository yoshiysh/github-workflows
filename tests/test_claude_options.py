import sys
import unittest
from pathlib import Path

sys.path.insert(
  0, str(Path(__file__).resolve().parents[1] / ".github/actions/claude-review")
)
import claude_options


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
        self.assertTrue(claude_options.is_model(value))

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
        self.assertFalse(claude_options.is_model(value))


class KindOptionTests(unittest.TestCase):
  def env(self, **values: str) -> dict[str, str]:
    return {
      "CODE_MODEL": "claude-opus-5-5",
      "DOCS_MODEL": "claude-sonnet-5-5",
      "CODE_EFFORT": "high",
      "DOCS_EFFORT": "xhigh",
      "SUBAGENT_MODEL": "claude-haiku-5-5",
      **values,
    }

  def test_selects_the_model_for_each_kind(self) -> None:
    self.assertEqual(
      claude_options.outputs(self.env(KIND="code")),
      "model=claude-opus-5-5\neffort=high\nsubagent_model=claude-haiku-5-5\n",
    )
    self.assertEqual(
      claude_options.outputs(self.env(KIND="docs")),
      "model=claude-sonnet-5-5\neffort=xhigh\nsubagent_model=claude-haiku-5-5\n",
    )

  def test_empty_model_for_this_kind_stops_instead_of_using_the_other(self) -> None:
    for kind, variable in (("code", "CODE_MODEL"), ("docs", "DOCS_MODEL")):
      with self.subTest(kind=kind):
        with self.assertRaisesRegex(claude_options.RejectedOption, variable):
          claude_options.outputs(self.env(KIND=kind, **{variable: ""}))

  def test_empty_effort_for_this_kind_stops_instead_of_using_the_other(self) -> None:
    for kind, variable in (("code", "CODE_EFFORT"), ("docs", "DOCS_EFFORT")):
      with self.subTest(kind=kind):
        with self.assertRaisesRegex(claude_options.RejectedOption, variable):
          claude_options.outputs(self.env(KIND=kind, **{variable: ""}))

  def test_effort_outside_the_documented_levels_stops(self) -> None:
    for value in ("ultracode", "HIGH", "high ", "high\nmax", "x --allowedTools Bash"):
      with self.subTest(value=value):
        with self.assertRaisesRegex(claude_options.RejectedOption, "CODE_EFFORT"):
          claude_options.outputs(self.env(KIND="code", CODE_EFFORT=value))

  def test_each_documented_effort_level_passes(self) -> None:
    for value in ("low", "medium", "high", "xhigh", "max", "auto"):
      with self.subTest(value=value):
        self.assertIn(
          f"effort={value}\n",
          claude_options.outputs(self.env(KIND="docs", DOCS_EFFORT=value)),
        )

  def test_empty_subagent_model_stops(self) -> None:
    with self.assertRaisesRegex(claude_options.RejectedOption, "SUBAGENT_MODEL"):
      claude_options.outputs(self.env(KIND="code", SUBAGENT_MODEL=""))

  def test_unknown_kind_stops(self) -> None:
    with self.assertRaisesRegex(claude_options.RejectedOption, "kind"):
      claude_options.outputs(self.env(KIND="rust"))


if __name__ == "__main__":
  unittest.main()
