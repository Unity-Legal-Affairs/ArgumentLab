from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.models import Document, EmailEvent


ASSERTION_MARKERS = [
    "first",
    "only",
    "never",
    "always",
    "undisputed",
    "no evidence",
    "notice",
    "objected",
    "objection",
    "terminated",
    "termination",
    "breach",
    "damages",
    "relied",
    "waived",
    "modified",
]

CONTRADICTION_MARKERS = {
    "first object": ["object", "objection"],
    "first objected": ["object", "objection"],
    "never gave notice": ["notice"],
    "never written notice": ["notice"],
    "no notice": ["notice"],
    "no notice occurred": ["notice"],
    "first complained": ["complaint", "notice"],
    "after termination": ["object", "notice", "complaint"],
}


@dataclass
class SourceChunk:
    source_type: str
    source_id: str
    text: str
    locator: dict[str, Any]
    timestamp: datetime | None = None


def build_source_chunks(documents: list[Document], emails: list[EmailEvent]) -> list[SourceChunk]:
    chunks: list[SourceChunk] = []
    for doc in documents:
        text = doc.extracted_text or ""
        for index, chunk in enumerate(chunk_text(text), start=1):
            chunks.append(
                SourceChunk(
                    source_type="document",
                    source_id=doc.id,
                    text=chunk,
                    locator={"filename": doc.filename, "chunk": index, "document_type": doc.document_type},
                )
            )
    for email in emails:
        text = email.normalized_body or email.raw_body or ""
        chunks.append(
            SourceChunk(
                source_type="email",
                source_id=email.id,
                text=text,
                locator={
                    "thread_id": email.thread_id,
                    "sender": email.sender,
                    "subject": email.subject,
                    "timestamp": email.normalized_timestamp.isoformat() if email.normalized_timestamp else email.original_timestamp,
                    "tags": email.legal_event_tags,
                },
                timestamp=email.normalized_timestamp,
            )
        )
    return chunks


def extract_material_claims(documents: list[Document]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    source_docs = [doc for doc in documents if doc.document_type in {"motion", "pleading", "opposition", "reply", "correspondence"}]
    for doc in source_docs:
        for sentence in split_sentences(doc.extracted_text or ""):
            lower = sentence.lower()
            if len(sentence.split()) < 5:
                continue
            if any(marker in lower for marker in ASSERTION_MARKERS):
                claims.append(
                    {
                        "claim_id": f"claim_{len(claims) + 1:03d}",
                        "text": sentence.strip(),
                        "source_document_id": doc.id,
                        "source_filename": doc.filename,
                    }
                )
    return claims[:40]


def ground_claims(claims: list[dict[str, Any]], documents: list[Document], emails: list[EmailEvent]) -> list[dict[str, Any]]:
    chunks = build_source_chunks(documents, emails)
    grounded: list[dict[str, Any]] = []
    for claim in claims:
        text = claim["text"]
        support = retrieve_support(text, chunks, exclude_source_id=claim.get("source_document_id"))
        contradiction = detect_timeline_contradiction(text, emails)
        if contradiction:
            status = "contradicted"
            explanation = contradiction["explanation"]
            sources = [contradiction["source"]]
        elif support:
            status = "supported" if support[0]["score"] >= 2 else "ambiguous"
            explanation = "Candidate support was retrieved from uploaded sources." if status == "supported" else "Only weak or partial candidate support was found."
            sources = [item["source"] for item in support[:3]]
        else:
            status = "unsupported"
            explanation = "No candidate record or email support was found with exact local retrieval."
            sources = []
        grounded.append(
            {
                **claim,
                "status": status,
                "explanation": explanation,
                "supporting_sources": sources,
            }
        )
    return grounded


def retrieve_support(claim: str, chunks: list[SourceChunk], exclude_source_id: str | None = None) -> list[dict[str, Any]]:
    terms = significant_terms(claim)
    results: list[dict[str, Any]] = []
    for chunk in chunks:
        if exclude_source_id and chunk.source_id == exclude_source_id:
            continue
        lower = chunk.text.lower()
        matched = [term for term in terms if term in lower]
        if not matched:
            continue
        results.append(
            {
                "score": len(matched),
                "source": {
                    "source_type": chunk.source_type,
                    "source_id": chunk.source_id,
                    "page": chunk.locator.get("page"),
                    "timestamp": chunk.locator.get("timestamp"),
                    "quote": chunk.text[:500],
                    "locator": chunk.locator,
                    "matched_terms": matched[:8],
                },
            }
        )
    return sorted(results, key=lambda item: item["score"], reverse=True)


def detect_timeline_contradiction(claim: str, emails: list[EmailEvent]) -> dict[str, Any] | None:
    lower = claim.lower()
    for phrase, markers in CONTRADICTION_MARKERS.items():
        if phrase not in lower:
            continue
        matching = [email for email in emails if any(marker in (email.normalized_body or email.raw_body or "").lower() for marker in markers)]
        if matching:
            matching.sort(key=lambda email: email.normalized_timestamp or datetime.max)
            email = matching[0]
            return {
                "explanation": f"The draft claim uses timing language ('{phrase}') but an email timeline event contains earlier or contrary {', '.join(markers)} language.",
                "source": {
                    "source_type": "email",
                    "source_id": email.id,
                    "page": None,
                    "timestamp": email.normalized_timestamp.isoformat() if email.normalized_timestamp else email.original_timestamp,
                    "quote": (email.normalized_body or email.raw_body or "")[:500],
                    "locator": {
                        "thread_id": email.thread_id,
                        "sender": email.sender,
                        "subject": email.subject,
                    },
                },
            }
    return None


def chunk_text(text: str, size: int = 900, overlap: int = 120) -> list[str]:
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return []
    chunks = []
    start = 0
    while start < len(clean):
        chunks.append(clean[start : start + size])
        start += max(size - overlap, 1)
    return chunks[:250]


def split_sentences(text: str) -> list[str]:
    clean = re.sub(r"\s+", " ", text).strip()
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", clean) if part.strip()]


def significant_terms(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9'-]{3,}", text.lower())
    stop = {
        "that",
        "this",
        "with",
        "from",
        "were",
        "been",
        "have",
        "will",
        "shall",
        "because",
        "there",
        "their",
        "under",
        "plaintiff",
        "defendant",
        "draft",
        "motion",
    }
    terms = []
    for word in words:
        if word not in stop and word not in terms:
            terms.append(word)
    return terms[:20]
