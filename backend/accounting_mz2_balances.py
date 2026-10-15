"""Transaction-bound MZ2 balances using the accepted report eligibility contract."""
from collections import defaultdict
from decimal import Decimal

from fastapi import HTTPException

from accounting_atomic import SessionDatabase
from accounting_mz2_reports import read_mz2_ledger
from accounting_write_control import AccountingDatabase


class MZ2WriteBalances:
    def __init__(self, rows):
        self._nets = defaultdict(lambda: Decimal(0))
        for row in rows:
            key = (row["entity_type"], row["entity_id"], row.get("sub_account") or "")
            amount = Decimal(str(row["amount"]))
            self._nets[key] += amount if row["side"] == "debit" else -amount

    def net_balance(self, *, entity_type, entity_id, sub_account=None):
        return self._nets[(entity_type, entity_id, sub_account or "")]


async def read_mz2_write_balances(db, *, owner, required_accounts=()):
    """Only the serialized owner's active transaction may authorize a write.

    Collection calls made by the shared reader stay bound to this session;
    neither the raw database nor an out-of-transaction report is consulted.
    The caller must consume the result before its atomic callback returns.
    """
    if isinstance(db, AccountingDatabase):
        db = db.current()
    if (not isinstance(db, SessionDatabase) or db._owner != owner
            or not db._session.in_transaction):
        raise HTTPException(409, "mz2_balance_requires_owner_transaction")
    scope = await read_mz2_ledger(db, owner=owner, required_accounts=required_accounts)
    if scope["status"] != "available":
        raise HTTPException(409, detail={
            "code": "mz2_balance_not_ready", "status": scope["status"],
            "reason": scope["reason"], "missing_accounts": scope["missing_accounts"],
        })
    return MZ2WriteBalances(scope["items"])
