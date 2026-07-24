"""
rag_service.py
==============
Everything that touches the vector store or the LLM gateway lives here.

Responsibilities
----------------
1. Chunk + embed reference policy PDFs into a persistent chromadb collection.
2. Retrieve grounded policy clauses for a query.
3. Extract structured JSON from vendor document text (fast model).
4. Compose the grounded AI validation summary + citations (reasoning model).
5. Transcribe reviewer dictation (whisper model).

Design note - "never break the demo"
------------------------------------
Every network call is wrapped. If the gateway is unreachable or no API key is
configured, the service transparently falls back to a deterministic local
implementation (hashed lexical embeddings, regex field extraction, template
summary). The active mode is reported back to the UI as ``ai_mode`` so nothing
is silently faked.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import chromadb
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

log = logging.getLogger("rag_service")

# --------------------------------------------------------------------------- #
# Model identifiers - exactly as provisioned on the gateway
# --------------------------------------------------------------------------- #
MODEL_WHISPER = "azure/genailab-maas-whisper"
MODEL_EMBEDDING = "azure/genailab-maas-text-embedding-3-large"
MODEL_FAST_JSON = "azure/genailab-maas-gpt-4o-mini"
MODEL_REASONING = "genailab-maas-gpt-4o"

# --------------------------------------------------------------------------- #
# Gateway configuration
# --------------------------------------------------------------------------- #
API_KEY = os.getenv("GENAILAB_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
BASE_URL = os.getenv("GENAILAB_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
REQUEST_TIMEOUT = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))

DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))
CHROMA_DIR = os.path.abspath(os.path.join(DATA_DIR, "chroma"))
COLLECTION_NAME = "procurement_policies"

EMBED_DIM_FALLBACK = 384  # dimensionality of the local hashed embedding

_client = None  # lazily-created OpenAI client


def gateway_configured() -> bool:
    """True when an API key is present, i.e. live model calls are possible."""
    return bool(API_KEY)


def get_client():
    """Lazily build the OpenAI-compatible client pointed at the gateway."""
    global _client
    if _client is None:
        from openai import OpenAI  # imported lazily so the app boots without it

        _client = OpenAI(api_key=API_KEY or "not-configured", base_url=BASE_URL, timeout=REQUEST_TIMEOUT)
    return _client


# --------------------------------------------------------------------------- #
# Text chunking
# --------------------------------------------------------------------------- #
def chunk_text(text: str, chunk_size: int = 700, overlap: int = 120) -> List[str]:
    """
    Paragraph-aware chunker. Keeps whole clauses together where possible so a
    retrieved chunk reads like a quotable policy clause rather than a fragment.
    """
    text = re.sub(r"[ \t]+", " ", text or "").strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n(?=##|\d+\.\d|Clause )", text) if p.strip()]
    chunks: List[str] = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 1 <= chunk_size:
            current = f"{current}\n{para}".strip()
            continue
        if current:
            chunks.append(current)
        # a single oversized paragraph is hard-split with overlap
        while len(para) > chunk_size:
            chunks.append(para[:chunk_size])
            para = para[max(0, chunk_size - overlap):]
        current = para

    if current:
        chunks.append(current)
    return [c for c in chunks if len(c.strip()) > 40]


# --------------------------------------------------------------------------- #
# Embeddings
# --------------------------------------------------------------------------- #
_TOKEN_RE = re.compile(r"[a-z0-9$][a-z0-9$.,']*")


def _local_embedding(text: str, dim: int = EMBED_DIM_FALLBACK) -> List[float]:
    """
    Deterministic hashed bag-of-words embedding used when the gateway is not
    reachable. Lexical only - good enough to retrieve the right policy clause
    from a small corpus, and it keeps the demo running offline.
    """
    vec = [0.0] * dim
    tokens = _TOKEN_RE.findall((text or "").lower())
    for token in tokens:
        token = token.strip(".,")
        if not token:
            continue
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += 1.0
        # a second hashed slot reduces collisions
        vec[(h >> 16) % dim] += 0.5
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def embed_texts(texts: List[str]) -> Tuple[List[List[float]], str]:
    """
    Embed a batch of strings.

    Returns ``(vectors, mode)`` where mode is ``"live"`` or ``"fallback"``.
    """
    texts = [t if t.strip() else " " for t in texts]
    if not texts:
        return [], "fallback"

    if gateway_configured():
        try:
            response = get_client().embeddings.create(model=MODEL_EMBEDDING, input=texts)
            vectors = [item.embedding for item in response.data]
            if vectors and len(vectors) == len(texts):
                return vectors, "live"
            log.warning("Embedding response size mismatch - using local fallback")
        except Exception as exc:  # noqa: BLE001 - demo must survive gateway errors
            log.warning("Embedding call failed (%s) - using local fallback", exc)

    return [_local_embedding(t) for t in texts], "fallback"


# --------------------------------------------------------------------------- #
# chromadb
# --------------------------------------------------------------------------- #
class _PassthroughEmbeddingFunction:
    """
    Chroma wants an embedding function on the collection, but this service always
    supplies vectors explicitly. This satisfies the interface without pulling in
    Chroma's default ONNX model download.
    """
    

    def __call__(self, input: List[str]) -> List[List[float]]:  # noqa: A002 - Chroma's arg name
        return [_local_embedding(t) for t in input]

    @staticmethod
    def name() -> str:
        return "genailab-passthrough"


_chroma_client = None


def get_collection():
    """Return (creating if needed) the persistent policy collection."""
    global _chroma_client
    if _chroma_client is None:
        os.makedirs(CHROMA_DIR, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
    try:
        return _chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=_PassthroughEmbeddingFunction(),
            metadata={"hnsw:space": "cosine"},
        )
    except Exception:  # older/newer Chroma signatures
        return _chroma_client.get_or_create_collection(name=COLLECTION_NAME)


def index_reference_document(filename: str, text: str) -> Dict[str, Any]:
    """
    Chunk, embed and upsert one reference policy document.

    Re-uploading the same filename replaces its chunks rather than duplicating
    them, so repeated demo runs stay clean.
    """
    chunks = chunk_text(text)
    if not chunks:
        return {"filename": filename, "chunks": 0, "mode": "fallback",
                "error": "No machine-readable text found in this PDF."}

    collection = get_collection()

    # drop any previous version of this file
    try:
        collection.delete(where={"source": filename})
    except Exception as exc:  # noqa: BLE001
        log.debug("No previous chunks to delete for %s (%s)", filename, exc)

    vectors, mode = embed_texts(chunks)
    doc_key = hashlib.md5(filename.encode("utf-8")).hexdigest()[:10]
    ids = [f"{doc_key}-{i:04d}" for i in range(len(chunks))]
    metadatas = [
        {
            "source": filename,
            "chunk_index": i,
            "clause": _guess_clause_label(chunk),
            "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "embedding_mode": mode,
        }
        for i, chunk in enumerate(chunks)
    ]

    collection.add(ids=ids, documents=chunks, embeddings=vectors, metadatas=metadatas)
    return {"filename": filename, "chunks": len(chunks), "mode": mode}


_CLAUSE_RE = re.compile(r"(Clause\s+\d+(?:\.\d+)*|AR-\d+|Section\s+\d+(?:\.\d+)*)", re.IGNORECASE)


def _guess_clause_label(chunk: str) -> str:
    """Pull a human-friendly clause reference out of a chunk for the citation UI."""
    match = _CLAUSE_RE.search(chunk)
    if match:
        return match.group(1).strip()
    first_line = chunk.strip().splitlines()[0] if chunk.strip() else ""
    return (first_line[:60] + "...") if len(first_line) > 60 else (first_line or "Policy excerpt")


def search_policies(query: str, k: int = 3) -> List[Dict[str, Any]]:
    """Vector-search the policy corpus. Returns [] when nothing is indexed."""
    collection = get_collection()
    try:
        if collection.count() == 0:
            return []
    except Exception:  # noqa: BLE001
        return []

    vectors, _mode = embed_texts([query])
    try:
        result = collection.query(
            query_embeddings=vectors,
            n_results=k,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:  # noqa: BLE001
        
        log.warning("Vector search failed: %s", exc)
        return []

    hits: List[Dict[str, Any]] = []
    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]

    for doc, meta, dist in zip(documents, metadatas, distances):
        meta = meta or {}
        hits.append(
            {
                "text": (doc or "").strip(),
                "source": meta.get("source", "policy"),
                "clause": meta.get("clause", "Policy excerpt"),
                "score": round(max(0.0, 1.0 - float(dist)), 3),
            }
        )
    return hits


def knowledge_base_stats() -> Dict[str, Any]:
    """Chunk count and indexed filenames, for the Tab 1 status panel."""
    try:
        collection = get_collection()
        count = collection.count()
        sources: List[str] = []
        if count:
            peek = collection.get(include=["metadatas"], limit=min(count, 2000))
            seen = []
            for meta in peek.get("metadatas") or []:
                src = (meta or {}).get("source")
                if src and src not in seen:
                    seen.append(src)
            sources = seen
        return {"chunks": count, "documents": sources, "ready": count > 0}
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not read knowledge base stats: %s", exc)
        return {"chunks": 0, "documents": [], "ready": False, "error": str(exc)}


def reset_knowledge_base() -> None:
    """Delete every indexed chunk - handy between demo runs."""
    global _chroma_client
    if _chroma_client is None:
        get_collection()
    try:
        _chroma_client.delete_collection(COLLECTION_NAME)
    except Exception as exc:  # noqa: BLE001
        log.debug("Collection delete skipped: %s", exc)
    get_collection()


# --------------------------------------------------------------------------- #
# Structured extraction (fast model)
# --------------------------------------------------------------------------- #
EXTRACTION_SCHEMA_HINT = """{
  "company_name": string|null,
  "document_type": one of ["registration_certificate","tax_certificate","bank_proof",
                           "insurance_certificate","compliance_declaration","unknown"],
  "tax_id": string|null,
  "tax_expiry_date": "YYYY-MM-DD"|null,
  "registration_number": string|null,
  "bank_account_number": string|null,
  "insurance_policy_number": string|null,
  "insurance_coverage_amount": number|null,   // numeric only, no currency symbols
  "insurance_currency": string|null,
  "insurance_expiry_date": "YYYY-MM-DD"|null,
  "signatory_name": string|null,
  "signature_date": "YYYY-MM-DD"|null,
  "contact_email": string|null
}"""

EXTRACTION_SYSTEM = (
    "You extract structured data from procurement documents. "
    "Return ONLY a JSON object, no prose and no markdown fences. "
    "Use null for anything not explicitly stated in the text - never guess. "
    "Normalise all dates to YYYY-MM-DD and all money amounts to a plain number."
)


def extract_fields(text: str, filename: str) -> Tuple[Dict[str, Any], str]:
    """
    Extract key-values from one document.

    Returns ``(fields, mode)``. Falls back to regex extraction on any failure.
    """
    excerpt = (text or "")[:6000]

    if gateway_configured():
        try:
            response = get_client().chat.completions.create(
                model=MODEL_FAST_JSON,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            f"File name: {filename}\n"
                            f"Return JSON matching this shape:\n{EXTRACTION_SCHEMA_HINT}\n\n"
                            f"DOCUMENT TEXT:\n---\n{excerpt}\n---"
                        ),
                    },
                ],
            )
            payload = _loads_lenient(response.choices[0].message.content)
            if isinstance(payload, dict):
                merged = regex_extract(text)           # regex fills gaps the model left null
                merged.update({k: v for k, v in payload.items() if v not in (None, "", [])})
                return _normalise_fields(merged), "live"
        except Exception as exc:  # noqa: BLE001
            log.warning("Field extraction failed for %s (%s) - using regex fallback", filename, exc)

    return _normalise_fields(regex_extract(text)), "fallback"


def _loads_lenient(raw: Optional[str]) -> Any:
    """Parse JSON that may arrive wrapped in markdown fences or prose."""
    if not raw:
        return None
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


# ---- regex fallback extraction -------------------------------------------- #
_LABEL_PATTERNS: Dict[str, List[str]] = {
    "company_name": [r"(?:Legal Entity Name|Named Insured|Account Holder Name|Company Name)\s*[:\-]\s*(.+)"],
    "tax_id": [r"(?:Tax Identification Number|Tax ID|TIN|GSTIN|VAT (?:No|Number))\s*[:\-]\s*([A-Z0-9\-]+)"],
    "registration_number": [r"(?:Registration Number|CIN|Company Number)\s*[:\-]\s*([A-Z0-9\-]+)"],
    "bank_account_number": [r"(?:Account Number|A/C No\.?)\s*[:\-]\s*([A-Z0-9\-]+)"],
    "insurance_policy_number": [r"(?:Policy Number|Policy No\.?)\s*[:\-]\s*([A-Z0-9\-]+)"],
    "signatory_name": [r"(?:Authorised Signatory|Authorized Signatory)\s*[:\-]\s*(.+)"],
    "contact_email": [r"[\w.\-+]+@[\w\-]+\.[\w.\-]+"],
}

_DATE_LABELS = {
    "tax_expiry_date": r"(?:Valid Through|Tax Expiry Date|Certificate Valid Until)\s*[:\-]\s*([0-9A-Za-z ,/\-]+)",
    "insurance_expiry_date": r"(?:Policy Expiry Date|Insurance Expiry Date|Expiration Date)\s*[:\-]\s*([0-9A-Za-z ,/\-]+)",
    "signature_date": r"(?:Date of Signature|Signed On)\s*[:\-]\s*([0-9A-Za-z ,/\-]+)",
}

_MONEY_RE = re.compile(
    r"(?:Coverage Limit[^:\n]*|Aggregate Limit|Coverage Amount|Limit of Liability)\s*[:\-]\s*"
    r"(?:(USD|INR|EUR|GBP|\$)\s*)?([\d.,]+)\s*(million|m|k)?",
    re.IGNORECASE,
)


def regex_extract(text: str) -> Dict[str, Any]:
    """Deterministic label-driven extraction - the offline safety net."""
    text = text or ""
    fields: Dict[str, Any] = {}

    for key, patterns in _LABEL_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                value = (match.group(1) if match.groups() else match.group(0)).strip()
                if value:
                    fields[key] = value
                break

    for key, pattern in _DATE_LABELS.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            parsed = parse_date(match.group(1))
            if parsed:
                fields[key] = parsed

    # generic expiry: catch "Expiry Date: ..." when the labelled variants miss
    if "tax_expiry_date" not in fields and re.search(r"tax registration", text, re.IGNORECASE):
        match = re.search(r"Expiry Date\s*[:\-]\s*([0-9A-Za-z ,/\-]+)", text, re.IGNORECASE)
        if match:
            fields["tax_expiry_date"] = parse_date(match.group(1))

    money = _MONEY_RE.search(text)
    if money:
        currency, amount, suffix = money.groups()
        value = _to_number(amount, suffix)
        if value is not None:
            fields["insurance_coverage_amount"] = value
            fields["insurance_currency"] = "USD" if (currency in (None, "$")) else currency.upper()

    return fields


def _to_number(amount: str, suffix: Optional[str]) -> Optional[float]:
    try:
        value = float(amount.replace(",", ""))
    except (TypeError, ValueError):
        return None
    if suffix:
        suffix = suffix.lower()
        if suffix in ("million", "m"):
            value *= 1_000_000
        elif suffix == "k":
            value *= 1_000
    return value


def parse_date(raw: Any) -> Optional[str]:
    """Parse a loosely-formatted date into an ISO string, or None."""
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        return None
    text = str(raw).strip().strip(".,")
    if not text:
        return None
    try:
        from dateutil import parser as date_parser

        parsed = date_parser.parse(text, fuzzy=True, dayfirst=False)
        return parsed.date().isoformat()
    except Exception:  # noqa: BLE001
        match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
        return match.group(0) if match else None


def _normalise_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce model output into predictable types."""
    out = dict(fields or {})

    for key in ("tax_expiry_date", "insurance_expiry_date", "signature_date"):
        if out.get(key):
            out[key] = parse_date(out[key])

    amount = out.get("insurance_coverage_amount")
    if isinstance(amount, str):
        digits = re.sub(r"[^\d.]", "", amount)
        suffix = "million" if re.search(r"million|\bm\b", amount, re.IGNORECASE) else None
        out["insurance_coverage_amount"] = _to_number(digits, suffix)
    elif isinstance(amount, (int, float)):
        out["insurance_coverage_amount"] = float(amount)

    for key in ("company_name", "tax_id", "registration_number", "signatory_name"):
        if isinstance(out.get(key), str):
            out[key] = re.sub(r"\s+", " ", out[key]).strip(" .,:;")

    return {k: v for k, v in out.items() if v not in (None, "", [])}


