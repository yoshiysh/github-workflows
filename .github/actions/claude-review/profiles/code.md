Review the files that are not documentation: implementation, tests, scripts, configuration and
workflows. Read every diff. Read changed source files whole before reading their tests; for files
over 1500 lines, read the complete functions and test modules the diff touches.

For every function, type, error, field, command-line argument or public name the change adds,
changes or removes, search the repository for its callers and verify that they remain consistent.
Look for duplicated checks, conversions and helper logic, including behavior copied under
different names. A copy seldom has the name of what it copies: for every function, callback and
branch the diff adds, search for what it calls and for the text it writes (the function it wraps
an error with, a message, a format string) and read the hits. The same steps written somewhere
else under another name are a copy.

Use copies.tsv as leads, not as a complete detector: it finds exact repeated source lines only.
Judge each row: a line that is a copy of a check, a conversion or a helper is a problem; the same
test data, an interface's signature, or a line two callers must both write is not.

Follow the calling repository's review policy (review-policy.md) and the base-branch rules it
names. If the review policy defines invariants, check the change against them.

Add this to step b of the confirming procedure you give each Agent: fetch the page for the
version the repository pins. Take the version from where the repository pins it, or from where
the review policy says. Use the site's versioned path when it has one. When the site has no
versioned path, or the versioned path fails or lands on the latest page, use the latest page and
say so.

When the fix replaces the lines a comment is on, give it as a suggestion block
(```suggestion ... ```), written so that applying it leaves working code; otherwise say what to
change and where.
