# 📒 Ads V2 — Posting Workflow (الترحيل إلى general_ledger)

> **الحالة:** مسودة لاعتماد التاجر  
> **التاريخ:** 2026-06-24

---

## 1. متى يحدث Posting؟

Posting يحدث **فقط** في إحدى الحالتين:

1. التاجر يضغط `Approve` على review في `pending` أو `reopened` أو `held_*`(مع force).
2. التاجر يضغط `Reverse` على posting سابق → Posting جديد بـ mirror legs.

**ممنوع** أي posting من:
- ❌ المزامنة التلقائية (sync)
- ❌ التجميع اليومي (recompute_daily)
- ❌ الـ reconciliation
- ❌ أي مسار آخر

> **القاعدة:** أي قيد في GL بـ `metadata.source='ads_v2'` يجب أن يكون له سطر مقابل في `ads_v2_ledger_postings`. لا استثناء.

---

## 2. هيكل القيد (Legs Structure)

### 2.1 الحالة الافتراضية (بدون bank_fee)

قيد Double-Entry بساطين:

```
DEBIT  expense:advertising:{provider}    spend_sar
CREDIT counterparty:ad_account:{acct_id} spend_sar
```

**معنى محاسبي:**
- Debit: نسجِّل مصروف الإعلانات في حساب المصروفات
- Credit: نسجِّل دَيناً مستحقاً على شركة الإعلانات (في حساب الـ counterparty)

### 2.2 مع Bank Fee (طريقة pct_only)

أربعة Legs:

```
DEBIT  expense:advertising:{provider}    2456.74    # spend
CREDIT counterparty:ad_account:{acct_id} 2456.74    # debt
DEBIT  expense:bank_fee:advertising      70.04      # fee
CREDIT counterparty:ad_account:{acct_id} 70.04      # extra debt
```

**معنى محاسبي:**
- مصروف الإعلانات الأصلي: 2456.74
- العمولة البنكية: 70.04 (تُسجَّل كمصروف منفصل بـ entry_type مميز)
- إجمالي الدين على الـ ad_account: 2526.78 (مجموع الـ credits)

### 2.3 مع Bank Fee (طريقة flat_only)

```
DEBIT  expense:advertising:{provider}    2456.74
CREDIT counterparty:ad_account:{acct_id} 2456.74
DEBIT  expense:bank_fee:advertising      50.00      # flat amount
CREDIT counterparty:ad_account:{acct_id} 50.00
```

### 2.4 مع Bank Fee (طريقة pct_plus_flat)

```
DEBIT  expense:advertising:{provider}    2456.74
CREDIT counterparty:ad_account:{acct_id} 2456.74
DEBIT  expense:bank_fee:advertising      120.04     # pct (70.04) + flat (50.00)
CREDIT counterparty:ad_account:{acct_id} 120.04
```

**ملاحظة:** نُجمِّع الـ pct و flat في leg واحد للـ fee لتبسيط القراءة، لكن في `bank_fee_breakdown` نحفظ التفاصيل.

### 2.5 entry_type لكل Leg

| الـ Leg | entry_type | side | الدلالة |
|---|---|---|---|
| Spend Debit | `ads_v2_expense` | debit | مصروف الإعلانات |
| Spend Credit | `ads_v2_debt_credit` | credit | دَين على ad_account |
| Bank Fee Debit | `ads_v2_bank_fee` | debit | مصروف العمولة |
| Bank Fee Credit | `ads_v2_debt_credit` | credit | دَين إضافي على ad_account |

**القاعدة:** كل القيود من V2 لها `metadata.source='ads_v2'` و `entry_type` يبدأ بـ `ads_v2_`. هذا يميِّزها عن V1.

---

## 3. الـ Posting Algorithm (الـ Flow الفعلي)

