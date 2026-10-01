"""Customer-specific arguments to the shared sealed native fixture."""
from mz2_native_fixture import provision_native_opening

async def native_customer_opening(db, *, cutover="2020-01-01T00:00:00Z", bank=None, amount="1.00", bank_zero_ids=()):
    return await provision_native_opening(db, cutover=cutover,
        bank_balances={bank: amount} if bank else None,
        zero_accounts=[("bank", identity, "main") for identity in bank_zero_ids])


from pymongo.monitoring import CommandListener
class CustomerLegacyAccess(CommandListener):
    """Observe actual database commands during a converted domain call."""
    def __init__(self):
        self.accesses = []
    def started(self, event):
        collection = event.command.get(event.command_name)
        if collection in ("general_ledger", "account_transactions", "accounts", "accounting_audit_log"):
            self.accesses.append((event.command_name, collection))
    def succeeded(self, event): pass
    def failed(self, event): pass
