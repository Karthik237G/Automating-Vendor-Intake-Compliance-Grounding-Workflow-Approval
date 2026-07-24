"""
main.py
=======
FastAPI server for the AI Vendor Onboarding & Approval Assistant.

Pipeline for POST /api/process-vendor
-------------------------------------
    PDF bytes
      -> text extraction (pypdf, pdfplumber fallback)
      -> document classification (filename + content signals)
      -> structured field extraction        [azure/genailab-maas-gpt-4o-mini]
      -> deterministic rules engine         [no LLM: dates, completeness, thresholds]
      -> policy retrieval from ChromaDB     [azure/...text-embedding-3-large]
      -> grounded validation summary        [genailab-maas-gpt-4o]
      -> audit record persisted to disk

Run:  cd backend && uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import sys
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from dotenv import find_dotenv, load_dotenv
from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Works whether uvicorn is started from the repo root or from ./backend
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rag_service as rag  # noqa: E402

load_dotenv(find_dotenv(usecwd=True))

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(name)s | %(message)s")
log = logging.getLogger("vendor-api")

# --------------------------------------------------------------------------- #
# Paths & policy constants
# --------------------------------------------------------------------------- #
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
TEST_DOCS_DIR = os.path.join(ROOT_DIR, "test_docs")
DATA_DIR = os.path.join(ROOT_DIR, "data")
AUDIT_DIR = os.path.join(DATA_DIR, "audit")
os.makedirs(AUDIT_DIR, exist_ok=True)

# Business thresholds. These mirror the reference policy PDFs and stay in code so
# the checks are deterministic and reproducible - the LLM never decides them.
MIN_INSURANCE_COVERAGE = float(os.getenv("MIN_INSURANCE_COVERAGE", "1000000"))
EXPIRY_WARNING_DAYS = int(os.getenv("EXPIRY_WARNING_DAYS", "30"))

REQUIRED_DOCUMENTS: List[Dict[str, str]] = [
    {"type": "registration_certificate", "label": "Business Registration Certificate"},
    {"type": "tax_certificate", "label": "Tax Registration Certificate"},
    {"type": "bank_proof", "label": "Bank Account Proof"},
    {"type": "insurance_certificate", "label": "Certificate of Insurance"},
    {"type": "compliance_declaration", "label": "Compliance Declaration"},
]
DOC_LABELS = {d["type"]: d["label"] for d in REQUIRED_DOCUMENTS}
DOC_LABELS["unknown"] = "Unclassified document"

# In-memory case store (an MVP stand-in for a real case database).
CASES: Dict[str, Dict[str, Any]] = {}

app = FastAPI(
    title="AI Vendor Onboarding & Approval Assistant",
    version="1.0.0",
    description="Deterministic document checks + grounded RAG policy reasoning for vendor onboarding.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# PDF text extraction
# --------------------------------------------------------------------------- #
def extract_pdf_text(data: bytes, filename: str) -> str:
    """Extract text with pypdf; fall back to pdfplumber for awkward layouts."""
    text = ""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:  # noqa: BLE001
        log.warning("pypdf failed on %s: %s", filename, exc)

    if len(text.strip()) < 40:
        try:
            import pdfplumber

            with pdfplumber.open(io.BytesIO(data)) as pdf:
                text = "\n".join((page.extract_text() or "") for page in pdf.pages)
        except Exception as exc:  # noqa: BLE001
            log.warning("pdfplumber failed on %s: %s", filename, exc)

    return re.sub(r"\n{3,}", "\n\n", text or "").strip()


def read_upload(upload: UploadFile) -> bytes:
    data = upload.file.read()
    if not data:
        raise HTTPException(status_code=400, detail=f"{upload.filename} is empty.")
    return data


# --------------------------------------------------------------------------- #
# Document classification
# --------------------------------------------------------------------------- #
CLASSIFIER_SIGNALS: List[tuple[str, List[str]]] = [
    ("insurance_certificate", ["certificate of insurance", "general liability", "named insured",
                               "coverage limit", "policy number", "insurance"]),
    ("tax_certificate", ["tax registration", "tax identification", "gstin", "revenue authority",
                         "vat", "tax certificate", "tax"]),
    ("bank_proof", ["bank account", "account holder", "void cheque", "ifsc", "swift",
                    "bank letter", "bank"]),
    ("compliance_declaration", ["compliance declaration", "anti-bribery", "code of conduct",
                                "sanctions", "declaration"]),
    ("registration_certificate", ["certificate of business registration", "registrar of companies",
                                  "incorporation", "registration number", "registration"]),
]


def classify_document(filename: str, text: str, declared_type: Optional[str] = None) -> str:
    """
    Decide what a document is, using the model's own guess, then the filename,
    then content keywords. Filename wins over body text because vendors name
    files predictably and body text mentions many document types at once.
    """
    if declared_type in DOC_LABELS and declared_type != "unknown":
        declared_ok = declared_type
    else:
        declared_ok = None

    haystacks = [filename.lower().replace("_", " ").replace("-", " "), (text or "").lower()[:1500]]
    for haystack in haystacks:
        for doc_type, keywords in CLASSIFIER_SIGNALS:
            if any(keyword in haystack for keyword in keywords):
                return doc_type
    return declared_ok or "unknown"


# --------------------------------------------------------------------------- #
# Evidence snippets (verbatim vendor text for the citation viewer)
# --------------------------------------------------------------------------- #
EVIDENCE_LABELS = re.compile(
    r"(Legal Entity Name|Named Insured|Account Holder Name|Tax Identification Number|"
    r"Valid Through|Expiry Date|Policy Expiry Date|Coverage Limit[^:\n]*|Aggregate Limit|"
    r"Registration Number|Account Number|Policy Number|Certificate Status|Authorised Signatory|"
    r"Date of Signature)\s*[:\-].*",
    re.IGNORECASE,
)


def build_evidence(filename: str, text: str) -> Dict[str, str]:
    """Pull the labelled lines out of a document - these are quoted in the UI."""
    lines = [m.group(0).strip() for m in EVIDENCE_LABELS.finditer(text or "")]
    snippet = "\n".join(dict.fromkeys(lines))[:600]
    if not snippet:
        snippet = (text or "").strip()[:400] or "No machine-readable text could be extracted."
    return {"document": filename, "text": snippet}


# --------------------------------------------------------------------------- #
# Deterministic rules engine
# --------------------------------------------------------------------------- #
def _iso_to_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _money(value: Optional[float]) -> str:
    return f"USD {value:,.0f}" if isinstance(value, (int, float)) else "not stated"


def _normalise_name(name: Optional[str]) -> str:
    if not name:
        return ""
    cleaned = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    stop = {"private", "pvt", "limited", "ltd", "llp", "inc", "corp", "corporation", "co", "the"}
    return " ".join(w for w in cleaned.split() if w not in stop)


def run_rules(documents: List[Dict[str, Any]], merged: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Every check here is pure Python - reproducible, auditable and independent of
    the LLM. ``status`` is one of pass / fail / gap:
        pass -> compliant
        fail -> blocking (missing, expired or illegible)  -> resubmission
        gap  -> policy deviation                          -> compliance review
    """
    today = date.today()
    checks: List[Dict[str, Any]] = []
    present_types = {d["doc_type"] for d in documents}

    # ---- Rule 1: mandatory document completeness --------------------------
    for required in REQUIRED_DOCUMENTS:
        found = required["type"] in present_types
        checks.append(
            {
                "id": f"COMPLETENESS_{required['type'].upper()}",
                "title": required["label"],
                "doc_type": required["type"],
                "status": "pass" if found else "fail",
                "detail": (
                    f"{required['label']} received." if found
                    else f"{required['label']} was not included in the submission."
                ),
                "remedy": None if found else f"Upload a current {required['label']}.",
                "policy_query": f"{required['label']} requirement for supplier onboarding",
            }
        )

    # ---- Rule 2: legibility ------------------------------------------------
    for doc in documents:
        if doc["chars"] < 40:
            checks.append(
                {
                    "id": f"LEGIBILITY_{doc['doc_type'].upper()}",
                    "title": f"Legibility - {doc['filename']}",
                    "doc_type": doc["doc_type"],
                    "status": "fail",
                    "detail": f"No machine-readable text could be extracted from {doc['filename']}.",
                    "remedy": "Resubmit a text-based PDF or a higher-quality scan.",
                    "policy_query": "illegible scanned document rejection rule",
                }
            )

    # ---- Rule 3: tax certificate validity ---------------------------------
    tax_expiry = _iso_to_date(merged.get("tax_expiry_date"))
    if "tax_certificate" in present_types:
        if tax_expiry is None:
            checks.append({
                "id": "TAX_EXPIRY",
                "title": "Tax certificate validity",
                "doc_type": "tax_certificate",
                "status": "gap",
                "detail": "No validity date could be read from the tax registration certificate.",
                "remedy": "Resubmit a tax certificate that clearly states its validity period.",
                "policy_query": "tax registration certificate must be valid and unexpired",
            })
        elif tax_expiry < today:
            days = (today - tax_expiry).days
            checks.append({
                "id": "TAX_EXPIRY",
                "title": "Tax certificate validity",
                "doc_type": "tax_certificate",
                "status": "fail",
                "detail": f"The tax registration certificate expired on {tax_expiry.isoformat()}, "
                          f"{days} days ago.",
                "remedy": "Upload a renewed tax registration certificate valid beyond today's date.",
                "policy_query": "tax registration certificate must be valid and unexpired",
            })
        else:
            checks.append({
                "id": "TAX_EXPIRY",
                "title": "Tax certificate validity",
                "doc_type": "tax_certificate",
                "status": "pass",
                "detail": f"Tax certificate is valid through {tax_expiry.isoformat()}.",
                "remedy": None,
                "policy_query": "tax registration certificate must be valid and unexpired",
            })

    # ---- Rule 4: tax identification number present ------------------------
    if "tax_certificate" in present_types:
        has_tin = bool(merged.get("tax_id"))
        checks.append({
            "id": "TAX_ID_PRESENT",
            "title": "Tax identification number",
            "doc_type": "tax_certificate",
            "status": "pass" if has_tin else "gap",
            "detail": f"Tax ID {merged['tax_id']} read from the certificate." if has_tin
                      else "No tax identification number could be read from the certificate.",
            "remedy": None if has_tin else "Resubmit a certificate showing a legible tax ID.",
            "policy_query": "tax identification number must be legible on the certificate",
        })

    # ---- Rule 5: insurance coverage threshold -----------------------------
    coverage = merged.get("insurance_coverage_amount")
    if "insurance_certificate" in present_types:
        if not isinstance(coverage, (int, float)):
            checks.append({
                "id": "INSURANCE_COVERAGE",
                "title": "General liability coverage",
                "doc_type": "insurance_certificate",
                "status": "gap",
                "detail": "No coverage limit could be read from the insurance certificate.",
                "remedy": f"Resubmit a certificate stating a per-occurrence limit of at least "
                          f"{_money(MIN_INSURANCE_COVERAGE)}.",
                "policy_query": "minimum general liability insurance coverage per occurrence",
            })
        elif coverage < MIN_INSURANCE_COVERAGE:
            shortfall = MIN_INSURANCE_COVERAGE - coverage
            checks.append({
                "id": "INSURANCE_COVERAGE",
                "title": "General liability coverage",
                "doc_type": "insurance_certificate",
                "status": "gap",
                "detail": f"Coverage of {_money(coverage)} is {_money(shortfall)} below the "
                          f"{_money(MIN_INSURANCE_COVERAGE)} minimum required by policy.",
                "remedy": f"Increase general liability cover to {_money(MIN_INSURANCE_COVERAGE)} "
                          f"per occurrence, or record a documented compliance exception.",
                "policy_query": "minimum general liability insurance coverage per occurrence",
            })
        else:
            checks.append({
                "id": "INSURANCE_COVERAGE",
                "title": "General liability coverage",
                "doc_type": "insurance_certificate",
                "status": "pass",
                "detail": f"Coverage of {_money(coverage)} meets the {_money(MIN_INSURANCE_COVERAGE)} minimum.",
                "remedy": None,
                "policy_query": "minimum general liability insurance coverage per occurrence",
            })

    # ---- Rule 6: insurance policy currency --------------------------------
    ins_expiry = _iso_to_date(merged.get("insurance_expiry_date"))
    if "insurance_certificate" in present_types and ins_expiry:
        if ins_expiry < today:
            status, detail = "fail", f"The insurance policy expired on {ins_expiry.isoformat()}."
            remedy = "Upload a current certificate of insurance."
        elif ins_expiry < today + timedelta(days=EXPIRY_WARNING_DAYS):
            status = "gap"
            detail = (f"The insurance policy expires on {ins_expiry.isoformat()}, inside the "
                      f"{EXPIRY_WARNING_DAYS}-day warning window.")
            remedy = "Ask the vendor for a renewal certificate before contract start."
        else:
            status = "pass"
            detail = f"Insurance policy is in force until {ins_expiry.isoformat()}."
            remedy = None
        checks.append({
            "id": "INSURANCE_EXPIRY",
            "title": "Insurance policy currency",
            "doc_type": "insurance_certificate",
            "status": status,
            "detail": detail,
            "remedy": remedy,
            "policy_query": "insurance policy expiry date must be at least 30 days after review",
        })

    # ---- Rule 7: entity name consistency ----------------------------------
    names = {d["fields"].get("company_name") for d in documents if d["fields"].get("company_name")}
    normalised = {_normalise_name(n) for n in names}
    if len(normalised) > 1:
        checks.append({
            "id": "NAME_CONSISTENCY",
            "title": "Legal entity name consistency",
            "doc_type": "registration_certificate",
            "status": "gap",
            "detail": "The legal entity name differs across documents: " + "; ".join(sorted(names)) + ".",
            "remedy": "Confirm the correct legal entity name and resubmit the mismatched document.",
            "policy_query": "legal entity name must be identical across all submitted documents",
        })
    elif names:
        checks.append({
            "id": "NAME_CONSISTENCY",
            "title": "Legal entity name consistency",
            "doc_type": "registration_certificate",
            "status": "pass",
            "detail": f"All documents name the same legal entity: {list(names)[0]}.",
            "remedy": None,
            "policy_query": "legal entity name must be identical across all submitted documents",
        })

    return checks