```python
def post_review_to_gl(review_id: str, actor: User, force: bool = False) -> Posting:
    """
    Posts a single review to general_ledger.
    Atomic: either ALL legs land in GL + posting row created,
    or NONE land (rollback).
    """
    
    # ── STEP 1: Load & validate ──
    review = db.ads_v2_spend_review.find_one({"id": review_id})
    assert review.review_status in ("pending", "reopened", *HELD_STATUSES)
    
    if review.review_status.startswith("held_"):
        if not force:
            raise HoldForceRequired(review.review_status)
        if actor.role != "owner":
            raise PermissionDenied("only owner can force held")
        if review.review_status == "held_unauthorized":
            raise PermissionDenied("fix OAuth first, force not allowed")
    
    # ── STEP 2: Idempotency check ──
    existing = db.general_ledger.find_one({
        "metadata.idempotency_key": review.idempotency_key,
        "status": "posted"
    })
    if existing:
        raise Conflict(f"already posted (txn_group={existing.txn_group_id})")
    
    # ── STEP 3: Re-validate snapshot ──
    if review.gross_sar_snapshot <= 0:
        raise InvalidAmount("gross_sar must be > 0")
    if review.fx_rate_snapshot is None or review.fx_rate_snapshot <= 0:
        raise InvalidFX("fx_rate missing")
    if review.spend_sar_snapshot is None:
        raise InvalidAmount("spend_sar must be computed")
    
    # ── STEP 4: Build legs ──
    legs = _build_legs(review)
    _validate_double_entry(legs)   # sum(debits) == sum(credits)
    
    # ── STEP 5: Atomic write (Mongo transaction) ──
    txn_group_id = str(uuid4())
    
    with db.client.start_session() as session:
        async with session.start_transaction():
            # Insert all legs to general_ledger
            for leg in legs:
                leg.update({
                    "txn_group_id": txn_group_id,
                    "status": "posted",
                    "posted_at": now_utc_iso(),
                    "metadata": {
                        **leg["metadata"],
                        "source": "ads_v2",
                        "ads_v2_review_id": review.id,
                        "idempotency_key": review.idempotency_key
                    }
                })
                await db.general_ledger.insert_one(leg, session=session)
            
            # Insert posting audit row
            posting_id = str(uuid4())
            await db.ads_v2_ledger_postings.insert_one({
                "id": posting_id,
                "review_id": review.id,
                "txn_group_id": txn_group_id,
                "user_id": review.user_id,
                "account_id": review.account_id,
                "date": review.date,
                "provider": review.provider,
                "amounts": {
                    "spend_sar": review.spend_sar_snapshot,
                    "bank_fee_sar": review.bank_fee_sar_snapshot,
                    "gross_sar": review.gross_sar_snapshot
                },
                "legs_summary": [leg_summary(l) for l in legs],
                "posted_at": now_utc_iso(),
                "posted_by": actor.id,
                "posted_via": "manual_approve",
                "reversed": False,
                "current_reversal_id": None
            }, session=session)
            
            # Update review
            await db.ads_v2_spend_review.update_one(
                {"id": review.id},
                {"$set": {
                    "review_status": "approved",
                    "decided_at": now_utc_iso(),
                    "decided_by": actor.id,
                    "posted_txn_group_id": txn_group_id,
                    "posted_at": now_utc_iso(),
                    "posting_id": posting_id
                }},
                session=session
            )
            
            # Log to history
            await db.ads_v2_review_history.insert_one({
                "id": str(uuid4()),
                "review_id": review.id,
                "user_id": review.user_id,
                "action": "approve",
                "from_status": review.review_status,
                "to_status": "approved",
                "actor_user_id": actor.id,
                "actor_email": actor.email,
                "at": now_utc_iso()
            }, session=session)
            
            # All good → commit (auto when context exits)
    
    return {
        "posting_id": posting_id,
        "txn_group_id": txn_group_id,
        "legs": legs
    }
```

### 3.1 `_build_legs(review)` التفصيل

```python
def _build_legs(review) -> list[dict]:
    legs = []
    common = {
        "user_id": review.user_id,
        "currency": "SAR",
        "metadata": {
            "account_id": review.account_id,
            "provider": review.provider,
            "spend_date": review.date,
            "currency_native": review.currency_native,
            "spend_native": review.spend_native_snapshot,
            "fx_rate": review.fx_rate_snapshot
        }
    }
    
    # Leg 1: Spend Debit
    legs.append({**common,
        "id": uuid4_hex(),
        "entry_type": "ads_v2_expense",
        "entity_type": "expense",
        "entity_id": f"advertising:{review.provider}",
        "side": "debit",
        "amount": review.spend_sar_snapshot
    })
    
    # Leg 2: Spend Credit
    legs.append({**common,
        "id": uuid4_hex(),
        "entry_type": "ads_v2_debt_credit",
        "entity_type": "counterparty",
        "entity_id": f"ad_account:{review.account_id}",
        "side": "credit",
        "amount": review.spend_sar_snapshot
    })
    
    # Bank fee (إذا موجود)
    if review.bank_fee_sar_snapshot > 0:
        legs.append({**common,
            "id": uuid4_hex(),
            "entry_type": "ads_v2_bank_fee",
            "entity_type": "expense",
            "entity_id": "bank_fee:advertising",
            "side": "debit",
            "amount": review.bank_fee_sar_snapshot
        })
        legs.append({**common,
            "id": uuid4_hex(),
            "entry_type": "ads_v2_debt_credit",
            "entity_type": "counterparty",
            "entity_id": f"ad_account:{review.account_id}",
            "side": "credit",
            "amount": review.bank_fee_sar_snapshot
        })
    
    return legs


def _validate_double_entry(legs):
    debits = sum(l["amount"] for l in legs if l["side"] == "debit")
    credits = sum(l["amount"] for l in legs if l["side"] == "credit")
    if abs(debits - credits) > 0.01:
        raise UnbalancedLedger(f"{debits} != {credits}")
```

---

## 4. Reversal Workflow

### 4.1 الـ Algorithm

```python
def reverse_posting(posting_id: str, reason: str, follow_up: str, actor: User) -> Reversal:
    """
    Reverses a posting by inserting MIRROR legs into GL.
    Mirror = same amounts, opposite sides.
    """
    
    # Load
    posting = db.ads_v2_ledger_postings.find_one({"id": posting_id})
    if posting.reversed:
        raise Conflict("already reversed")
    
    # Load original legs from GL
    original_legs = list(db.general_ledger.find({
        "txn_group_id": posting.txn_group_id,
        "status": "posted"
    }))
    
    # Build mirror
    new_txn_group_id = str(uuid4())
    mirror_legs = []
    for leg in original_legs:
        mirror_legs.append({
            "id": uuid4_hex(),
            "txn_group_id": new_txn_group_id,
            "user_id": leg["user_id"],
            "entry_type": leg["entry_type"],     # نفس entry_type
            "entity_type": leg["entity_type"],
            "entity_id": leg["entity_id"],
            "side": "credit" if leg["side"] == "debit" else "debit",  # ← العكس
            "amount": leg["amount"],
            "currency": leg["currency"],
            "status": "posted",
            "posted_at": now_utc_iso(),
            "metadata": {
                **leg["metadata"],
                "is_reversal": True,
                "reversal_of_txn_group_id": posting.txn_group_id,
                "reversal_of_leg_id": leg["id"],
                "reversal_reason": reason,
                "idempotency_key": f"ads_v2_rev:{posting_id}"
            }
        })
    
    # Atomic write
    with db.client.start_session() as session:
        async with session.start_transaction():
            
            # Insert mirror legs
            await db.general_ledger.insert_many(mirror_legs, session=session)
            
            # Insert reversal record
            reversal_id = str(uuid4())
            await db.ads_v2_reversals.insert_one({
                "id": reversal_id,
                "original_posting_id": posting_id,
                "user_id": posting.user_id,
                "reversal_txn_group_id": new_txn_group_id,
                "reason": reason,
                "reversed_at": now_utc_iso(),
                "reversed_by": actor.id,
                "reversal_type": "full",
                "amount_reversed_sar": posting.amounts.gross_sar,
                "legs_summary": [leg_summary(l) for l in mirror_legs],
                "follow_up_action": follow_up,
                "follow_up_completed": False
            }, session=session)
            
            # Mark posting as reversed
            await db.ads_v2_ledger_postings.update_one(
                {"id": posting_id},
                {"$set": {"reversed": True, "current_reversal_id": reversal_id}},
                session=session
            )
            
            # Follow-up action
            if follow_up == "reopen_review":
                await db.ads_v2_spend_review.update_one(
                    {"id": posting.review_id},
                    {"$set": {
                        "review_status": "reopened",
                        "posted_txn_group_id": None,
                        "posted_at": None,
                        "posting_id": None,
                        "decided_at": None,
                        "decided_by": None
                    }}
                )
                await db.ads_v2_review_history.insert_one({
                    "review_id": posting.review_id,
                    "action": "auto_reopen_after_reverse",
                    "from_status": "approved",
                    "to_status": "reopened",
                    "actor_user_id": actor.id,
                    "context": {"reversal_id": reversal_id, "reason": reason},
                    "at": now_utc_iso()
                }, session=session)
                
                # Mark follow-up as done
                await db.ads_v2_reversals.update_one(
                    {"id": reversal_id},
                    {"$set": {"follow_up_completed": True}},
                    session=session
                )
    
    return reversal_id
```

