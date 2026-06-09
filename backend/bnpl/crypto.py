"""Fernet helpers — re-use the Salla encryption key.

Same key (`SALLA_TOKEN_ENC_KEY` from backend/.env) is reused because the
threat-model is identical: opaque ciphertext stored in Mongo, never
logged in plaintext.  Re-export through this thin wrapper so BNPL code
doesn't import from the Salla package directly (decoupling).
"""
from salla_integration.crypto import encrypt_token, decrypt_token

__all__ = ["encrypt_token", "decrypt_token"]
