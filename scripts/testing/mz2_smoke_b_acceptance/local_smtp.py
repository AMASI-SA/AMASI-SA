"""Ephemeral loopback SMTP delivery receiver; message contents never leave RAM."""
import asyncio


class LocalSMTP:
    def __init__(self):
        self.server = None
        self.messages = []

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        self.messages.clear()

    async def handle(self, reader, writer):
        recipients, data, receiving = [], [], False
        async def reply(value):
            writer.write(value + b"\r\n")
            await writer.drain()
        try:
            await reply(b"220 localhost Acceptance SMTP")
            while line := await asyncio.wait_for(reader.readline(), timeout=20):
                line = line.rstrip(b"\r\n")
                if receiving:
                    if line == b".":
                        self.messages.append({"recipients": recipients[:], "content": b"\r\n".join(data)})
                        receiving, data = False, []
                        await reply(b"250 Message received locally")
                    else:
                        data.append(line[1:] if line.startswith(b"..") else line)
                    continue
                command = line.split(b" ", 1)[0].upper()
                if command in {b"EHLO", b"HELO", b"NOOP", b"RSET"}:
                    if command == b"RSET":
                        recipients, data = [], []
                    await reply(b"250 localhost")
                elif command == b"MAIL":
                    recipients = []
                    await reply(b"250 Sender accepted")
                elif command == b"RCPT":
                    recipients.append(line.decode("utf-8", errors="replace"))
                    await reply(b"250 Local recipient accepted")
                elif command == b"DATA":
                    receiving = True
                    await reply(b"354 End with a single dot")
                elif command == b"QUIT":
                    await reply(b"221 Closing")
                    break
                else:
                    await reply(b"502 Unsupported command")
        except (TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
