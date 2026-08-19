from __future__ import annotations

import socket

import campaign_ai_ad_creative_media_v3 as media
from campaign_ai_visual_evidence_v3 import combined_visuals, responses_input


def _public_dns(*_args, **_kwargs):
    return [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
    ]


def _private_dns(*_args, **_kwargs):
    return [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ]


def test_provider_media_url_must_be_public_https(monkeypatch):
    monkeypatch.setattr(media.socket, "getaddrinfo", _public_dns)
    assert media._public_https_url("https://cdn.example.com/ad.jpg") == "https://cdn.example.com/ad.jpg"
    assert media._public_https_url("http://cdn.example.com/ad.jpg") is None
    assert media._public_https_url("https://user:pass@cdn.example.com/ad.jpg") is None

    monkeypatch.setattr(media.socket, "getaddrinfo", _private_dns)
    assert media._public_https_url("https://internal.example/ad.jpg") is None


def test_snap_media_payload_extracts_nested_media():
    payload = {
        "media": [
            {
                "sub_request_status": "SUCCESS",
                "media": {
                    "id": "media-1",
                    "type": "VIDEO",
                    "media_status": "READY",
                },
            }
        ]
    }
    assert media._extract_snap_media(payload)["id"] == "media-1"
    assert media._extract_snap_media(payload)["type"] == "VIDEO"


def test_meta_video_ids_are_bounded_and_deduplicated():
    creative = {
        "object_story_spec": {"video_data": {"video_id": "v1"}},
        "asset_feed_spec": {
            "videos": [
                {"video_id": "v1"},
                {"video_id": "v2"},
                {"video_id": "v3"},
                {"video_id": "v4"},
            ]
        },
    }
    assert media._video_ids_from_creative(creative) == ["v1", "v2", "v3"]


def test_actual_ad_visuals_are_sent_before_product_visuals():
    pack = {
        "actual_creative_media": {
            "entities": {
                "snapchat|ad||ad-1": {
                    "provider": "snapchat",
                    "media_available": True,
                    "visuals": [
                        {
                            "ad_id": "ad-1",
                            "creative_id": "creative-1",
                            "media_id": "media-1",
                            "image_url": "https://cdn.example.com/actual.jpg",
                            "visual_role": "actual_ad_video_thumbnail",
                            "source": "snapchat_marketing_api_media",
                        }
                    ],
                }
            }
        },
        "product_intelligence": {
            "entities": {
                "snapchat|ad||ad-1": {
                    "products": [
                        {
                            "product_id": "p1",
                            "product_name": "Product",
                            "main_image": "https://cdn.example.com/product.jpg",
                            "images": [],
                            "page_probe": {},
                        }
                    ]
                }
            }
        },
    }

    visuals = combined_visuals(pack)
    assert visuals[0]["kind"] == "actual_ad_creative"
    assert visuals[0]["image_url"].endswith("actual.jpg")
    assert visuals[1]["kind"] == "product_page_visual"

    response_input, count = responses_input({"evidence_pack": pack}, pack, include_images=True)
    assert count == 2
    content = response_input[0]["content"]
    labels = [row["text"] for row in content if row.get("type") == "input_text"]
    assert any("ACTUAL provider ad creative visual" in label for label in labels)
    assert any("Do not claim unseen video moments" in label for label in labels)


def test_missing_actual_media_does_not_create_visual_claim():
    pack = {
        "actual_creative_media": {
            "entities": {
                "meta|ad||ad-2": {
                    "provider": "meta",
                    "media_available": False,
                    "visuals": [],
                    "limitations": ["creative_media_unavailable:no_safe_visual_asset"],
                }
            }
        },
        "product_intelligence": {"entities": {}},
    }
    assert combined_visuals(pack) == []
    response_input, count = responses_input({"evidence_pack": pack}, pack, include_images=True)
    assert count == 0
    assert all(row.get("type") != "input_image" for row in response_input[0]["content"])