# --------------------------------------------------------------------------- #
# Grounded validation summary (reasoning model)
# --------------------------------------------------------------------------- #
SUMMARY_SYSTEM = (
    "You are a procurement compliance analyst. You are given (a) numbered policy excerpts "
    "retrieved from the buyer's own policy corpus and (b) numbered evidence snippets taken "
    "verbatim from the vendor's submitted documents, plus the results of deterministic checks.\n\n"
    "Rules you must follow:\n"
    "- Ground every statement in the supplied material. Never invent a policy requirement.\n"
    "- Every citation must reference an existing policy id (P1, P2, ...) and, where evidence "
    "exists, a vendor evidence id (V1, V2, ...).\n"
    "- The deterministic check results are authoritative; do not contradict them.\n"
    "- Write the summary as plain sentences that read well when spoken aloud by a screen "
    "reader: no markdown, no bullet characters, no tables.\n"
    "- Return ONLY a JSON object."
)

SUMMARY_SCHEMA_HINT = """{
  "summary": "3-6 sentence plain-language verdict, safe to read aloud",
  "recommendation": "APPROVE" | "REQUEST_RESUBMISSION" | "COMPLIANCE_REVIEW",
  "risk_level": "low" | "medium" | "high",
  "citations": [
    {"policy_ref":"P1","vendor_ref":"V2"|null,"verdict":"pass"|"fail"|"gap",
     "rationale":"one sentence linking the clause to the evidence"}
  ],
  "outstanding_actions": ["imperative sentence the vendor or reviewer must act on"],
  "email_draft": {"subject":"...","body":"plain-text email to the vendor, no markdown"}
}"""


