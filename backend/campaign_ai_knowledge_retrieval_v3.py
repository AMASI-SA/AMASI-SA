"""Marketing Intelligence Knowledge Base for Campaign AI Decision Intelligence V3.

The knowledge base stores short, original summaries and source metadata rather
than copying articles/transcripts.  Retrieval is evidence support, not a rules
engine: no source can force an action and OpenAI remains the final diagnostic
and marketing decision authority.

Tier 1 = official platform documentation/case studies.
Tier 2 = high-quality practitioner/research sources.
Tier 3 = interviews/podcasts/case-study material ingested after review.

Embeddings are optional at runtime.  When OPENAI_API_KEY is available, bounded
chunks can use text-embedding-3-small.  If embeddings are unavailable, retrieval
falls back to lexical relevance with the same reliability/recency metadata.
"""
from __future__ import annotations

import math
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

try:
    from openai import AsyncOpenAI
except ImportError:  # focused tests may omit the SDK
    AsyncOpenAI = None


SOURCE_COLLECTION = "mezan_marketing_knowledge_sources_v1"
CHUNK_COLLECTION = "mezan_marketing_knowledge_chunks_v1"
EMBEDDING_MODEL = os.environ.get("MEZAN_MARKETING_KB_EMBEDDING_MODEL", "text-embedding-3-small")
MAX_RETRIEVED_CHUNKS = 10
MAX_SOURCE_CHARS = 80_000
MAX_CHUNK_CHARS = 1800
CHUNK_OVERLAP_CHARS = 220
LAST_REVIEWED = "2026-08-19"


