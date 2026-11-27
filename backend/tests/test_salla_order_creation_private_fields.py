"""Synthetic encrypted Mezan temporary customer field vault — never live PII."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet, InvalidToken

from salla_order_creation.private_fields import (
    PrivateOrderFieldDenied, FIELDS, _safe_value,
    require_private_fields_ttl, install_private_fields_ttl,
    save_private_order_field, load_private_order_fields,
    require_unchanged_private_vault,
)

NOW = datetime.fromtimestamp(1800000000, tz=timezone.utc)
TTL = "salla_assisted_private_fields_ttl_v1"


class Collection:
    def __init__(self, indexes=False):
        self.rows = []
        self.indices = (
            {TTL: {"key": [("purge_after", 1)], "expireAfterSeconds": 0}}
            if indexes else {}
        )
        self.created = []

    async def index_information(self):
        return self.indices

    async def create_index(self, keys, **options):
        self.created.append((keys, options))
        self.indices[options["name"]] = {
            "key": list(keys),
            "expireAfterSeconds": options["expireAfterSeconds"],
        }

    async def find_one(self, query):
        for row in self.rows:
            if all(row.get(k) == v for k, v in query.items()):
                return dict(row)
        return None

    async def insert_one(self, row):
        if any(r["_id"] == row["_id"] for r in self.rows):
            raise ValueError("synthetic duplicate")
        self.rows.append(dict(row))

    async def update_one(self, query, data):
        for row in self.rows:
            if all(row.get(k) == v for k, v in query.items()):
                row.update(data["$set"])
                row["revision"] += data["$inc"]["revision"]
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


class DB:
    def __init__(self, ttl=True):
        self.salla_ai_private_order_fields = Collection(indexes=ttl)
        self.salla_ai_order_quotes = Collection()
        self.salla_ai_order_intents = Collection()


def save(db, cipher, **changes):
    payload = {
        "owner_id": "owner", "account_id": 1, "inbox_id": 2,
        "conversation_id": 3, "intent_id": "intent-0001",
        "contact_id": 77, "message_id": 101,
        "field": "recipient_name", "value": "عميل تجريبي",
        "now": NOW, "cipher": cipher,
    }
    payload.update(changes)
    return asyncio.run(save_private_order_field(db, **payload))


def load(db, cipher, **changes):
    payload = {
        "owner_id": "owner", "account_id": 1, "inbox_id": 2,
        "conversation_id": 3, "intent_id": "intent-0001",
        "contact_id": 77, "cipher": cipher, "now": NOW,
    }
    payload.update(changes)
    return asyncio.run(load_private_order_fields(db, **payload))


@pytest.fixture
def cipher():
    return Fernet(Fernet.generate_key())


def test_encrypted_field_only_and_safe_metadata(cipher):
    db = DB()
    result = save(db, cipher)
    assert result["fields_present"] == ["recipient_name"]
    assert result["revision"] == 1
    assert "عميل تجريبي" not in str(result)
    record = db.salla_ai_private_order_fields.rows[0]
    assert isinstance(record["encrypted_fields"], bytes)
    assert "عميل تجريبي" not in str(record)
    assert "recipient_name" in record["field_message_ids"]
    assert load(db, cipher) == {"recipient_name": "عميل تجريبي"}


def test_same_customer_message_replay_no_reencryption(cipher):
    db = DB()
    save(db, cipher)
    before = db.salla_ai_private_order_fields.rows[0]["encrypted_fields"]
    replay = save(db, cipher)
    assert replay["replayed"] is True
    assert replay["revision"] == 1
    assert db.salla_ai_private_order_fields.rows[0]["encrypted_fields"] == before


def test_conflicting_same_message_field_value_denied(cipher):
    db = DB()
    save(db, cipher)
    with pytest.raises(PrivateOrderFieldDenied, match="same_message_field_conflict"):
        save(db, cipher, value="اسم غير مطابق")


def test_latest_customer_can_add_second_private_field(cipher):
    db = DB()
    save(db, cipher)
    result = save(
        db, cipher, field="recipient_phone", value="0500000000",
        message_id=102,
    )
    assert result["fields_present"] == ["recipient_name", "recipient_phone"]
    assert load(db, cipher)["recipient_phone"] == "+966500000000"
    assert result["revision"] == 2


def test_same_message_can_supply_multiple_distinct_fields(cipher):
    db = DB()
    save(db, cipher)
    result = save(db, cipher, field="recipient_phone",
                  value="+966500000000", message_id=101)
    assert result["revision"] == 2
    assert load(db, cipher)["recipient_phone"] == "+966500000000"


def test_stale_message_does_not_overwrite_newer_field(cipher):
    db = DB()
    save(db, cipher)
    save(db, cipher, field="recipient_phone", value="0500000000",
         message_id=102)
    with pytest.raises(PrivateOrderFieldDenied, match="stale_customer_field_message"):
        save(db, cipher, field="recipient_name",
             value="اسم قديم", message_id=100)


@pytest.mark.parametrize("change", [
    {"account_id": 55},
    {"inbox_id": 99},
    {"conversation_id": 44},
    {"contact_id": 33},
    {"owner_id": "other"},
])
def test_wrong_scope_or_contact_not_allowed_to_read(cipher, change):
    db = DB()
    save(db, cipher)
    with pytest.raises(PrivateOrderFieldDenied):
        load(db, cipher, **change)


def test_existing_intent_cannot_be_reassigned_to_different_contact(cipher):
    db = DB()
    save(db, cipher)
    with pytest.raises(PrivateOrderFieldDenied, match="private_field_scope_conflict"):
        save(db, cipher, contact_id=99, message_id=103)


def test_private_fields_freeze_when_verified_quote_exists(cipher):
    db = DB()
    save(db, cipher)
    db.salla_ai_order_quotes.rows.append({
        "user_id": "owner", "intent_id": "intent-0001",
    })
    with pytest.raises(PrivateOrderFieldDenied, match="frozen"):
        save(db, cipher, field="buyer_email", value="buyer@example.invalid",
             message_id=103)


def test_private_fields_freeze_after_salla_order_intent(cipher):
    db = DB()
    save(db, cipher)
    db.salla_ai_order_intents.rows.append({
        "user_id": "owner", "intent_id": "intent-0001",
    })
    with pytest.raises(PrivateOrderFieldDenied, match="frozen"):
        save(db, cipher, field="buyer_email", value="buyer@example.invalid",
             message_id=103)


def test_missing_ttl_index_denies_before_writing_any_pii(cipher):
    db = DB(ttl=False)
    with pytest.raises(PrivateOrderFieldDenied, match="ttl_missing"):
        save(db, cipher)
    assert db.salla_ai_private_order_fields.rows == []


def test_expired_vault_record_rejected_even_before_background_ttl_removal(cipher):
    db = DB()
    save(db, cipher)
    with pytest.raises(PrivateOrderFieldDenied, match="expired"):
        load(db, cipher, now=NOW + timedelta(days=8))
    with pytest.raises(PrivateOrderFieldDenied, match="expired"):
        save(db, cipher, now=NOW + timedelta(days=8),
             field="buyer_email", value="buyer@example.invalid",
             message_id=110)


def test_unauthorized_private_value_types_and_pci_denied(cipher):
    db = DB()
    for field, value in (
        ("cc_number", "4111111111111111"),
        ("buyer_mobile", "123456"),
        ("buyer_email", "invalid@"),
        ("recipient_name", "رمز التحقق 12345"),
        ("recipient_name", "<html>"),
        ("recipient_name", "name\nextra"),
        ("postal_code", "../secret"),
    ):
        with pytest.raises(PrivateOrderFieldDenied):
            save(db, cipher, field=field, value=value)


def test_wrong_cipher_does_not_return_plaintext(cipher):
    db = DB()
    save(db, cipher)
    with pytest.raises(PrivateOrderFieldDenied, match="integrity"):
        load(db, Fernet(Fernet.generate_key()))


def test_private_ttl_installer_is_explicit_not_request_time(cipher):
    db = DB(ttl=False)
    with pytest.raises(PrivateOrderFieldDenied):
        asyncio.run(require_private_fields_ttl(db))
    asyncio.run(install_private_fields_ttl(db))
    assert db.salla_ai_private_order_fields.created == [
        ([("purge_after", 1)],
         {"expireAfterSeconds": 0, "name": TTL}),
    ]
    save(db, cipher)
    assert len(db.salla_ai_private_order_fields.rows) == 1



def test_dynamic_engraving_option_id_is_encrypted_not_plaintext(cipher):
    db=DB()
    result=save(
        db,cipher,field="option_text_501",
        value="اسم هدية خاص",message_id=101,
    )
    assert result["fields_present"] == ["option_text_501"]
    assert "اسم هدية خاص" not in str(result)
    row=db.salla_ai_private_order_fields.rows[0]
    assert "اسم هدية خاص" not in str(row)
    assert load(db,cipher)["option_text_501"]=="اسم هدية خاص"


@pytest.mark.parametrize("field",[
    "option_text_0","option_text_-1","option_text_001",
    "option_text_1; DROP TABLE","option_text_abc",
])
def test_invalid_dynamic_option_names_denied(cipher,field):
    db=DB()
    with pytest.raises(PrivateOrderFieldDenied,match="untrusted_private_order_field"):
        save(db,cipher,field=field,value="اسم خاص")


def test_oversized_engraving_or_html_is_not_accepted(cipher):
    db=DB()
    with pytest.raises(PrivateOrderFieldDenied):
        save(db,cipher,field="option_text_501",value="أ"*501)
    with pytest.raises(PrivateOrderFieldDenied):
        save(db,cipher,field="option_text_501",value="<script>")



def test_short_street_number_and_private_geo_coordinates_stored_encrypted(cipher):
    db=DB()
    save(db,cipher,field="street_number",value="11")
    save(db,cipher,field="geo_lat",value="24.7000",message_id=102)
    save(db,cipher,field="geo_lng",value="46.7000",message_id=103)
    values=load(db,cipher)
    assert values["street_number"]=="11"
    assert values["geo_lat"]=="24.7000"
    assert values["geo_lng"]=="46.7000"
    assert "24.7000" not in str(db.salla_ai_private_order_fields.rows[0])


def test_out_of_range_encrypted_geo_coordinates_denied(cipher):
    db=DB()
    with pytest.raises(PrivateOrderFieldDenied):
        save(db,cipher,field="geo_lat",value="100.01")



def test_private_snapshot_revision_and_ref_roundtrip(cipher):
    db=DB()
    save(db,cipher,field="recipient_name",value="عميل تجريبي")
    meta=asyncio.run(load_private_order_fields(
        db,owner_id="owner",account_id=1,inbox_id=2,
        conversation_id=3,intent_id="intent-0001",contact_id=77,
        cipher=cipher,now=NOW,with_revision=True,
    ))
    assert meta["revision"]==1
    assert meta["vault_ref"].startswith("vault.")
    assert meta["fields"]["recipient_name"]=="عميل تجريبي"
    assert "عميل تجريبي" not in str({
        "revision":meta["revision"],
        "vault_ref":meta["vault_ref"],
    })
    asyncio.run(require_unchanged_private_vault(
        db,owner_id="owner",account_id=1,inbox_id=2,
        conversation_id=3,intent_id="intent-0001",contact_id=77,
        expected_revision=1,expected_vault_ref=meta["vault_ref"],now=NOW,
    ))


def test_private_field_edit_invalidates_prior_verified_snapshot(cipher):
    db=DB()
    save(db,cipher,field="recipient_name",value="عميل تجريبي")
    meta=asyncio.run(load_private_order_fields(
        db,owner_id="owner",account_id=1,inbox_id=2,
        conversation_id=3,intent_id="intent-0001",contact_id=77,
        cipher=cipher,now=NOW,with_revision=True,
    ))
    save(db,cipher,field="recipient_phone",value="0500000000",message_id=102)
    with pytest.raises(PrivateOrderFieldDenied,match="private_vault_changed_after_quote"):
        asyncio.run(require_unchanged_private_vault(
            db,owner_id="owner",account_id=1,inbox_id=2,
            conversation_id=3,intent_id="intent-0001",contact_id=77,
            expected_revision=meta["revision"],
            expected_vault_ref=meta["vault_ref"],now=NOW,
        ))


def test_wrong_or_expired_vault_reference_never_authorizes_create(cipher):
    db=DB()
    save(db,cipher)
    for ref,contact,clock in (
        ("vault."+"b"*32,77,NOW),
        ("vault."+"a"*32,99,NOW),
        ("vault."+"a"*32,77,NOW+timedelta(days=8)),
    ):
        with pytest.raises(PrivateOrderFieldDenied):
            asyncio.run(require_unchanged_private_vault(
                db,owner_id="owner",account_id=1,inbox_id=2,
                conversation_id=3,intent_id="intent-0001",
                contact_id=contact,expected_revision=1,
                expected_vault_ref=ref,now=clock,
            ))
