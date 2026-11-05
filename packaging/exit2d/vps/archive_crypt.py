"""CI-only bounded synthetic archive encryption; offsite production uses restic.

Key file never printed or uploaded. Full-buffer Fernet deliberately bounded to
this<100MiB fixture, not a streaming backup system for real business databases.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
from cryptography.fernet import Fernet, InvalidToken

phase, source, target, key_path = sys.argv[1:]
source, target, key_path = map(Path, (source, target, key_path))
assert source.stat().st_size < 150*1024*1024, 'bounded synthetic fixture only'
if phase == 'encrypt':
    key = Fernet.generate_key()
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(key)
    original = source.read_bytes()
    encrypted = Fernet(key).encrypt(original)
    target.write_bytes(encrypted)
    wrong_key_refused = tamper_refused = False
    try:
        Fernet(Fernet.generate_key()).decrypt(encrypted)
    except InvalidToken:
        wrong_key_refused = True
    damaged = bytearray(encrypted); damaged[len(damaged)//2] ^= 1
    try:
        Fernet(key).decrypt(bytes(damaged))
    except InvalidToken:
        tamper_refused = True
    assert wrong_key_refused and tamper_refused
    print(json.dumps({'kind': 'encrypted_synthetic_archive',
        'plain_sha256': hashlib.sha256(original).hexdigest(), 'encrypted_sha256': hashlib.sha256(encrypted).hexdigest(),
        'plain_bytes': len(original), 'encrypted_bytes': len(encrypted),
        'wrong_key_rejected': True, 'tamper_rejected': True}), flush=True)
elif phase == 'decrypt':
    restored = Fernet(key_path.read_bytes()).decrypt(source.read_bytes())
    target.write_bytes(restored)
    print(json.dumps({'kind': 'decrypted_synthetic_archive',
        'plain_sha256': hashlib.sha256(restored).hexdigest(), 'plain_bytes': len(restored)}), flush=True)
else:
    raise RuntimeError('unknown synthetic crypto phase')