def generate_validation_summary(
    *,
    vendor_name: str,
    checks: List[Dict[str, Any]],
    extracted: Dict[str, Any],
    policy_hits: List[Dict[str, Any]],
    evidence: List[Dict[str, Any]],
    overall_status: str,
) -> Tuple[Dict[str, Any], str]:
    """
    Ask the reasoning model for the audit summary + side-by-side citations.

    Citation text is resolved server-side from ``policy_hits`` and ``evidence``
    so the displayed clause is always the retrieved text, never model prose.
    Returns ``(summary_payload, mode)``.
    """
    policy_block = "\n".join(
        f"[P{i+1}] (source: {hit['source']} | {hit['clause']}) {hit['text'][:900]}"
        for i, hit in enumerate(policy_hits)
    ) or "[none] No policy documents have been indexed yet."

    evidence_block = "\n".join(
        f"[V{i+1}] (document: {ev['document']}) {ev['text'][:600]}" for i, ev in enumerate(evidence)
    ) or "[none] No vendor evidence extracted."

    checks_block = "\n".join(
        f"- {c['title']}: {c['status'].upper()} - {c['detail']}" for c in checks
    ) or "- No deterministic checks were run."

    if gateway_configured():
        try:
            response = get_client().chat.completions.create(
                model=MODEL_REASONING,
                temperature=0.1,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SUMMARY_SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            f"VENDOR: {vendor_name}\n"
                            f"DETERMINISTIC OUTCOME: {overall_status}\n\n"
                            f"DETERMINISTIC CHECK RESULTS:\n{checks_block}\n\n"
                            f"EXTRACTED KEY-VALUES:\n{json.dumps(extracted, indent=2)}\n\n"
                            f"RETRIEVED POLICY EXCERPTS:\n{policy_block}\n\n"
                            f"VENDOR EVIDENCE SNIPPETS:\n{evidence_block}\n\n"
                            f"Return JSON in this shape:\n{SUMMARY_SCHEMA_HINT}"
                        ),
                    },
                ],
            )
            payload = _loads_lenient(response.choices[0].message.content)
            if isinstance(payload, dict) and payload.get("summary"):
                payload["citations"] = _resolve_citations(payload.get("citations"), policy_hits, evidence)
                payload.setdefault("outstanding_actions", [])
                payload.setdefault("risk_level", "medium")
                payload.setdefault("recommendation", _default_recommendation(overall_status))
                return payload, "live"
        except Exception as exc:  # noqa: BLE001
            log.warning("Summary generation failed (%s) - using deterministic template", exc)

    return _template_summary(vendor_name, checks, policy_hits, evidence, overall_status), "fallback"


