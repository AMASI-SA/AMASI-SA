"""Scoped Chatwoot sender identity read: never trust request-supplied phones."""
import asyncio
import unittest
import httpx

from backend.order_engine.whatsapp_sender_reader import (
    ChatwootVerifiedSenderReader, VerifiedSenderUnavailable
)
HOST="chatwoot.example.com"
TOKEN="testbot-token-1234567890123456"

def run(response):
    async def scenario():
        def handler(request):
            assert request.url.host==HOST
            assert request.url.path=="/api/v1/accounts/1/conversations/9/messages"
            assert request.url.params["mezan_verified_buyer"]=="1"
            assert request.url.params["mezan_message_id"]=="101"
            assert request.headers["api_access_token"]==TOKEN
            return response
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reader=ChatwootVerifiedSenderReader(
                base_url="https://"+HOST,approved_hostname=HOST,
                agentbot_token=TOKEN,http_client=client)
            return await reader(9,101)
    return asyncio.run(scenario())


class SenderReaderTest(unittest.TestCase):
    def test_verified_contact_inbox_identity(self):
        result=run(httpx.Response(200,json={
            "schema":"amasi_verified_whatsapp_buyer_v1",
            "buyer_mobile":"966550000001","latest_message_id":101,
        }))
        self.assertEqual(result,"966550000001")

    def test_wrong_mobile_scope_and_server_denial(self):
        for payload in (
            {"schema":"amasi_verified_whatsapp_buyer_v1",
             "buyer_mobile":"966550000001","latest_message_id":102},
            {"schema":"amasi_verified_whatsapp_buyer_v1",
             "buyer_mobile":"not-a-mobile","latest_message_id":101},
            {"schema":"amasi_verified_whatsapp_buyer_v1",
             "buyer_mobile":"966550000001","latest_message_id":101,
             "customer_email":"must-not-appear"},
        ):
            with self.subTest(payload=payload),self.assertRaises(VerifiedSenderUnavailable):
                run(httpx.Response(200,json=payload))
        for status in (302,401,403,404,429,500):
            with self.subTest(status=status),self.assertRaises(VerifiedSenderUnavailable):
                run(httpx.Response(status))

    def test_no_unapproved_host(self):
        for url in ("http://"+HOST, "https://evil.example",
                    "https://"+HOST+"/api/v1", "https://"+HOST+"?x=1"):
            with self.assertRaises(ValueError):
                ChatwootVerifiedSenderReader(base_url=url,
                    approved_hostname=HOST,agentbot_token=TOKEN)

if __name__=="__main__":
    unittest.main()
