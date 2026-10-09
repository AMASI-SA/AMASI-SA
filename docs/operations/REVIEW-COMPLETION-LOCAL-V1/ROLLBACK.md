# Local review rollback boundary

Before deployment this is a Draft/WIP candidate only. No data migration or provider change is needed to abandon it.

After any separately authorized deployment creates completed local operations, a wholesale rollback to provider-only readers is unsafe: Salla will still be pending while durable Mezan approvals exist. Preserve the local policy/readers and immutable operation mode, and pause NEW completion through the existing operational owner write control if required by a separately approved incident procedure. Do not reinterpret local operations as provider-backed approvals or replay status POSTs.

Old prepared/syncing/provider_confirmed operations remain parked, byte-for-byte and without automatic dispatch. Resolving them is a separate owner-reviewed task; no backfill or recovery is part of this change. Shipping and financial eligibility retain their existing independent provider/business guards.
