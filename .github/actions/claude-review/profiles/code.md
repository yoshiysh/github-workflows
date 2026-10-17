Review non-Markdown files: implementation, tests, scripts, configuration and workflows.
Read every diff. Read changed source files whole before reading their tests; for files over 1500
lines, read the complete functions and test modules the diff touches.

For every function, type, error kind, field, command-line argument or public name the change adds,
changes or removes, search the repository for its callers and verify that they remain consistent.
Look for duplicated checks, conversions and helper logic, including behavior copied under
different names. A copy seldom has the name of what it copies: for every function, closure and
match the diff adds, search for what it calls and for the text it writes (the function it wraps
an error with, a message, a format string) and read the hits. The same steps written somewhere
else under another name are a copy.

Use copies.tsv as leads, not as a complete detector: it finds exact repeated source lines only.
Judge each row: a line that is a copy of a check, a conversion or a helper is a problem; the same
test data, a trait's signature, or a line two callers must both write is not.

Follow the calling repository's REVIEW.md and the base-branch rules it names, including the domain
invariants it defines.

Give each confirming Agent the following procedure verbatim. It confirms or rejects the candidate
in this order:
a. Read the code and files in the repository.
b. For a claim about language, library or tool behavior that the repository cannot settle, fetch
   the official documentation for the version the repository pins with WebFetch. Take the version
   from where the code pins it: toolchain files, lockfiles, version files, or where the calling
   repository's REVIEW.md says. Use the versioned path the site offers, which is often
   major.minor (doc.rust-lang.org/1.99.0/, docs.rs/serde/1.0/, docs.python.org/3.14/) rather
   than the full version. For Node.js, take the major from package.json engines or the workflow's
   node-version and use nodejs.org/docs/latest-v<major>.x/api/. When the site has no versioned
   path, or the versioned path fails or lands on the latest page, use the latest page and say so.
   WebFetch returns a model's extraction of the page, not the page itself: treat it as a claim
   about the docs, and a page that "does not mention" something refutes nothing.
c. Otherwise reject the candidate.
A problem confirmed from documentation keeps the fetched URL for its comment.

When the fix replaces the lines a comment is on, give it as a suggestion block
(```suggestion ... ```), written so that applying it leaves working code; otherwise say what to
change and where.
