Review only the documentation files, selected by the caller's documentation suffixes. files.tsv
gives each file's diff driver. A file with a driver has each changed section whole in its diff;
check those sections against the repository for stale claims and contradictions. A file without a
driver has a plain diff; Read the surrounding part of the file when the diff lines alone do not
show what a changed passage claims. Check that every file, command and setting a document names
exists.

The driver finds sections from patterns, so a hunk can start below the section it belongs to (for
the markdown driver, a "#" comment inside a code fence looks like a heading); then Read the file
from the section named in the hunk's "@@ ... @@" line with offset and limit.

Search for names, values and steps that the change renames or removes, then read the relevant
context. Do not read whole long documents when the changed sections provide enough context.