def _resolve_citations(
    raw_citations: Any, policy_hits: List[Dict[str, Any]], evidence: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Map P#/V# references back to the real retrieved text (anti-hallucination)."""
    resolved: List[Dict[str, Any]] = []
    for item in raw_citations or []:
        if not isinstance(item, dict):
            continue
        p_index = _ref_index(item.get("policy_ref"), len(policy_hits))
        if p_index is None:
            continue
        v_index = _ref_index(item.get("vendor_ref"), len(evidence))
        hit = policy_hits[p_index]
        ev = evidence[v_index] if v_index is not None else None
        resolved.append(
            {
                "policy_clause": hit["clause"],
                "policy_source": hit["source"],
                "policy_text": hit["text"],
                "match_score": hit.get("score"),
                "vendor_document": ev["document"] if ev else "-",
                "vendor_text": ev["text"] if ev else "No matching text was found in the submitted documents.",
                "verdict": str(item.get("verdict", "gap")).lower(),
                "rationale": item.get("rationale", ""),
            }
        )
    return resolved


def _ref_index(ref: Any, size: int) -> Optional[int]:
    if not ref:
        return None
    match = re.search(r"(\d+)", str(ref))
    if not match:
        return None
    index = int(match.group(1)) - 1
    return index if 0 <= index < size else None


def _default_recommendation(overall_status: str) -> str:
    return {
        "READY_FOR_APPROVAL": "APPROVE",
        "RESUBMISSION_REQUIRED": "REQUEST_RESUBMISSION",
        "RISK_EXCEPTION": "COMPLIANCE_REVIEW",
    }.get(overall_status, "COMPLIANCE_REVIEW")


def _template_summary(
    vendor_name: str,
    checks: List[Dict[str, Any]],
    policy_hits: List[Dict[str, Any]],
    evidence: List[Dict[str, Any]],
    overall_status: str,
) -> Dict[str, Any]:
    """Deterministic summary used when the reasoning model is unavailable."""
    failed = [c for c in checks if c["status"] == "fail"]
    gaps = [c for c in checks if c["status"] == "gap"]
    passed = [c for c in checks if c["status"] == "pass"]

    def join(items: List[Dict[str, Any]]) -> str:
        return "; ".join(c["detail"].rstrip(".") for c in items)

    lines = [
        f"{vendor_name or 'This vendor'} submitted a package in which "
        f"{len(passed)} of {len(checks)} policy checks passed."
    ]
    if failed:
        lines.append(f"Blocking issues: {join(failed)}.")
    if gaps:
        lines.append(f"Policy deviations needing a compliance decision: {join(gaps)}.")
    if not failed and not gaps:
        lines.append("No blocking issues or policy deviations were detected, so the package is "
                     "ready for procurement approval.")
    if policy_hits:
        lines.append(f"The assessment is grounded in {len(policy_hits)} retrieved policy excerpts, "
                     f"including {policy_hits[0]['clause']} from {policy_hits[0]['source']}.")
    else:
        lines.append("No reference policy has been indexed yet, so only deterministic checks "
                     "were applied.")

    checks_by_id = {c["id"]: c for c in checks}
    evidence_by_doc = {ev.get("doc_type"): ev for ev in evidence if ev.get("doc_type")}

    citations = []
    for i, hit in enumerate(policy_hits):
        # main.py tags each hit with the check whose query retrieved it
        related = checks_by_id.get(hit.get("for_check")) or (checks[i] if i < len(checks) else None)
        ev = evidence_by_doc.get(related.get("doc_type")) if related else None
        if ev is None and related and related.get("doc_type") in evidence_by_doc:
            ev = evidence_by_doc[related["doc_type"]]
        # If the document this clause governs was never submitted, say so plainly
        # rather than quoting an unrelated file.
        missing_doc = related is not None and related.get("doc_type") not in evidence_by_doc
        if ev is None and not missing_doc:
            ev = evidence[i] if i < len(evidence) else (evidence[0] if evidence else None)

        citations.append(
            {
                "policy_clause": hit["clause"],
                "policy_source": hit["source"],
                "policy_text": hit["text"],
                "match_score": hit.get("score"),
                "vendor_document": ev["document"] if ev else "Not submitted",
                "vendor_text": ev["text"] if ev else
                "No document of this type was submitted, so there is no vendor text to compare "
                "against this clause.",
                "verdict": (related or {}).get("status", "gap"),
                "rationale": (related or {}).get("detail", "Retrieved as the closest matching policy clause."),
            }
        )

    actions = [c["remedy"] for c in failed + gaps if c.get("remedy")]
    return {
        "summary": " ".join(lines),
        "recommendation": _default_recommendation(overall_status),
        "risk_level": "high" if failed else ("medium" if gaps else "low"),
        "citations": citations,
        "outstanding_actions": actions,
        "email_draft": build_email_draft(vendor_name, failed + gaps, overall_status),
    }


def build_email_draft(vendor_name: str, issues: List[Dict[str, Any]], overall_status: str) -> Dict[str, str]:
    """Structured notice matching the case outcome. Plain text, no markdown."""
    vendor_name = vendor_name or "Supplier"

    if overall_status == "READY_FOR_APPROVAL":
        return {
            "subject": f"Onboarding approved - {vendor_name}",
            "body": (
                f"Dear {vendor_name},\n\n"
                "Your onboarding package has passed every mandatory check in our supplier "
                "onboarding policy. No further documents are needed and your vendor record is "
                "being activated.\n\n"
                "Kind regards,\nProcurement Operations"
            ),
        }

    numbered = "\n".join(
        f"{i+1}. {issue['title']} - {issue['detail'].rstrip('.')}.\n   Required action: "
        f"{issue.get('remedy') or 'Please resubmit a compliant document.'}"
        for i, issue in enumerate(issues)
    ) or "1. Please resubmit your onboarding package with all mandatory documents."

    if overall_status == "RISK_EXCEPTION":
        return {
            "subject": f"Policy deviation found - {vendor_name}",
            "body": (
                f"Dear {vendor_name},\n\n"
                "Your onboarding documents are complete and current, but the review found a "
                "deviation from our procurement policy:\n\n"
                f"{numbered}\n\n"
                "Please either send an updated document that meets the stated threshold, or "
                "confirm in writing that you would like us to raise a formal exception for "
                "compliance review. We will hold your file open for 10 business days.\n\n"
                "Kind regards,\nProcurement Operations"
            ),
        }

    return {
        "subject": f"Action required - documents outstanding for {vendor_name}",
        "body": (
            f"Dear {vendor_name},\n\n"
            "Thank you for your onboarding submission. Our review found the following items that "
            "need your attention before we can proceed:\n\n"
            f"{numbered}\n\n"
            "Please upload corrected documents through the supplier portal within 7 business days. "
            "Reply to this email if any item is unclear.\n\n"
            "Kind regards,\nProcurement Operations"
        ),
    }


# --------------------------------------------------------------------------- #
# Speech to text
# --------------------------------------------------------------------------- #
def transcribe_audio(audio_bytes: bytes, filename: str = "dictation.webm") -> Tuple[str, str]:
    """
    Transcribe a browser-recorded audio blob with the whisper deployment.
    Returns ``(text, mode)``; mode is ``"unavailable"`` when no gateway is set.
    """
    if not gateway_configured():
        return (
            "Voice transcription needs a gateway API key. Add GENAILAB_API_KEY to your .env "
            "file, or type your notes here instead.",
            "unavailable",
        )
    try:
        response = get_client().audio.transcriptions.create(
            model=MODEL_WHISPER,
            file=(filename, audio_bytes),
        )
        text = getattr(response, "text", None) or str(response)
        return text.strip(), "live"
    except Exception as exc:  # noqa: BLE001
        log.warning("Transcription failed: %s", exc)
        return (f"Transcription failed: {exc}", "error")
