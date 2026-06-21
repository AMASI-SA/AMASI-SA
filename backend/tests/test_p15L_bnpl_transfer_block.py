"""
Iter-250b · P1.5.L — Unit test for `_account_blocks_internal_transfer` to
ensure Tamara/Tabby BNPL wallets are rejected while Salla is allowed.

We replicate the helper logic here because it's a closure inside the
factory in universal_accounting_routes.register(...). The logic is
verbatim.
"""

def _account_blocks_internal_transfer(acc: dict):
    atype = (acc.get("account_type") or "").strip()
    if atype not in ("bank", "cash", "payment_platform"):
        return True, "not internal-transfer-eligible"
    npm = (acc.get("normalized_payment_method") or "").lower()
    pname_raw = acc.get("provider_name") or acc.get("name") or ""
    pname = pname_raw.lower()
    cod_patterns = ["cod", "cash_on_delivery", "الدفع عند الاستلام"]
    bank_transfer_patterns = ["bank_transfer", "تحويل بنكي"]
    for p in cod_patterns:
        if p in npm or p in pname_raw:
            return True, "cod-blocked"
    for p in bank_transfer_patterns:
        if p in npm or p in pname_raw:
            return True, "bt-blocked"
    bnpl_patterns = ["tamara", "tabby", "bnpl", "تمارا", "تابي"]
    for p in bnpl_patterns:
        if p in npm or p in pname:
            return True, (
                "لا يمكن تسجيل تحويل مباشر على حسابات Tamara/Tabby. "
                "استخدم شاشة تسويات BNPL الرسمية."
            )
    return False, ""


def run():
    cases = [
        # Bank → allowed
        ({"account_type": "bank", "name": "AlRajhi"}, False),
        # Cash → allowed
        ({"account_type": "cash", "name": "صندوق الرياض"}, False),
        # Salla → allowed (NOT in BNPL list)
        ({"account_type": "payment_platform", "name": "Salla Wallet",
          "normalized_payment_method": "salla"}, False),
        # Tamara English npm → BLOCK
        ({"account_type": "payment_platform", "name": "محفظة تمارا",
          "normalized_payment_method": "tamara"}, True),
        # Tabby English npm → BLOCK
        ({"account_type": "payment_platform", "name": "Tabby Wallet",
          "normalized_payment_method": "tabby"}, True),
        # Arabic name only → BLOCK
        ({"account_type": "payment_platform", "provider_name": "تمارا",
          "normalized_payment_method": ""}, True),
        ({"account_type": "payment_platform", "provider_name": "تابي",
          "normalized_payment_method": ""}, True),
        # Generic bnpl key → BLOCK
        ({"account_type": "payment_platform", "name": "x",
          "normalized_payment_method": "bnpl"}, True),
        # COD still blocked
        ({"account_type": "payment_platform", "name": "الدفع عند الاستلام"},
         True),
        # Bank transfer (provider) still blocked
        ({"account_type": "payment_platform",
          "normalized_payment_method": "bank_transfer"}, True),
        # Wrong type → block
        ({"account_type": "courier", "name": "X"}, True),
    ]
    fail = 0
    for i, (acc, expected_blocked) in enumerate(cases):
        b, reason = _account_blocks_internal_transfer(acc)
        ok = (b == expected_blocked)
        print(f"[{i}] {'PASS' if ok else 'FAIL'} acc={acc} -> blocked={b} reason={reason!r}")
        if not ok:
            fail += 1
    print(f"\n{'ALL PASS' if fail == 0 else f'{fail} FAIL'}")
    return fail


if __name__ == "__main__":
    import sys
    sys.exit(run())