# Short, original summaries only.  These are intentionally reasoning principles,
# not action thresholds.  Source text itself is never copied into the seed set.
CURATED_SOURCES: tuple[dict[str, Any], ...] = (
    {
        "source_id": "meta-delivery-learning-official",
        "title": "Meta delivery status and learning",
        "url": "https://www.facebook.com/business/help/316478108955072",
        "source_tier": 1,
        "publisher": "Meta",
        "published_at": None,
        "last_reviewed_at": LAST_REVIEWED,
        "reliability": "official",
        "topics": ["meta", "delivery", "learning", "campaign_structure"],
        "summary": (
            "Meta documents a learning phase in which delivery is exploring and performance can be less stable. "
            "Significant edits can affect delivery state, so learning status is evidence that should be considered before diagnosing a campaign as structurally bad."
        ),
    },
    {
        "source_id": "meta-performance-five-official",
        "title": "Meta Performance 5",
        "url": "https://www.facebook.com/business/m/meta-performance-5",
        "source_tier": 1,
        "publisher": "Meta",
        "published_at": None,
        "last_reviewed_at": LAST_REVIEWED,
        "reliability": "official",
        "topics": ["meta", "creative_strategy", "measurement", "data_quality", "campaign_structure"],
        "summary": (
            "Meta's performance guidance emphasizes account simplification, creative diversification, high-quality conversion data, and systematic measurement. "
            "These are diagnostic dimensions rather than universal pause or scale rules."
        ),
    },
    {
        "source_id": "meta-video-insights-official",
        "title": "Meta video insight metrics",
        "url": "https://developers.facebook.com/docs/marketing-api/insights/parameters/v24.0",
        "source_tier": 1,
        "publisher": "Meta",
        "published_at": None,
        "last_reviewed_at": LAST_REVIEWED,
        "reliability": "official",
        "topics": ["meta", "video", "creative_strategy", "measurement"],
        "summary": (
            "Meta exposes video-consumption and advertising insight metrics that can be combined with clicks and conversion events. "
            "Attention metrics should be interpreted together with downstream shopping and purchase outcomes."
        ),
    },
    {
        "source_id": "meta-budget-bidding-official",
        "title": "Meta budget and bid strategy guidance",
        "url": "https://www.facebook.com/business/ads/pricing",
        "source_tier": 1,
        "publisher": "Meta",
        "published_at": None,
        "last_reviewed_at": LAST_REVIEWED,
        "reliability": "official",
        "topics": ["meta", "budget", "bidding", "delivery", "scaling"],
        "summary": (
            "Meta notes that daily delivery can fluctuate and that budget and bid strategy shape delivery behavior. "
            "A single partial day should therefore be read in context rather than treated as a deterministic failure rule."
        ),
    },
    {
        "source_id": "snap-measurement-official",
        "title": "Snapchat Ads measurement",
        "url": "https://forbusiness.snapchat.com/advertising/measurement",
        "source_tier": 1,
        "publisher": "Snap Inc.",
        "published_at": None,
        "last_reviewed_at": LAST_REVIEWED,
        "reliability": "official",
        "topics": ["snapchat", "measurement", "delivery", "learning", "video"],
        "summary": (
            "Snapchat measurement guidance covers spend, impressions, clicks, click rate, video views and conversion outcomes. "
            "Platform exploration/learning context can make early performance unstable, so launch age and recent edits are relevant counterfactual evidence."
        ),
    },
    {
        "source_id": "snap-metrics-official",
        "title": "Snapchat Ads metrics",
        "url": "https://businesshelp.snapchat.com/s/article/metrics",
        "source_tier": 1,
        "publisher": "Snap Inc.",
        "published_at": None,
        "last_reviewed_at": LAST_REVIEWED,
        "reliability": "official",
        "topics": ["snapchat", "video", "funnel", "creative_strategy", "measurement"],
        "summary": (
            "Snapchat exposes video-view milestones, average screen/watch signals, swipes and conversion funnel events such as add-to-cart, checkout and purchase. "
            "These metrics support diagnosis of where attention or shopping intent is lost."
        ),
    },
    {
        "source_id": "snap-pixel-official",
        "title": "Snap Pixel",
        "url": "https://forbusiness.snapchat.com/advertising/snap-pixel",
        "source_tier": 1,
        "publisher": "Snap Inc.",
        "published_at": None,
        "last_reviewed_at": LAST_REVIEWED,
        "reliability": "official",
        "topics": ["snapchat", "tracking", "attribution", "funnel"],
        "summary": (
            "Snap Pixel is designed to observe website behaviors such as page visits, cart events and purchases. "
            "When provider conversions conflict with store or funnel evidence, tracking and attribution quality should be investigated rather than silently reconciled."
        ),
    },
    {
        "source_id": "snap-unified-attribution-official-2026",
        "title": "Snap Unified Attribution",
        "url": "https://forbusiness.snapchat.com/blog/introducing-unified-attribution",
        "source_tier": 1,
        "publisher": "Snap Inc.",
        "published_at": "2026-05-20",
        "last_reviewed_at": LAST_REVIEWED,
        "reliability": "official",
        "topics": ["snapchat", "attribution", "measurement"],
        "summary": (
            "Snap introduced updated attribution tooling in 2026, reinforcing that platform attribution methodology evolves over time. "
            "Current provider reporting configuration and store-side commercial truth should remain separately identified."
        ),
    },
    {
        "source_id": "ctc-hierarchy-of-metrics",
        "title": "Common Thread Collective - hierarchy of metrics",
        "url": "https://commonthreadco.com/blogs/coachs-corner/hierarchy-of-metrics",
        "source_tier": 2,
        "publisher": "Common Thread Collective",
        "published_at": None,
        "last_reviewed_at": LAST_REVIEWED,
        "reliability": "high",
        "topics": ["ecommerce", "unit_economics", "measurement", "profitability"],
        "summary": (
            "The framework separates business, customer and channel metrics so platform efficiency is not mistaken for whole-business profitability. "
            "For Mezan this supports keeping Salla/product-cost contribution context distinct from provider attribution."
        ),
    },
    {
        "source_id": "ctc-modern-media-playbook",
        "title": "Common Thread Collective - modern media buying",
        "url": "https://commonthreadco.com/blogs/coachs-corner/ecommerce-media-buying",
        "source_tier": 2,
        "publisher": "Common Thread Collective",
        "published_at": None,
        "last_reviewed_at": LAST_REVIEWED,
        "reliability": "high",
        "topics": ["media_buying", "profitability", "testing", "scaling"],
        "summary": (
            "A useful media-buying lens connects tests to business impact and contribution economics rather than optimizing isolated platform metrics. "
            "Scaling should consider marginal commercial value and operational capacity, not ROAS alone."
        ),
    },
    {
        "source_id": "ctc-creative-strategy",
        "title": "Common Thread Collective - creative strategy",
        "url": "https://commonthreadco.com/blogs/coachs-corner/stop-creative-testing-start-marketing",
        "source_tier": 2,
        "publisher": "Common Thread Collective",
        "published_at": None,
        "last_reviewed_at": LAST_REVIEWED,
        "reliability": "high",
        "topics": ["creative_strategy", "testing", "positioning", "offer"],
        "summary": (
            "Creative testing is more useful when framed as testing customer, message and offer hypotheses instead of rotating arbitrary assets. "
            "A creative recommendation should therefore specify the problem, angle, hook, treatment and success evidence."
        ),
    },
    {
        "source_id": "cxl-checkout-friction",
        "title": "CXL - checkout and cart friction research",
        "url": "https://cxl.com/blog/shopping-cart-abandonment/",
        "source_tier": 2,
        "publisher": "CXL",
        "published_at": None,
        "last_reviewed_at": LAST_REVIEWED,
        "reliability": "high",
        "topics": ["cro", "checkout", "abandoned_carts", "payment", "shipping"],
        "summary": (
            "Checkout abandonment analysis should distinguish acquisition quality from downstream friction such as unexpected cost, payment limitations, usability and trust. "
            "Strong shopping intent followed by purchase loss is evidence to investigate checkout before blaming traffic."
        ),
    },
    {
        "source_id": "measured-incrementality",
        "title": "Measured - incrementality and attribution",
        "url": "https://www.measured.com/blog/incrementality-testing/",
        "source_tier": 2,
        "publisher": "Measured",
        "published_at": None,
        "last_reviewed_at": LAST_REVIEWED,
        "reliability": "high",
        "topics": ["attribution", "incrementality", "measurement", "cross_platform"],
        "summary": (
            "Attribution reports are not the same as causal incrementality. "
            "Cross-platform and store-side evidence should be used to challenge a channel-specific interpretation when several systems move together."
        ),
    },
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[\w\u0600-\u06FF]+", str(value or "").casefold())
        if len(token) > 2
    }


