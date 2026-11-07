"""Loopback-only synthetic OTP inbox; never delivers or logs mail contents.

Read /tmp/staging-mail/latest.eml through the service's authenticated shell.
No HTTP inbox and no real SMTP credentials. Ephemeral test messages only.
"""
import asyncore
import os
from pathlib import Path
import smtpd


class Capture(smtpd.SMTPServer):
    def process_message(self, peer, mailfrom, rcpttos, data, **kwargs):
        directory = Path('/tmp/staging-mail')
        directory.mkdir(mode=0o700, exist_ok=True)
        with (directory / 'latest.eml').open('wb') as message:
            message.write(data)


if __name__ == '__main__':
    os.umask(0o077)
    Capture(('127.0.0.1', 8025), None, data_size_limit=1024*1024, decode_data=False)
    asyncore.loop()
