# Snapchat new-day dashboard spend

The owner reported that Snapchat spend disappeared at the start of the new
Riyadh day. Live read-only evidence on 2026-10-26 showed that the USD account
already had an observed provisional SAR amount. The authorized SAR account
had no hourly rows, while its same-run independent reconciliation stored a
normalized zero and its financial run completed successfully.

The unified dashboard reader considered only hourly fact run IDs. An empty
hourly result therefore lost its producer proof and made the entire portfolio
unavailable. The card then described the connected account as "no data" and
coerced the unknown aggregate amount to zero.

Further review proved that the HOUR fallback counts payloads and can normalize
an empty breakdown to zero. Its stored rows_received=1 is therefore not proof
of an explicit numeric spend observation. The proposed dashboard zero exception
was rejected before execution or deployment. Empty rows alone do not prove zero.

The bounded repair exposes a separate known subtotal from only authorized
accounts whose complete/provisional amount is already available. An unavailable
account never contributes a fabricated zero. The full total remains unavailable
and historical closing, profit completeness and intelligence eligibility retain
their original gates. Missing hourly values stay missing, and no daily total is
distributed into hours. The card labels known partial amounts "المتاح" and
"غير مكتمل", distinguishes provisional amounts and disconnected providers, and
preserves numeric zero. It does not use untrusted arithmetic daily sums as the
known subtotal.

Verification and deployment evidence are recorded in the task PR and the
documentation-only Snapchat completion status branch. This source document
does not assert a production deployment.

Root verification: the 18 new regression cases fail on the unchanged baseline
because the explicit known-subtotal contract is absent (0.62 seconds), and pass
on the patch (0.63 seconds). The actual reader/storage case retains the unknown
SAR account and hourly gap while exposing only the observed USD amount. The
48 existing affected dashboard/range/portfolio cases pass (1.41 seconds, two
existing Pydantic deprecation warnings). A malformed-headline fixture was
isolated from unrelated legacy daily parsing before the final red/green run.
Fresh integrated CI, governed release checks and live acceptance remain required.
