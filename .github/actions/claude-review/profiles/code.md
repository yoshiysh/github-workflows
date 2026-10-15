Review non-Markdown files: implementation, tests, scripts, configuration and workflows.
Read every diff. Read changed source files whole before reading their tests; for files over 1500
lines, read the complete functions and test modules the diff touches.

For every function, type, error kind, field, command-line argument or public name the change adds,
changes or removes, search the repository for its callers and verify that they remain consistent.
Look for duplicated checks, conversions and helper logic, including behavior copied under
different names. Use copies.tsv as leads, not as a complete detector: it finds exact repeated
source lines only.

Follow the calling repository's REVIEW.md and the base-branch rules it names. For financial
calculations, check units, denominator conditions, time-series look-ahead, timeouts, and any other
financial invariants required by REVIEW.md. Confirm language, library and tool behavior from the
version used by the repository and official documentation when the source alone cannot settle it.