def _chunk_text(text: str) -> list[str]:
    clean = " ".join(str(text or "").split()).strip()[:MAX_SOURCE_CHARS]
    if not clean:
        return []
    if len(clean) <= MAX_CHUNK_CHARS:
        return [clean]
    chunks: list[str] = []
    cursor = 0
    while cursor < len(clean):
        end = min(len(clean), cursor + MAX_CHUNK_CHARS)
        piece = clean[cursor:end]
        if end < len(clean):
            split = max(piece.rfind(". "), piece.rfind("؟ "), piece.rfind("! "))
            if split > MAX_CHUNK_CHARS // 2:
                end = cursor + split + 1
                piece = clean[cursor:end]
        chunks.append(piece.strip())
        if end >= len(clean):
            break
        cursor = max(cursor + 1, end - CHUNK_OVERLAP_CHARS)
    return [item for item in chunks if item]


def _cosine(left: list[float] | None, right: list[float] | None) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    dot = sum(a * b for a, b in zip(left, right))
    lnorm = math.sqrt(sum(a * a for a in left))
    rnorm = math.sqrt(sum(b * b for b in right))
    if not lnorm or not rnorm:
        return None
    return dot / (lnorm * rnorm)


def _tier_weight(tier: int) -> float:
    return {1: 1.0, 2: 0.86, 3: 0.68}.get(int(tier or 3), 0.6)


def _recency_weight(published_at: Any, topics: Iterable[str], now: datetime) -> float:
    fast_moving = bool(set(topics) & {"meta", "snapchat", "delivery", "bidding", "attribution", "campaign_structure"})
    if not published_at:
        return 0.88 if fast_moving else 0.95
    try:
        published = datetime.fromisoformat(str(published_at)[:10]).replace(tzinfo=timezone.utc)
    except ValueError:
        return 0.88 if fast_moving else 0.95
    age_days = max(0, (now - published).days)
    horizon = 540 if fast_moving else 1460
    return max(0.55, 1.0 - min(age_days, horizon) / horizon * 0.35)


async def ensure_marketing_knowledge_indexes(db: Any) -> None:
    await db[SOURCE_COLLECTION].create_index("source_id", unique=True, name="marketing_kb_source_unique")
    await db[SOURCE_COLLECTION].create_index([("source_tier", 1), ("last_reviewed_at", -1)], name="marketing_kb_source_quality")
    await db[CHUNK_COLLECTION].create_index("chunk_id", unique=True, name="marketing_kb_chunk_unique")
    await db[CHUNK_COLLECTION].create_index([("source_id", 1), ("chunk_index", 1)], unique=True, name="marketing_kb_source_chunk_unique")
    await db[CHUNK_COLLECTION].create_index("topics", name="marketing_kb_topics")