def overall_status(checks: List[Dict[str, Any]]) -> tuple[str, str]:
    if any(c["status"] == "fail" for c in checks):
        return "RESUBMISSION_REQUIRED", "Resubmission required"
    if any(c["status"] == "gap" for c in checks):
        return "RISK_EXCEPTION", "Risk / exception found"
    return "READY_FOR_APPROVAL", "Ready for approval"


def build_checklist(documents: List[Dict[str, Any]], checks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per-document tracker rows: green verified / red missing-expired / amber mismatch."""
    rows: List[Dict[str, Any]] = []
    for required in REQUIRED_DOCUMENTS:
        doc = next((d for d in documents if d["doc_type"] == required["type"]), None)
        related = [c for c in checks if c["doc_type"] == required["type"]]
        failing = [c for c in related if c["status"] == "fail"]
        gapping = [c for c in related if c["status"] == "gap"]

        if doc is None:
            badge, state, detail = "red", "Missing", f"{required['label']} was not submitted."
        elif failing:
            badge, state, detail = "red", "Expired / invalid", failing[0]["detail"]
        elif gapping:
            badge, state, detail = "amber", "Policy mismatch", gapping[0]["detail"]
        else:
            badge, state, detail = "green", "Verified", f"{required['label']} passed all checks."

        rows.append({
            "doc_type": required["type"],
            "label": required["label"],
            "filename": doc["filename"] if doc else None,
            "badge": badge,
            "state": state,
            "detail": detail,
        })

    # anything uploaded that is not on the mandatory list
    for doc in documents:
        if doc["doc_type"] not in DOC_LABELS or doc["doc_type"] == "unknown":
            rows.append({
                "doc_type": "unknown",
                "label": "Additional document",
                "filename": doc["filename"],
                "badge": "amber",
                "state": "Not classified",
                "detail": "This file did not match any mandatory document type; it was indexed for reference only.",
            })
    return rows


# --------------------------------------------------------------------------- #
# Core processing
# --------------------------------------------------------------------------- #
def process_documents(files: List[Dict[str, bytes]], source_label: str) -> Dict[str, Any]:
    """Shared pipeline for uploaded files and for the bundled sample presets."""
    if not files:
        raise HTTPException(status_code=400, detail="No vendor documents were provided.")

    documents: List[Dict[str, Any]] = []
    merged: Dict[str, Any] = {}
    extraction_modes: List[str] = []

    for item in files:
        filename, data = item["filename"], item["data"]
        text = extract_pdf_text(data, filename)
        fields, mode = rag.extract_fields(text, filename)
        extraction_modes.append(mode)
        doc_type = classify_document(filename, text, fields.get("document_type"))

        documents.append({
            "filename": filename,
            "doc_type": doc_type,
            "label": DOC_LABELS.get(doc_type, "Unclassified document"),
            "chars": len(text),
            "fields": fields,
            "evidence": {**build_evidence(filename, text), "doc_type": doc_type},
            "text_preview": text[:500],
        })

        # merge key-values, first non-empty wins per key
        for key, value in fields.items():
            if key != "document_type" and value not in (None, "", []) and key not in merged:
                merged[key] = value

    checks = run_rules(documents, merged)
    status, status_label = overall_status(checks)

    # ---- retrieval ---------------------------------------------------------
    # Failing and deviating checks are retrieved first so the citation panel
    # always leads with the clauses the reviewer actually has to act on.
    ordered = ([c for c in checks if c["status"] == "fail"]
               + [c for c in checks if c["status"] == "gap"]
               + [c for c in checks if c["status"] == "pass"])

    policy_hits: List[Dict[str, Any]] = []
    seen_queries, seen_texts = set(), set()
    for check in ordered:
        query = check.get("policy_query")
        if not query or query in seen_queries or len(policy_hits) >= 6:
            continue
        seen_queries.add(query)
        for hit in rag.search_policies(query, k=2):
            key = hit["text"][:120]
            if key in seen_texts:
                continue
            seen_texts.add(key)
            # remember which check pulled this clause in - drives citation pairing
            hit["for_check"] = check["id"]
            hit["check_status"] = check["status"]
            policy_hits.append(hit)
            break  # one clause per check keeps the citation panel readable

    if not policy_hits:
        for hit in rag.search_policies("mandatory supplier onboarding document checklist", k=3):
            policy_hits.append(hit)
    policy_hits = policy_hits[:6]

    evidence = [doc["evidence"] for doc in documents]
    vendor_name = merged.get("company_name") or "Unnamed vendor"

    summary, summary_mode = rag.generate_validation_summary(
        vendor_name=vendor_name,
        checks=checks,
        extracted=merged,
        policy_hits=policy_hits,
        evidence=evidence,
        overall_status=status,
    )

    # the email draft is always regenerated deterministically if the model omitted one
    if not summary.get("email_draft"):
        blocking = [c for c in checks if c["status"] in ("fail", "gap")]
        summary["email_draft"] = rag.build_email_draft(vendor_name, blocking, status)

    case_id = f"VND-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    case = {
        "case_id": case_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": source_label,
        "vendor": {
            "company_name": vendor_name,
            "tax_id": merged.get("tax_id"),
            "contact_email": merged.get("contact_email"),
        },
        "status": status,
        "status_label": status_label,
        "documents": documents,
        "checklist": build_checklist(documents, checks),
        "checks": checks,
        "extracted": merged,
        "policy_hits": policy_hits,
        "citations": summary.get("citations", []),
        "ai": {
            "summary": summary.get("summary", ""),
            "recommendation": summary.get("recommendation"),
            "risk_level": summary.get("risk_level"),
            "outstanding_actions": summary.get("outstanding_actions", []),
            "email_draft": summary.get("email_draft"),
            "mode": summary_mode,
            "extraction_mode": "live" if "live" in extraction_modes else "fallback",
            "models": {
                "extraction": rag.MODEL_FAST_JSON,
                "embeddings": rag.MODEL_EMBEDDING,
                "reasoning": rag.MODEL_REASONING,
            },
        },
        "knowledge_base": rag.knowledge_base_stats(),
        "events": [
            {"at": datetime.now().isoformat(timespec="seconds"),
             "actor": "system",
             "event": "case_created",
             "detail": f"{len(documents)} document(s) processed from {source_label}."}
        ],
    }

    CASES[case_id] = case
    persist_case(case)
    return case


def persist_case(case: Dict[str, Any]) -> None:
    """Write the audit record to disk so it survives a server restart."""
    try:
        path = os.path.join(AUDIT_DIR, f"{case['case_id']}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(case, handle, indent=2, default=str)
    except OSError as exc:
        log.warning("Could not persist audit record: %s", exc)


def record_event(case_id: str, actor: str, event: str, detail: str) -> Dict[str, Any]:
    case = CASES.get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case {case_id} was not found.")
    case["events"].append({
        "at": datetime.now().isoformat(timespec="seconds"),
        "actor": actor,
        "event": event,
        "detail": detail,
    })
    persist_case(case)
    return case


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class PresetRequest(BaseModel):
    preset: str = Field(..., description="Folder name inside /test_docs")


class EmailRequest(BaseModel):
    to: str
    subject: str
    body: str
    case_id: Optional[str] = None
    kind: str = "correction_notice"


class DecisionRequest(BaseModel):
    case_id: str
    decision: str = Field(..., description="approved | exception_approved | resubmission_requested")
    reviewer: str = "Procurement Officer"
    notes: str = ""


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health() -> Dict[str, Any]:
    """Status banner data: which subsystems are live vs running on fallbacks."""
    return {
        "status": "ok",
        "gateway_configured": rag.gateway_configured(),
        "gateway_base_url": rag.BASE_URL if rag.gateway_configured() else None,
        "email_configured": bool(os.getenv("RESEND_API_KEY")),
        "knowledge_base": rag.knowledge_base_stats(),
        "presets": list_presets(),
        "policy": {
            "min_insurance_coverage": MIN_INSURANCE_COVERAGE,
            "expiry_warning_days": EXPIRY_WARNING_DAYS,
            "required_documents": REQUIRED_DOCUMENTS,
        },
        "models": {
            "transcription": rag.MODEL_WHISPER,
            "embeddings": rag.MODEL_EMBEDDING,
            "extraction": rag.MODEL_FAST_JSON,
            "reasoning": rag.MODEL_REASONING,
        },
    }


@app.post("/api/upload-reference")
async def upload_reference(files: List[UploadFile] = File(...)) -> Dict[str, Any]:
    """Chunk, embed and index reference policy PDFs into ChromaDB."""
    indexed: List[Dict[str, Any]] = []
    for upload in files:
        if not upload.filename.lower().endswith(".pdf"):
            indexed.append({"filename": upload.filename, "chunks": 0,
                            "error": "Only PDF files can be indexed."})
            continue
        data = read_upload(upload)
        text = extract_pdf_text(data, upload.filename)
        result = rag.index_reference_document(upload.filename, text)
        indexed.append(result)
        log.info("Indexed %s -> %s chunks (%s)", upload.filename, result.get("chunks"), result.get("mode"))

    stats = rag.knowledge_base_stats()
    return {"indexed": indexed, "knowledge_base": stats,
            "total_chunks": stats.get("chunks", 0)}


@app.delete("/api/reference")
def clear_reference() -> Dict[str, Any]:
    """Wipe the knowledge base - useful between demo runs."""
    rag.reset_knowledge_base()
    return {"cleared": True, "knowledge_base": rag.knowledge_base_stats()}


@app.get("/api/presets")
def presets() -> Dict[str, Any]:
    return {"presets": list_presets(), "test_docs_dir": TEST_DOCS_DIR}


def list_presets() -> List[Dict[str, Any]]:
    """Discover sample vendor packages generated by generate_test_pdfs.py."""
    if not os.path.isdir(TEST_DOCS_DIR):
        return []
    descriptions = {
        "Vendor_Happy_Path": "All five documents valid, USD 1.5M cover",
        "Vendor_Expired_Tax": "Tax certificate lapsed, bank proof missing",
        "Vendor_Low_Insurance": "USD 500k cover against a USD 1M minimum",
    }
    out: List[Dict[str, Any]] = []
    for name in sorted(os.listdir(TEST_DOCS_DIR)):
        folder = os.path.join(TEST_DOCS_DIR, name)
        if not os.path.isdir(folder) or name == "Reference_Policy":
            continue
        pdfs = sorted(f for f in os.listdir(folder) if f.lower().endswith(".pdf"))
        if pdfs:
            out.append({"name": name,
                        "label": name.replace("Vendor_", "").replace("_", " "),
                        "description": descriptions.get(name, f"{len(pdfs)} sample documents"),
                        "files": pdfs})
    return out


@app.post("/api/process-vendor")
async def process_vendor(files: List[UploadFile] = File(...)) -> Dict[str, Any]:
    """Full ingest -> extract -> verify -> retrieve -> summarise pipeline."""
    payload = []
    for upload in files:
        if not upload.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400,
                                detail=f"{upload.filename} is not a PDF. Upload vendor documents as PDFs.")
        payload.append({"filename": upload.filename, "data": read_upload(upload)})
    return process_documents(payload, source_label="upload")


@app.post("/api/process-preset")
def process_preset(request: PresetRequest) -> Dict[str, Any]:
    """One-click demo: run the pipeline over a bundled sample vendor package."""
    valid = {p["name"] for p in list_presets()}
    if request.preset not in valid:
        raise HTTPException(status_code=404,
                            detail=f"Unknown preset '{request.preset}'. Run generate_test_pdfs.py first.")
    folder = os.path.join(TEST_DOCS_DIR, request.preset)
    payload = []
    for name in sorted(os.listdir(folder)):
        if name.lower().endswith(".pdf"):
            with open(os.path.join(folder, name), "rb") as handle:
                payload.append({"filename": name, "data": handle.read()})
    return process_documents(payload, source_label=f"preset:{request.preset}")


@app.post("/api/index-sample-policies")
def index_sample_policies() -> Dict[str, Any]:
    """Convenience endpoint: index everything in /test_docs/Reference_Policy."""
    folder = os.path.join(TEST_DOCS_DIR, "Reference_Policy")
    if not os.path.isdir(folder):
        raise HTTPException(status_code=404,
                            detail="No sample policies found. Run python generate_test_pdfs.py first.")
    indexed = []
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".pdf"):
            continue
        with open(os.path.join(folder, name), "rb") as handle:
            text = extract_pdf_text(handle.read(), name)
        indexed.append(rag.index_reference_document(name, text))
    stats = rag.knowledge_base_stats()
    return {"indexed": indexed, "knowledge_base": stats, "total_chunks": stats.get("chunks", 0)}


@app.post("/api/transcribe")
async def transcribe(audio: UploadFile = File(...)) -> Dict[str, Any]:
    """Transcribe a browser microphone recording for the reviewer notes field."""
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="The recording was empty. Try again.")
    text, mode = rag.transcribe_audio(data, audio.filename or "dictation.webm")
    return {"text": text, "mode": mode, "model": rag.MODEL_WHISPER, "bytes": len(data)}


@app.post("/api/send-email")
def send_email(request: EmailRequest) -> Dict[str, Any]:
    """
    Dispatch the correction / approval notice through Resend.

    Without RESEND_API_KEY the endpoint returns the rendered message as a preview
    instead of failing, so the flow is still demonstrable.
    """
    api_key = os.getenv("RESEND_API_KEY")
    sender = os.getenv("RESEND_FROM", "Vendor Onboarding <onboarding@resend.dev>")

    if not request.to or "@" not in request.to:
        raise HTTPException(status_code=400, detail="Enter a valid recipient email address.")

    if not api_key:
        if request.case_id and request.case_id in CASES:
            record_event(request.case_id, "system", "email_previewed",
                         f"Preview only - no RESEND_API_KEY set. Recipient: {request.to}")
        return {
            "sent": False,
            "mode": "preview",
            "message": "No RESEND_API_KEY configured, so the notice was rendered but not sent.",
            "preview": {"from": sender, "to": request.to,
                        "subject": request.subject, "body": request.body},
        }

    try:
        import resend

        resend.api_key = api_key
        result = resend.Emails.send({
            "from": sender,
            "to": [request.to],
            "subject": request.subject,
            "text": request.body,
        })
        email_id = result.get("id") if isinstance(result, dict) else str(result)
        if request.case_id and request.case_id in CASES:
            record_event(request.case_id, "procurement_officer", "email_sent",
                         f"{request.kind} sent to {request.to} (Resend id {email_id}).")
        return {"sent": True, "mode": "resend", "id": email_id, "to": request.to}
    except Exception as exc:  # noqa: BLE001
        log.exception("Resend dispatch failed")
        return JSONResponse(
            status_code=502,
            content={"sent": False, "mode": "error",
                     "message": f"Resend rejected the request: {exc}"},
        )


@app.post("/api/decision")
def decision(request: DecisionRequest) -> Dict[str, Any]:
    """Record an approval, exception sign-off or resubmission request."""
    labels = {
        "approved": "Vendor approved",
        "exception_approved": "Approved with documented compliance exception",
        "resubmission_requested": "Resubmission requested from vendor",
    }
    if request.decision not in labels:
        raise HTTPException(status_code=400, detail=f"Unknown decision '{request.decision}'.")

    case = record_event(
        request.case_id,
        request.reviewer,
        request.decision,
        f"{labels[request.decision]}. Reviewer notes: {request.notes.strip() or 'none recorded'}",
    )
    case["decision"] = {
        "outcome": request.decision,
        "label": labels[request.decision],
        "reviewer": request.reviewer,
        "notes": request.notes,
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    persist_case(case)
    return {"case_id": case["case_id"], "decision": case["decision"], "events": case["events"]}


@app.post("/api/notes")
def save_notes(case_id: str = Body(..., embed=True), notes: str = Body(..., embed=True)) -> Dict[str, Any]:
    """Persist reviewer notes (typed or dictated) onto the case."""
    case = CASES.get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case {case_id} was not found.")
    case["reviewer_notes"] = notes
    persist_case(case)
    return {"case_id": case_id, "saved": True}


@app.get("/api/audit/{case_id}")
def audit(case_id: str) -> Dict[str, Any]:
    """Full audit record - what the Download Audit Log button retrieves."""
    case = CASES.get(case_id)
    if case:
        return case
    path = os.path.join(AUDIT_DIR, f"{case_id}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    raise HTTPException(status_code=404, detail=f"Case {case_id} was not found.")


@app.get("/")
def root() -> Dict[str, str]:
    return {"service": "AI Vendor Onboarding & Approval Assistant",
            "docs": "/docs", "health": "/api/health"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