### 4.2 Re-approve بعد Reverse

بعد reverse، الـ review في حالة `reopened`. التاجر يستطيع `approve` مرة أخرى — يُنشأ posting **جديد** بـ `posting_id` و `txn_group_id` جديدين. الـ idempotency_key الأصلي لا يزال موجوداً في الـ reversed posting، لذلك لا تكرار.

**التطور التاريخي:**
```
Day 1: posting_id=P1, txn=T1 (approved)
Day 2: reversal_id=R1, txn=T2 (mirror of T1)
       review → reopened
Day 3: posting_id=P2, txn=T3 (re-approved)
```

في GL: T1 + T2 يلغيان بعضهما، T3 هو الصافي. كل شيء قابل للتتبع.

---

## 5. التحقق المحاسبي (Double-Entry Sanity)

### 5.1 لكل posting

`sum(debits) == sum(credits)` على نفس `txn_group_id`. هذا فحص قبل INSERT.

### 5.2 لكل ad_account (على مستوى الأرصدة)

```python
debt_balance = SUM(credit) - SUM(debit) 
               WHERE entity_id = f"ad_account:{account_id}"
               AND status = "posted"
```

يجب أن يكون:
- موجب (= التاجر مدين) أو
- صفر (= مدفوع بالكامل)

سالب = bug (أكثر دفع من الصرف) → تنبيه في `/ads-v2/diagnostics/`.

### 5.3 لكل ad_provider

```python
total_expense = SUM(amount)
                WHERE entry_type = "ads_v2_expense"
                AND entity_id LIKE f"advertising:{provider}:%"
                AND status = "posted"
```

يجب أن يطابق `SUM(spend_sar)` من `ads_v2_spend_daily` للأيام المعتمدة فقط.

### 5.4 Reconciliation Endpoint (`/ads-v2/reports/reconciliation/{account_id}`)

يكشف أي drift بين spend_daily و GL. **يجب أن يكون drift = 0** للأيام المعتمدة. أي drift = bug.

---

## 6. متطلبات MongoDB

> **هام:** Multi-document transactions تتطلب MongoDB Replica Set.

- إذا كان الإنتاج يعمل على Standalone MongoDB → نحتاج switch لـ Replica Set
- البديل: تنفيذ atomic write يدوياً مع **compensating actions** عند الفشل (روتين أصعب)

**القرار المطلوب:** هل MongoDB في الإنتاج Replica Set؟ إذا لا → نحتاج خطة لـ migration أو نتبنّى compensating actions.

---

## 7. Audit Trail Summary

كل posting يترك أثره في 4 أماكن:

1. **`general_ledger`** — القيد الفعلي (2-4 legs)
2. **`ads_v2_ledger_postings`** — Audit row بـ legs_summary
3. **`ads_v2_spend_review`** — حالة approved + posting_id + txn_group_id
4. **`ads_v2_review_history`** — action='approve' مع actor + timestamp

أي عملية reverse تترك إضافياً:

5. **`ads_v2_reversals`** — Reversal audit
6. **`general_ledger`** — Mirror legs بـ `is_reversal=true`

---

## 8. Performance & Concurrency

### 8.1 Locks

كل review له `idempotency_key` فريد. عند approve:
1. Mongo unique index على `idempotency_key` في `general_ledger.metadata.idempotency_key` يضمن لا double-post
2. Transaction يضمن لا split legs

### 8.2 Bulk approve performance

50 سطر = 50 transactions sequential (آمن لكن بطيء ~5 ثوان).  
البديل: queue async + status polling. للإصدار الأول → sequential.

---

## 9. Edge Cases

| الحالة | المعالجة |
|---|---|
| FX rate = 0 | reject في validation قبل posting |
| spend_native = 0 | يُمنع approve (Conflict — لا قيد بقيمة 0) |
| spend_native < 0 (refund من المنصة) | يُسمح، يُنشئ قيد عكسي طبيعي (credit expense, debit counterparty) |
| Provider يُرجع currency مختلف عن account.currency_native | reconciliation يضع flag `provider_currency_mismatch` → held |
| Bank fee أكبر من spend (نادر) | يُسمح، الـ gross يكون أعلى من spend |
| Reverse لـ reverse (re-reverse) | يُعامل كـ posting جديد، يحفظ chain من reversals |

---

**✍️ التعديلات المطلوبة:** هل entry_types مناسبة؟ هل posting paths سليمة محاسبياً؟ هل MongoDB في production replica set؟
