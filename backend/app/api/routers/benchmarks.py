import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Document, EmailEvent, Matter, SimulationFinding, SimulationTurn
from app.schemas import BenchmarkPacketRead, BenchmarkRunRequest, BenchmarkRunResult, SimulationConfig
from app.services.email_parser import parse_copied_thread
from app.services.ingestion import classify_document, create_source_refs
from app.services.simulation import create_and_run_simulation

router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])


@router.get("/packets", response_model=list[BenchmarkPacketRead])
def list_packets() -> list[BenchmarkPacketRead]:
    packets = []
    for path in packets_dir().glob("*.json"):
        data = json.loads(path.read_text())
        packets.append(BenchmarkPacketRead(id=data["id"], name=data["name"], planted_issues=data["planted_issues"], description=data["description"]))
    return sorted(packets, key=lambda item: item.id)


@router.post("/run", response_model=BenchmarkRunResult)
async def run_packet(payload: BenchmarkRunRequest, db: Session = Depends(get_db)) -> BenchmarkRunResult:
    path = packets_dir() / f"{payload.packet_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Benchmark packet not found")
    data = json.loads(path.read_text())
    matter = Matter(name=f"Benchmark: {data['name']}", description=data["description"])
    db.add(matter)
    db.flush()
    for doc_payload in data.get("documents", []):
        text = doc_payload["text"]
        inferred, reason = classify_document(doc_payload["filename"], text)
        doc = Document(
            matter_id=matter.id,
            filename=doc_payload["filename"],
            document_type=doc_payload.get("document_type") or inferred,
            status="benchmark_loaded",
            mime_type="text/plain",
            storage_path=f"benchmark://{payload.packet_id}/{doc_payload['filename']}",
            size_bytes=len(text.encode()),
            extracted_text=text,
            classification_reason=reason,
            source_refs=create_source_refs(text),
        )
        db.add(doc)
    for email_text in data.get("emails", []):
        for parsed in parse_copied_thread(email_text.get("text", ""), email_text.get("subject")):
            db.add(
                EmailEvent(
                    matter_id=matter.id,
                    thread_id=parsed.thread_id,
                    message_id=parsed.message_id,
                    in_reply_to=parsed.in_reply_to,
                    sender=parsed.sender,
                    recipients=parsed.recipients,
                    cc=parsed.cc,
                    bcc=parsed.bcc,
                    subject=parsed.subject,
                    original_timestamp=parsed.original_timestamp,
                    normalized_timestamp=parsed.normalized_timestamp,
                    detected_timezone=parsed.detected_timezone,
                    raw_body=parsed.raw_body,
                    normalized_body=parsed.normalized_body,
                    quoted_text=parsed.quoted_text,
                    attachments=parsed.attachments,
                    legal_event_tags=parsed.legal_event_tags,
                    duplicate_quote_warning=parsed.duplicate_quote_warning,
                )
            )
    db.commit()
    config = SimulationConfig.model_validate(data.get("simulation_config", {}))
    run = await create_and_run_simulation(db, matter.id, config)
    findings = db.query(SimulationFinding).filter(SimulationFinding.simulation_id == run.id).all()
    turns = db.query(SimulationTurn).filter(SimulationTurn.simulation_id == run.id).all()
    answer_key = data.get("expected_findings", [])
    answer_key_score = score_answer_key(answer_key, findings)
    metrics = {
        "self_play_rounds_completed": run.summary.get("rounds_completed", 0),
        "schema_validation_success": all(turn.schema_validated for turn in turns),
        "turn_provider_statuses": status_counts([turn.provider_status for turn in turns]),
        "answer_key": answer_key_score,
        "unsupported_facts_correctly_flagged": answer_key_score["true_positives_by_category"].get("unsupported_fact", 0),
        "contradicted_email_chronology_correctly_flagged": answer_key_score["true_positives_by_category"].get("contradicted_fact", 0)
        + answer_key_score["true_positives_by_category"].get("email_chronology_issue", 0),
        "citation_hallucinations": answer_key_score["hallucinated_source_count"],
        "judge_persona_disagreement_quality": "requires human review" if run.summary.get("open_attack_count", 0) else "not assessed",
        "useful_vulnerabilities_found": run.summary.get("finding_count", 0),
        "false_positives": answer_key_score["false_positive_count"],
        "cost_per_run": 0,
        "latency_per_run": None,
    }
    return BenchmarkRunResult(packet_id=payload.packet_id, matter_id=matter.id, simulation_id=run.id, metrics=metrics)


def packets_dir() -> Path:
    return Path(__file__).resolve().parents[4] / "benchmarks" / "v0_1" / "matters"


def score_answer_key(expected_findings: list[dict], findings: list[SimulationFinding]) -> dict:
    matched_finding_ids: set[str] = set()
    missed: list[dict] = []
    true_positives_by_category: dict[str, int] = {}
    wrong_severity: list[dict] = []
    wrong_source: list[dict] = []
    hallucinated_source_count = 0

    for expected in expected_findings:
        if not expected.get("must_detect", True):
            continue
        match = find_expected_match(expected, findings, matched_finding_ids)
        if not match:
            missed.append(expected)
            continue
        matched_finding_ids.add(match.id)
        category = match.category
        true_positives_by_category[category] = true_positives_by_category.get(category, 0) + 1
        if expected.get("severity") and expected["severity"] != match.severity:
            wrong_severity.append({"expected": expected, "actual": match.severity, "finding_id": match.id})
        source_contains = (expected.get("source_email_contains") or expected.get("source_document_contains") or "").lower()
        if source_contains and not finding_sources_contain(match, source_contains):
            wrong_source.append({"expected": expected, "finding_id": match.id})

    for finding in findings:
        for source in finding.supporting_sources:
            if source.get("source_id") and not source.get("quote"):
                hallucinated_source_count += 1

    expected_categories = {item.get("category") for item in expected_findings if item.get("must_detect", True)}
    false_positive_count = sum(1 for finding in findings if finding.id not in matched_finding_ids and finding.category not in expected_categories)
    return {
        "expected_count": len([item for item in expected_findings if item.get("must_detect", True)]),
        "true_positive_count": len(matched_finding_ids),
        "true_positives_by_category": true_positives_by_category,
        "missed": missed,
        "wrong_severity": wrong_severity,
        "wrong_source": wrong_source,
        "false_positive_count": false_positive_count,
        "hallucinated_source_count": hallucinated_source_count,
    }


def find_expected_match(expected: dict, findings: list[SimulationFinding], already_matched: set[str]) -> SimulationFinding | None:
    category = expected.get("category")
    title_contains = (expected.get("title_contains") or "").lower()
    source_contains = (expected.get("source_email_contains") or expected.get("source_document_contains") or "").lower()
    for finding in findings:
        if finding.id in already_matched:
            continue
        if category and finding.category != category:
            continue
        if title_contains and title_contains not in finding.title.lower() and title_contains not in finding.description.lower():
            continue
        if source_contains and not finding_sources_contain(finding, source_contains):
            continue
        return finding
    return None


def finding_sources_contain(finding: SimulationFinding, needle: str) -> bool:
    return any(needle in (source.get("quote") or "").lower() for source in finding.supporting_sources)


def status_counts(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts
