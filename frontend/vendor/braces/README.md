# Local braces security backport

This private, source-vendored dependency is **not** an official braces release.
It preserves Tailwind 3 and the existing Firefox 121 contract. The original MIT
license is retained. Source starts from the published braces 3.0.3 tarball:

`sha512-yQbXgO/OSZVD2IsiLlro+7Hf6Q18EJrKSEsdoMzKePKXct3gvD8oLcOQdIzGupr5Fj+EDe8gO/lxc1BzfMpxvA==`

The five runtime security-file changes from upstream PR72 at
`28d440b5dd449dbf1fe6f3506cf94ecca4d02660` are backported. Unreleased upstream
quote/comma parser behavior is deliberately excluded. Parsing bounds both braces
and parentheses to 100 levels, including mixed nesting; callers can request a
stricter maxDepth but cannot raise that cap. compile/expand/stringify enforce the
cap independently for supplied ASTs. Expansion rejects cyclic parent chains.
An additional local iterative AST validator rejects malformed non-string values
and non-array child lists before all three walkers. This closes the nested/cyclic
array-value coercion bypass found during independent review of the backport.
The original released test suite is retained under test/ without altered assertions.
Excessive input throws a bounded SyntaxError/RangeError rather than overflowing
the native stack. Normal expansion/compilation behavior and range limits remain.

The Yarn resolution must select these reviewed bytes at every dependency edge.
Security CI verifies installed-file hashes, all installed braces copies, public
API regression cases, and real Tailwind/fast-glob/micromatch/chokidar usage. A
clean registry audit alone is not evidence that a private fork is safe. The
original advisory remains recorded here for dependency inventory and review.

Replace this backport with an official compatible fixed release once one exists,
after repeating the same tests and full audit. Do not rename/version-bump the
unpatched source or add an audit exception.
