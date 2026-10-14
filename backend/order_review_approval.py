"""Signed evidence for the displayed review; no persistence or provider IO."""
import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import HTTPException
from order_review_business_snapshot import build_snapshot, REVIEW_SCOPE_VERSION
from order_review_acceptance_snapshot import fingerprint

TTL_SECONDS = 1800
DOMAIN = b"mezan.order-review.display-approval.v1\x00"


def reject(code="component_source_event_stale"):
    raise HTTPException(409, detail={"code": code, "refresh_required": True,
        "message": "تغيرت بيانات الاعتماد أو انتهت صلاحية عرضها. حدّث الشاشة وراجع البيانات ثم وافق مجددًا."})


def _key():
    secret = os.environ.get("JWT_SECRET", "")
    if not secret:
        raise HTTPException(503, detail={"code": "review_approval_signing_unavailable"})
    return hmac.new(secret.encode(), DOMAIN, hashlib.sha256).digest()


def approval_fingerprint(source, order, acceptance, workflow, identities, *, user_id):
    from order_review_completion import workflow_fingerprint
    snapshot = build_snapshot(source, order, acceptance,
        identity={"user_id": user_id, "order_number": order.order_number},
        schema_version=REVIEW_SCOPE_VERSION)
    # Bind catalogue-resolved identities as displayed, not SKU/name alone.
    items = [{k: v for k, v in item.model_dump(mode="json").items()
              if k not in {"image_url", "image_urls", "product_url"}}
             for item in identities]
    return fingerprint({"business": snapshot["canonical_hash"],
                        "workflow": workflow_fingerprint(workflow), "items": items})


def issue_token(digest, *, user_id, order_number, revision):
    payload = {"v": 1, "merchant": user_id, "order": order_number,
               "revision": revision, "fingerprint": digest, "expires": int(time.time()) + TTL_SECONDS}
    encoded = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).rstrip(b"=")
    signature = hmac.new(_key(), DOMAIN + encoded, hashlib.sha256).hexdigest()
    return encoded.decode() + "." + signature


def verify_token(token, *, user_id, order_number, revision):
    if not token:
        reject("review_approval_required")
    try:
        if not isinstance(token, str) or len(token) > 2048:
            raise ValueError()
        encoded, signature = token.split(".")
        expected = hmac.new(_key(), DOMAIN + encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if (payload["v"] != 1 or payload["merchant"] != user_id or payload["order"] != order_number
                or payload["revision"] != revision or type(payload["expires"]) is not int
                or payload["expires"] < time.time() or not isinstance(payload["fingerprint"], str)):
            raise ValueError()
        return payload["fingerprint"]
    except (ValueError, KeyError, TypeError, UnicodeError):
        reject()


class ReadSnapshot:
    """Read-only session binding for rendering one consistent review detail."""
    def __init__(self, db, session):
        self.db, self.session = db, session

    def __getitem__(self, name):
        collection, session = self.db[name], self.session
        class Reads:
            def __getattr__(self, method):
                if method not in {"find", "find_one", "aggregate", "count_documents", "distinct"}:
                    raise RuntimeError("review detail snapshot is read only")
                return lambda *args, **kwargs: getattr(collection, method)(*args, session=session, **kwargs)
        return Reads()

    def __getattr__(self, name):
        return self[name]
