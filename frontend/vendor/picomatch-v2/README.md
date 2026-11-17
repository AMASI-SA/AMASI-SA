# Local picomatch 2.3.2 security backport

This private source-vendored package is a local Mezan security backport, not an
upstream picomatch release. Its original MIT license is retained. The exact
published npm tarball, integrity, upstream source commit, and original file
hashes are recorded in `SECURITY-PROVENANCE.json`.

## Security boundary

GHSA-fc5q-39xc-vjpf affects repeated extglobs such as `+(*a)`: compiling an
unbounded wildcard inside another unbounded repetition can block the Node event
loop. At review time the upstream advisory lists no patched release. The linked
PR194 changes additional syntax and explicitly does not fix the full problem;
that patch is not imported here.

This backport checks the actual compiled inner expression before a repeated
extglob is emitted. It accepts bounded, unambiguous forms used by the real
consumers, and only flattens repetition when the resulting language has a
certified equivalent without nested repetition. Unsupported or ambiguous
repeated forms become literal text through picomatch's existing rejection path.
The new boundary remains active even with `maxExtglobRecursion: false`. Raw group repetitions are also checked; when an unsafe raw wrapper cannot preserve captures, the complete original glob is treated as literal text. Literal plus signs under `regex: false` and adjacent new extglobs keep their existing behavior.

This intentionally restricts complex repeated expressions that the original
package accepted. It is not a general safety claim for arbitrary caller-created
regular expressions, forged parser states, `toRegex`, or `test`'s RegExp input.
Ordinary globs, globstars, literal-word repetition, quoted/escaped stars, and
supported fixed-width classes keep their tested behavior. Captures are retained
for accepted forms; a rewrite is refused if it cannot preserve the supported
capture contract.

The v2 and v4 packages remain separate to preserve their consumers' APIs and
platform behavior. The v2 parser also incorporates v4's small text-token merge
correction so final rebuilt output agrees with the expression checked by the
security boundary. Neither package trusts the previous raw-pattern heuristic's
proposed rewrite without independently checking the actual compiled expression.

## Verification and replacement

`frontend/scripts/verify-picomatch-backports.cjs` binds the vendor and every
installed copy to the reviewed complete runtime file set and verifies resolution
from each consumer. `picomatch-security.test.cjs` exercises attacks in killable
child processes, normal matching and consumer compatibility, and tamper checks.
The normal registry audit remains strict and unchanged. A clean registry audit
alone does not establish that a private backport is safe.

Replace this backport with an official compatible fixed release when available,
after repeating the security, installed-byte, compatibility, build and release
checks. Do not rename unpatched sources, silently move vulnerable dependencies
between dependency groups, or add an audit exception.
