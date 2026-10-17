Review only the documentation files, selected by the caller's documentation suffixes. Each diff
includes complete changed sections where Git can identify them. Check those sections against the
repository for stale claims and contradictions. Check that every file, command and setting a
document names exists.

A hunk can start below its heading (a "#" comment inside a code fence looks like a heading to
git); then Read the file from the heading named in the hunk's "@@ ... @@" line with offset and
limit.

Search for names, values and steps that the change renames or removes, then read the relevant
context. Do not read whole long documents when the changed sections provide enough context.