async def _embed(client: Any, texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    response = await client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [list(item.embedding) for item in response.data]


async def seed_marketing_knowledge_base(db: Any, *, embed_missing: bool = False) -> dict[str, Any]:
    """Idempotently seed curated metadata/summaries and optionally embeddings."""
    await ensure_marketing_knowledge_indexes(db)
    now = _utcnow()
    sources = 0
    chunks = 0
    for source in CURATED_SOURCES:
        await db[SOURCE_COLLECTION].update_one(
            {"source_id": source["source_id"]},
            {"$set": {**source, "status": "active", "updated_at": now}, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        sources += 1
        for index, text in enumerate(_chunk_text(source["summary"])):
            await db[CHUNK_COLLECTION].update_one(
                {"source_id": source["source_id"], "chunk_index": index},
                {"$set": {
                    "chunk_id": f"{source['source_id']}:{index}",
                    "source_id": source["source_id"],
                    "chunk_index": index,
                    "text": text,
                    "source_tier": source["source_tier"],
                    "reliability": source["reliability"],
                    "topics": source["topics"],
                    "published_at": source["published_at"],
                    "last_reviewed_at": source["last_reviewed_at"],
                    "title": source["title"],
                    "url": source["url"],
                    "status": "active",
                    "updated_at": now,
                }, "$setOnInsert": {"created_at": now}},
                upsert=True,
            )
            chunks += 1

    embedded = 0
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if embed_missing and api_key and AsyncOpenAI is not None:
        missing = await db[CHUNK_COLLECTION].find(
            {"status": "active", "embedding": {"$exists": False}},
            {"_id": 0, "chunk_id": 1, "text": 1},
        ).limit(32).to_list(length=32)
        if missing:
            client = AsyncOpenAI(api_key=api_key, max_retries=0, timeout=60.0)
            try:
                vectors = await _embed(client, [row["text"] for row in missing])
                for row, vector in zip(missing, vectors):
                    await db[CHUNK_COLLECTION].update_one(
                        {"chunk_id": row["chunk_id"]},
                        {"$set": {
                            "embedding": vector,
                            "embedding_model": EMBEDDING_MODEL,
                            "embedded_at": now,
                        }},
                    )
                    embedded += 1
            finally:
                await client.close()
    return {"sources": sources, "chunks": chunks, "embedded": embedded}


async def ingest_marketing_knowledge_text(
    db: Any,
    *,
    title: str,
    url: str,
    text: str,
    source_tier: int,
    reliability: str,
    topics: list[str],
    published_at: str | None,
    last_reviewed_at: str,
    publisher: str | None = None,
    source_id: str | None = None,
    embed: bool = True,
) -> dict[str, Any]:
    """Ingest reviewed text/transcript into chunks; intended for admin jobs."""
    if int(source_tier) not in {1, 2, 3}:
        raise ValueError("source_tier must be 1, 2 or 3")
    if reliability not in {"official", "high", "contextual"}:
        raise ValueError("invalid reliability")
    chunks = _chunk_text(text)
    if not chunks:
        raise ValueError("knowledge text is empty")
    sid = source_id or f"kb-{uuid.uuid4().hex}"
    await ensure_marketing_knowledge_indexes(db)
    now = _utcnow()
    source = {
        "source_id": sid,
        "title": str(title)[:500],
        "url": str(url)[:2000],
        "source_tier": int(source_tier),
        "publisher": str(publisher or "")[:300] or None,
        "published_at": published_at,
        "last_reviewed_at": last_reviewed_at,
        "reliability": reliability,
        "topics": sorted(set(topics))[:30],
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    await db[SOURCE_COLLECTION].update_one({"source_id": sid}, {"$set": source}, upsert=True)

    vectors: list[list[float] | None] = [None] * len(chunks)
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if embed and api_key and AsyncOpenAI is not None:
        client = AsyncOpenAI(api_key=api_key, max_retries=0, timeout=90.0)
        try:
            embedded = await _embed(client, chunks)
            vectors = list(embedded)
        finally:
            await client.close()
    await db[CHUNK_COLLECTION].delete_many({"source_id": sid})
    for index, chunk in enumerate(chunks):
        document = {
            "chunk_id": f"{sid}:{index}",
            "source_id": sid,
            "chunk_index": index,
            "text": chunk,
            "source_tier": int(source_tier),
            "reliability": reliability,
            "topics": source["topics"],
            "published_at": published_at,
            "last_reviewed_at": last_reviewed_at,
            "title": source["title"],
            "url": source["url"],
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        if vectors[index] is not None:
            document.update({
                "embedding": vectors[index],
                "embedding_model": EMBEDDING_MODEL,
                "embedded_at": now,
            })
        await db[CHUNK_COLLECTION].insert_one(document)
    return {"source_id": sid, "chunks": len(chunks), "embedded": sum(v is not None for v in vectors)}


def infer_retrieval_topics(evidence_pack: dict[str, Any]) -> list[str]:
    """Infer broad retrieval domains from available evidence, not outcomes."""
    topics = {"performance_marketing", "ecommerce", "measurement", "profitability"}
    providers = set(evidence_pack.get("providers") or [])
    topics.update(providers)
    if evidence_pack.get("funnel_evidence_available"):
        topics.update({"funnel", "cro", "checkout", "tracking"})
    if evidence_pack.get("creative_evidence_available"):
        topics.update({"creative_strategy", "video"})
    if evidence_pack.get("product_evidence_available"):
        topics.update({"product_page", "inventory", "offer"})
    if evidence_pack.get("abandoned_cart_evidence_available"):
        topics.update({"abandoned_carts", "checkout", "payment", "shipping"})
    if len(providers) > 1:
        topics.update({"cross_platform", "attribution", "incrementality"})
    return sorted(topics)


async def retrieve_marketing_knowledge(
    db: Any,
    *,
    query: str,
    topics: list[str],
    limit: int = MAX_RETRIEVED_CHUNKS,
) -> list[dict[str, Any]]:
    """Retrieve bounded current knowledge with reliability/recency weighting."""
    await seed_marketing_knowledge_base(db, embed_missing=False)
    rows = await db[CHUNK_COLLECTION].find(
        {"status": "active", "$or": [{"topics": {"$in": topics}}, {"source_tier": 1}]},
        {"_id": 0},
    ).limit(250).to_list(length=250)
    if not rows:
        return []

    query_vector: list[float] | None = None
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if api_key and AsyncOpenAI is not None and any(row.get("embedding") for row in rows):
        client = AsyncOpenAI(api_key=api_key, max_retries=0, timeout=45.0)
        try:
            query_vector = (await _embed(client, [query]))[0]
        except Exception:
            query_vector = None
        finally:
            await client.close()

    qtokens = _tokens(query) | set(topics)
    now = _utcnow()
    ranked = []
    for row in rows:
        text_tokens = _tokens(row.get("text") or "") | set(row.get("topics") or [])
        lexical = len(qtokens & text_tokens) / max(1, len(qtokens))
        semantic = _cosine(query_vector, row.get("embedding"))
        relevance = max(lexical, semantic if semantic is not None else 0.0)
        score = (
            relevance
            * _tier_weight(int(row.get("source_tier") or 3))
            * _recency_weight(row.get("published_at"), row.get("topics") or [], now)
        )
        ranked.append((score, row))
    ranked.sort(key=lambda pair: pair[0], reverse=True)

    output = []
    seen_sources: set[str] = set()
    for score, row in ranked:
        if len(output) >= max(1, min(limit, 20)):
            break
        if score <= 0 and output:
            continue
        source_id = str(row.get("source_id") or "")
        # Keep diversity; allow two chunks per source only after broad coverage.
        same_source = sum(1 for item in output if item["source_id"] == source_id)
        if same_source >= 2:
            continue
        output.append({
            "source_id": source_id,
            "title": row.get("title"),
            "url": row.get("url"),
            "source_tier": row.get("source_tier"),
            "published_at": row.get("published_at"),
            "last_reviewed_at": row.get("last_reviewed_at"),
            "reliability": row.get("reliability"),
            "topics": row.get("topics") or [],
            "insight_summary": row.get("text"),
            "retrieval_score": round(score, 6),
            "retrieval_method": "embedding_plus_lexical" if semantic is not None else "lexical",
        })
        seen_sources.add(source_id)
    return output


__all__ = [
    "CHUNK_COLLECTION",
    "SOURCE_COLLECTION",
    "ensure_marketing_knowledge_indexes",
    "infer_retrieval_topics",
    "ingest_marketing_knowledge_text",
    "retrieve_marketing_knowledge",
    "seed_marketing_knowledge_base",
]
