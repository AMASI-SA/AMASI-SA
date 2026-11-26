"""Mezan read-only Chatwoot latest WhatsApp sender proof.

An explicitly configured service client queries the owner-controlled pinned
Chatwoot CE bot endpoint. A customer/order number or language-model output
can NEVER supply buyer_mobile. This module does not register an API route.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx

SOURCE_PATH = "/api/v1/accounts/1/conversations/"
MOBILE = re.compile(r"9665[0-9]{8}\Z", re.ASCII)
DOMAIN = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z", re.ASCII)


class VerifiedSenderUnavailable(ValueError):
    pass


class ChatwootVerifiedSenderReader:
    def __init__(self, *, base_url: str, approved_hostname: str,
                 agentbot_token: str, http_client=None):
        parsed=urlsplit(base_url)
        if (type(base_url) is not str or type(approved_hostname) is not str
                or DOMAIN.fullmatch(approved_hostname) is None
                or parsed.scheme!="https" or parsed.hostname!=approved_hostname
                or parsed.port not in (None,443) or parsed.username
                or parsed.password or parsed.path not in ("","/")
                or parsed.query or parsed.fragment
                or type(agentbot_token) is not str
                or not re.fullmatch(r"[A-Za-z0-9_-]{24,256}",agentbot_token)):
            raise ValueError("invalid_verified_sender_configuration")
        self._hostname=approved_hostname
        self._token=agentbot_token
        self._http_client=http_client

    async def __call__(self, conversation_id: int, message_id: int) -> str:
        if (type(conversation_id) is not int or conversation_id<=0
                or type(message_id) is not int or message_id<=0):
            raise VerifiedSenderUnavailable("whatsapp_sender_unavailable")
        path=f"{SOURCE_PATH}{conversation_id}/messages"
        params={"mezan_verified_buyer":"1","mezan_message_id":str(message_id)}
        headers={"api_access_token":self._token,"Accept":"application/json"}
        try:
            if self._http_client is None:
                async with httpx.AsyncClient(timeout=5,follow_redirects=False,
                                             trust_env=False) as client:
                    response=await client.get(
                        "https://"+self._hostname+path,params=params,headers=headers)
            else:
                response=await self._http_client.get(
                    "https://"+self._hostname+path,params=params,headers=headers)
            if response.status_code != 200 or len(response.content)>1024:
                raise VerifiedSenderUnavailable("whatsapp_sender_unavailable")
            result=response.json()
            if (type(result) is not dict
                    or set(result)!={"schema","buyer_mobile","latest_message_id"}
                    or result.get("schema")!="amasi_verified_whatsapp_buyer_v1"
                    or result.get("latest_message_id")!=message_id
                    or type(result.get("buyer_mobile")) is not str
                    or MOBILE.fullmatch(result["buyer_mobile"]) is None):
                raise VerifiedSenderUnavailable("whatsapp_sender_unavailable")
            return result["buyer_mobile"]
        except VerifiedSenderUnavailable:
            raise
        except (httpx.HTTPError,ValueError,TypeError,AttributeError):
            raise VerifiedSenderUnavailable("whatsapp_sender_unavailable") from None
