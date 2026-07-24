import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardList,
  Download,
  FileText,
  FolderKanban,
  GitCompare,
  ListChecks,
  Loader2,
  Mail,
  Mic,
  Moon,
  ScrollText,
  Send,
  ShieldAlert,
  Sparkles,
  Sun,
  Trash2,
  Undo2,
  UploadCloud,
  Volume2,
  X,
  XCircle,
} from "lucide-react";

/* ============================================================================
 * AI Vendor Onboarding & Approval Assistant - frontend
 * ----------------------------------------------------------------------------
 * Talks to the FastAPI backend in backend/main.py. Every request/response
 * shape below matches that file exactly - see the endpoint list in README.md.
 * ========================================================================== */

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

function cx(...parts) {
  return parts.filter(Boolean).join(" ");
}

/* ----------------------------------------------------------------------------
 * API helpers
 * -------------------------------------------------------------------------- */
async function apiFetch(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, options);
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    const message = (data && (data.detail || data.message)) || `Request failed (${res.status})`;
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return data;
}

/** XHR (not fetch) so we can surface a real upload-progress percentage. */
function xhrUpload(path, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}${path}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      let data = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        data = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error((data && (data.detail || data.message)) || `Request failed (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("Network error while uploading. Is the backend running?"));
    xhr.send(formData);
  });
}

/* ----------------------------------------------------------------------------
 * Small presentational helpers
 * -------------------------------------------------------------------------- */
const STATUS_META = {
  pass: { Icon: CheckCircle2, word: "Verified" },
  fail: { Icon: XCircle, word: "Missing / Expired" },
  gap: { Icon: AlertTriangle, word: "Policy Mismatch" },
};
const BADGE_TO_KIND = { green: "pass", red: "fail", amber: "gap" };

const CASE_STATUS_META = {
  READY_FOR_APPROVAL: { kind: "pass", label: "Ready for approval" },
  RESUBMISSION_REQUIRED: { kind: "fail", label: "Resubmission required" },
  RISK_EXCEPTION: { kind: "gap", label: "Risk / exception found" },
};

function prettifyKey(key) {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function prettifyValue(key, value) {
  if (value === null || value === undefined || value === "") return "—";
  if (key.toLowerCase().includes("amount") && typeof value === "number") {
    return `USD ${value.toLocaleString()}`;
  }
  return String(value);
}

/** Design tokens for standard vs. WCAG high-contrast mode, computed once per render. */
function useThemeTokens(hc) {
  return useMemo(
    () => ({
      hc,
      page: hc ? "bg-slate-950 text-slate-100" : "bg-slate-50 text-slate-900",
      header: hc ? "bg-slate-950/95 backdrop-blur-md border-b-2 border-slate-700" : "backdrop-blur-md bg-white/80 border-b border-slate-200",
      headerText: hc ? "text-slate-100" : "text-slate-900",
      headerMuted: hc ? "text-slate-400" : "text-slate-500",
      card: hc
        ? "bg-slate-900 border-2 border-slate-700 text-slate-100"
        : "bg-white border border-slate-200/80 shadow-sm hover:shadow-md transition-shadow duration-200 text-slate-900",
      cardDim: hc ? "bg-slate-900/70 border-2 border-slate-700/80 text-slate-100" : "bg-slate-50 border border-slate-200 text-slate-900",
      heading: hc ? "text-cyan-300" : "text-slate-900",
      muted: hc ? "text-slate-400" : "text-slate-500",
      border: hc ? "border-slate-700" : "border-slate-200",
      divider: hc ? "border-slate-700" : "border-slate-200",
      primaryBtn: hc
        ? "bg-yellow-400 text-slate-950 hover:bg-yellow-300"
        : "bg-indigo-600 text-white hover:bg-indigo-700 shadow-sm",
      secondaryBtn: hc
        ? "bg-slate-950 border-2 border-slate-100 text-slate-100 hover:bg-slate-100 hover:text-slate-950"
        : "bg-white border border-slate-200 text-slate-700 hover:border-indigo-300 hover:text-indigo-700",
      dangerBtn: hc
        ? "bg-slate-950 border-2 border-cyan-300 text-cyan-300 hover:bg-cyan-300 hover:text-slate-950"
        : "bg-rose-600 text-white hover:bg-rose-700",
      input: hc
        ? "bg-slate-950 border-2 border-slate-100 text-slate-100 placeholder-slate-400"
        : "bg-white border border-slate-200 text-slate-900 placeholder-slate-400 focus:border-indigo-400",
      tabActive: hc ? "bg-yellow-400 text-slate-950" : "bg-white text-indigo-700 shadow-sm",
      tabInactive: hc ? "text-slate-300 hover:text-white" : "text-slate-500 hover:text-slate-800",
      chip: hc ? "border-2 border-slate-100 text-slate-100" : "border border-slate-200 bg-slate-50 text-slate-600",
    }),
    [hc]
  );
}

function Spinner({ hc }) {
  return (
    <Loader2
      className={cx("inline h-4 w-4 animate-spin motion-reduce:animate-none align-[-3px]", hc ? "text-yellow-400" : "text-indigo-600")}
      aria-hidden="true"
    />
  );
}

function StatusBadge({ kind, t, label }) {
  const meta = STATUS_META[kind] || STATUS_META.gap;
  const { Icon } = meta;
  const colorClasses = t.hc
    ? "bg-slate-950 border-2 border-slate-100 text-slate-100"
    : kind === "pass"
    ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
    : kind === "fail"
    ? "bg-rose-50 text-rose-700 border border-rose-200"
    : "bg-amber-50 text-amber-700 border border-amber-200";
  return (
    <span className={cx("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold", colorClasses)}>
      <Icon className="h-3.5 w-3.5" aria-hidden="true" />
      {label || meta.word}
    </span>
  );
}

/* ----------------------------------------------------------------------------
 * Dropzone (shared by both tabs)
 * -------------------------------------------------------------------------- */
function Dropzone({ t, files, onAddFiles, onRemoveFile, hint, inputId, disabled }) {
  const [active, setActive] = useState(false);
  const inputRef = useRef(null);

  function handleFiles(fileList) {
    const pdfs = Array.from(fileList).filter(
      (f) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf")
    );
    if (pdfs.length) onAddFiles(pdfs);
  }

  return (
    <div>
      <div
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-disabled={disabled}
        aria-label={hint}
        onClick={() => !disabled && inputRef.current?.click()}
        onKeyDown={(e) => {
          if (!disabled && (e.key === "Enter" || e.key === " ")) {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setActive(true);
        }}
        onDragLeave={() => setActive(false)}
        onDrop={(e) => {
          e.preventDefault();
          setActive(false);
          if (!disabled) handleFiles(e.dataTransfer.files);
        }}
        className={cx(
          "cursor-pointer rounded-xl border-2 border-dashed px-6 py-8 text-center transition-colors",
          disabled && "cursor-not-allowed opacity-60",
          active && "dropzone-marching",
          t.hc
            ? active
              ? "border-transparent bg-slate-800"
              : "border-slate-100"
            : active
            ? "border-transparent bg-indigo-50/50"
            : "border-slate-300 bg-slate-50/60 hover:border-indigo-300 hover:bg-indigo-50/30"
        )}
      >
        <UploadCloud className={cx("mx-auto h-7 w-7", t.hc ? "text-slate-100" : "text-indigo-500")} aria-hidden="true" />
        <p className={cx("mt-2 font-display text-base font-semibold", t.hc ? "text-white" : "text-slate-900")}>Drop PDF files here</p>
        <p className={cx("mt-1 text-sm", t.muted)}>{hint}</p>
        <p className={cx("mt-3 text-xs font-medium underline", t.hc ? "text-yellow-300" : "text-indigo-600")}>or click to browse</p>
        <input
          ref={inputRef}
          id={inputId}
          type="file"
          accept="application/pdf"
          multiple
          disabled={disabled}
          className="sr-only"
          onChange={(e) => {
            handleFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>
      {files.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {files.map((f, i) => (
            <li
              key={`${f.name}-${i}`}
              className={cx("flex items-center justify-between gap-2 rounded-lg px-3 py-2 text-sm", t.card)}
            >
              <span className="flex min-w-0 items-center gap-2">
                <FileText className={cx("h-4 w-4 shrink-0", t.muted)} aria-hidden="true" />
                <span className="truncate font-mono text-xs">{f.name}</span>
              </span>
              <button
                type="button"
                onClick={() => onRemoveFile(i)}
                className={cx(
                  "flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-xs font-medium",
                  t.hc ? "text-cyan-300 hover:bg-slate-100 hover:text-slate-950" : "text-rose-600 hover:bg-rose-50"
                )}
                aria-label={`Remove ${f.name}`}
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ----------------------------------------------------------------------------
 * Approval stamp - the one deliberately bold, signature element
 * -------------------------------------------------------------------------- */
function StampMark({ t, outcome }) {
  const meta = {
    approved: { text: "APPROVED", color: "#059669" },
    exception_approved: { text: "EXCEPTION APPROVED", color: "#D97706" },
    resubmission_requested: { text: "RETURNED", color: "#E11D48" },
  }[outcome] || { text: "DECIDED", color: "#4F46E5" };

  const ringColor = t.hc ? "#FACC15" : meta.color;

  return (
    <div
      className="pointer-events-none inline-flex animate-stamp-in select-none items-center justify-center"
      role="img"
      aria-label={`Case stamped: ${meta.text.toLowerCase()}`}
    >
      <div
        className="flex h-24 w-24 -rotate-[8deg] items-center justify-center rounded-full border-4 text-center font-display text-[11px] font-bold leading-tight tracking-wide"
        style={{
          borderColor: ringColor,
          color: ringColor,
          boxShadow: `0 0 0 6px ${ringColor}1F, 0 10px 28px -6px ${ringColor}66`,
        }}
      >
        {meta.text}
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------------------
 * App
 * -------------------------------------------------------------------------- */
export default function App() {
  const [highContrast, setHighContrast] = useState(false);
  const t = useThemeTokens(highContrast);
  const [activeTab, setActiveTab] = useState("setup"); // "setup" | "review"

  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState(null);

  const [liveMessage, setLiveMessage] = useState("");
  const [speaking, setSpeaking] = useState(false);

  // ---- Tab 1: Setup Reference Rules ---------------------------------------
  const [refFiles, setRefFiles] = useState([]);
  const [refIndexing, setRefIndexing] = useState(false);
  const [refProgress, setRefProgress] = useState(0);
  const [refStage, setRefStage] = useState(null); // "uploading" | "indexing" | "done" | "error"
  const [refResult, setRefResult] = useState(null);
  const [refError, setRefError] = useState(null);

  // ---- Tab 2: Vendor Review Dashboard --------------------------------------
  const [vendorFiles, setVendorFiles] = useState([]);
  const [processing, setProcessing] = useState(false);
  const [vendorProgress, setVendorProgress] = useState(0);
  const [vendorStage, setVendorStage] = useState(null); // "uploading" | "analyzing" | "done" | "error"
  const [caseError, setCaseError] = useState(null);
  const [currentCase, setCurrentCase] = useState(null);

  const [reviewerNotes, setReviewerNotes] = useState("");
  const [notesSaved, setNotesSaved] = useState(false);
  const [notesError, setNotesError] = useState(null);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);

  const [emailModalOpen, setEmailModalOpen] = useState(false);
  const [emailTo, setEmailTo] = useState("");
  const [emailSubject, setEmailSubject] = useState("");
  const [emailBody, setEmailBody] = useState("");
  const [emailSending, setEmailSending] = useState(false);
  const [emailResult, setEmailResult] = useState(null);

  const [decisionLoading, setDecisionLoading] = useState(null); // which decision is in flight

  /* ---- health check on mount --------------------------------------------- */
  const refreshHealth = useCallback(async () => {
    try {
      const data = await apiFetch("/api/health");
      setHealth(data);
      setHealthError(null);
    } catch (err) {
      setHealthError(err.message);
    }
  }, []);

  useEffect(() => {
    refreshHealth();
  }, [refreshHealth]);

  useEffect(() => {
    document.documentElement.classList.toggle("high-contrast", highContrast);
  }, [highContrast]);

  /* ---- speech synthesis ("read aloud") ----------------------------------- */
  const speak = useCallback((text) => {
    if (!("speechSynthesis" in window) || !text) return;
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(text);
    utter.rate = 1;
    utter.onstart = () => setSpeaking(true);
    utter.onend = () => setSpeaking(false);
    utter.onerror = () => setSpeaking(false);
    window.speechSynthesis.speak(utter);
  }, []);

  const stopSpeaking = useCallback(() => {
    window.speechSynthesis?.cancel();
    setSpeaking(false);
  }, []);

  function globalStatusText() {
    if (activeTab === "setup") {
      const kb = health?.knowledge_base;
      return kb?.ready
        ? `Setup Reference Rules tab. The knowledge base holds ${kb.chunks} indexed passages from ${kb.documents.length} document${kb.documents.length === 1 ? "" : "s"}.`
        : "Setup Reference Rules tab. No reference policy has been indexed yet.";
    }
    if (!currentCase) return "Vendor Review Dashboard. No case has been loaded yet. Upload documents or load a sample preset to begin.";
    const statusLabel = CASE_STATUS_META[currentCase.status]?.label || currentCase.status_label;
    return `Case ${currentCase.case_id} for ${currentCase.vendor.company_name}. Status: ${statusLabel}. ${currentCase.ai.summary}`;
  }

  function announce(message) {
    setLiveMessage(message);
  }

  /* ---- Tab 1 handlers ------------------------------------------------------ */
  async function handleIndexReference() {
    if (!refFiles.length) return;
    setRefError(null);
    setRefIndexing(true);
    setRefStage("uploading");
    setRefProgress(0);
    const formData = new FormData();
    refFiles.forEach((f) => formData.append("files", f));
    try {
      const data = await xhrUpload("/api/upload-reference", formData, (pct) => {
        setRefProgress(pct);
        if (pct >= 100) setRefStage("indexing");
      });
      setRefResult(data);
      setRefStage("done");
      setRefFiles([]);
      announce(`Indexed ${data.total_chunks} passages into the knowledge base.`);
      refreshHealth();
    } catch (err) {
      setRefError(err.message);
      setRefStage("error");
    } finally {
      setRefIndexing(false);
    }
  }

  async function handleIndexSamplePolicies() {
    setRefError(null);
    setRefIndexing(true);
    setRefStage("indexing");
    setRefProgress(100);
    try {
      const data = await apiFetch("/api/index-sample-policies", { method: "POST" });
      setRefResult(data);
      setRefStage("done");
      announce(`Indexed ${data.total_chunks} passages from the sample reference policy.`);
      refreshHealth();
    } catch (err) {
      setRefError(err.message);
      setRefStage("error");
    } finally {
      setRefIndexing(false);
    }
  }

  async function handleClearReference() {
    setRefError(null);
    try {
      await apiFetch("/api/reference", { method: "DELETE" });
      setRefResult(null);
      setRefStage(null);
      announce("Knowledge base cleared.");
      refreshHealth();
    } catch (err) {
      setRefError(err.message);
    }
  }

  /* ---- Tab 2 handlers -------------------------------------------------------- */
  function applyCase(data) {
    setCurrentCase(data);
    setReviewerNotes(data.reviewer_notes || "");
    setEmailResult(null);
    setVendorFiles([]);
    setVendorStage("done");
    const statusLabel = CASE_STATUS_META[data.status]?.label || data.status_label;
    announce(`Case ${data.case_id} processed. Status: ${statusLabel}.`);
  }

  async function handleProcessVendor() {
    if (!vendorFiles.length) return;
    setCaseError(null);
    setProcessing(true);
    setVendorProgress(0);
    setVendorStage("uploading");
    const formData = new FormData();
    vendorFiles.forEach((f) => formData.append("files", f));
    try {
      const data = await xhrUpload("/api/process-vendor", formData, (pct) => {
        setVendorProgress(pct);
        if (pct >= 100) setVendorStage("analyzing");
      });
      applyCase(data);
    } catch (err) {
      setCaseError(err.message);
      setVendorStage("error");
    } finally {
      setProcessing(false);
    }
  }

  async function handleLoadPreset(presetName) {
    setCaseError(null);
    setProcessing(true);
    setVendorStage("analyzing");
    try {
      const data = await apiFetch("/api/process-preset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preset: presetName }),
      });
      applyCase(data);
    } catch (err) {
      setCaseError(err.message);
      setVendorStage("error");
    } finally {
      setProcessing(false);
    }
  }

  async function refreshCase() {
    if (!currentCase) return;
    try {
      const data = await apiFetch(`/api/audit/${currentCase.case_id}`);
      setCurrentCase(data);
    } catch {
      // non-fatal - keep showing the locally held case
    }
  }

  /* ---- reviewer notes + dictation ------------------------------------------- */
  async function sendForTranscription(blob) {
    setTranscribing(true);
    setNotesError(null);
    const formData = new FormData();
    formData.append("audio", blob, "dictation.webm");
    try {
      const data = await apiFetch("/api/transcribe", { method: "POST", body: formData });
      if (data.mode === "error" || data.mode === "unavailable") {
        setNotesError(data.text);
      } else {
        setReviewerNotes((prev) => (prev.trim() ? `${prev.trim()} ${data.text}` : data.text));
      }
    } catch (err) {
      setNotesError(err.message);
    } finally {
      setTranscribing(false);
    }
  }

  async function startRecording() {
    setNotesError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = window.MediaRecorder && MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      audioChunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        const blob = new Blob(audioChunksRef.current, { type: mimeType || "audio/webm" });
        await sendForTranscription(blob);
      };
      mediaRecorderRef.current = recorder;
      recorder.start();
      setRecording(true);
    } catch {
      setNotesError("Microphone access was denied or is unavailable. Type your notes instead.");
    }
  }

  function stopRecording() {
    mediaRecorderRef.current?.stop();
    setRecording(false);
  }

  async function handleSaveNotes() {
    if (!currentCase) return;
    setNotesError(null);
    try {
      await apiFetch("/api/notes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ case_id: currentCase.case_id, notes: reviewerNotes }),
      });
      setNotesSaved(true);
      setTimeout(() => setNotesSaved(false), 2000);
    } catch (err) {
      setNotesError(err.message);
    }
  }

  /* ---- email ------------------------------------------------------------------ */
  function openEmailModal() {
    const draft = currentCase?.ai?.email_draft;
    setEmailTo(currentCase?.vendor?.contact_email || "");
    setEmailSubject(draft?.subject || "");
    setEmailBody(draft?.body || "");
    setEmailResult(null);
    setEmailModalOpen(true);
  }

  async function handleSendEmail() {
    setEmailSending(true);
    setEmailResult(null);
    try {
      const data = await apiFetch("/api/send-email", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          to: emailTo,
          subject: emailSubject,
          body: emailBody,
          case_id: currentCase?.case_id,
          kind: currentCase?.status === "READY_FOR_APPROVAL" ? "approval_notice" : "correction_notice",
        }),
      });
      setEmailResult(data);
      announce(data.sent ? `Email sent to ${emailTo}.` : "Email preview rendered (no live send configured).");
      refreshCase();
    } catch (err) {
      setEmailResult({ sent: false, mode: "error", message: err.message });
    } finally {
      setEmailSending(false);
    }
  }

  /* ---- decisions ---------------------------------------------------------------- */
  async function handleDecision(decisionType) {
    if (!currentCase) return;
    setDecisionLoading(decisionType);
    setCaseError(null);
    try {
      const data = await apiFetch("/api/decision", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          case_id: currentCase.case_id,
          decision: decisionType,
          reviewer: decisionType === "exception_approved" ? "Compliance Reviewer" : "Procurement Officer",
          notes: reviewerNotes,
        }),
      });
      setCurrentCase((prev) => ({ ...prev, decision: data.decision, events: data.events }));
      announce(`Decision recorded: ${data.decision.label}.`);
    } catch (err) {
      setCaseError(err.message);
    } finally {
      setDecisionLoading(null);
    }
  }

  /* ---- audit log download --------------------------------------------------------- */
  async function handleDownloadAudit() {
    if (!currentCase) return;
    try {
      const data = await apiFetch(`/api/audit/${currentCase.case_id}`);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${currentCase.case_id}-audit-log.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setCaseError(err.message);
    }
  }

  /* ============================================================================
   * Render
   * ========================================================================== */
  const kb = health?.knowledge_base;
  const statusMeta = currentCase ? CASE_STATUS_META[currentCase.status] : null;

  // Live API status badge: red (unreachable) > amber (fallback, no gateway key) > green (ready).
  const apiStatus = healthError
    ? { label: "Backend offline", dot: t.hc ? "bg-cyan-300" : "bg-rose-500", ping: t.hc ? "bg-cyan-300" : "bg-rose-500" }
    : health && !health.gateway_configured
    ? { label: "Fallback mode", dot: t.hc ? "bg-yellow-400" : "bg-amber-500", ping: t.hc ? "bg-yellow-400" : "bg-amber-500" }
    : health
    ? { label: "System ready", dot: t.hc ? "bg-yellow-400" : "bg-emerald-500", ping: t.hc ? "bg-yellow-400" : "bg-emerald-500" }
    : { label: "Connecting…", dot: "bg-slate-400", ping: "bg-slate-400" };

  return (
    <div className={cx("min-h-screen transition-colors", t.page)}>
      <a
        href="#main-content"
        className={cx(
          "sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded focus:px-4 focus:py-2 focus:font-semibold",
          t.hc ? "focus:bg-yellow-400 focus:text-slate-950" : "focus:bg-indigo-600 focus:text-white"
        )}
      >
        Skip to main content
      </a>

      {/* ---------------------------------------------------------------- Top bar */}
      <header className={cx("sticky top-0 z-50", t.header)}>
        <div className="mx-auto flex max-w-7xl flex-col gap-3 px-4 py-3 sm:px-6 lg:grid lg:grid-cols-[auto_1fr_auto] lg:items-center lg:gap-4">
          {/* Left: logo + name */}
          <div className="flex items-center gap-3">
            <span
              className={cx(
                "relative flex h-10 w-10 shrink-0 items-center justify-center rounded-xl",
                t.hc ? "border-2 border-yellow-400 bg-slate-950" : "bg-gradient-to-br from-indigo-500 to-purple-600 shadow-glow"
              )}
              aria-hidden="true"
            >
              <Sparkles className={cx("h-5 w-5", t.hc ? "text-yellow-400" : "text-white")} />
            </span>
            <div className="min-w-0">
              <p className={cx("text-[11px] font-semibold uppercase tracking-wider", t.hc ? "text-cyan-300" : "text-indigo-600")}>
                Enterprise AI Onboarding
              </p>
              <p className={cx("truncate font-display text-base font-semibold leading-tight sm:text-lg", t.headerText)}>
                Vendor Onboarding &amp; Approval Assistant
              </p>
              <p className={cx("text-xs", t.headerMuted)}>
                {healthError
                  ? "Backend unreachable"
                  : health
                  ? `${health.gateway_configured ? "Live AI models" : "Fallback mode (no gateway key set)"} · ${
                      kb?.ready ? `${kb.chunks} policy passages indexed` : "No policy indexed yet"
                    }`
                  : "Connecting…"}
              </p>
            </div>
          </div>

          {/* Center: segmented tab navigation with sliding pill */}
          <div className="flex justify-center">
            <div
              role="tablist"
              aria-label="Sections"
              className={cx("relative inline-flex rounded-full p-1", t.hc ? "bg-slate-900 border-2 border-slate-700" : "bg-slate-100")}
            >
              <span
                aria-hidden="true"
                className={cx(
                  "tab-pill absolute inset-y-1 left-1 w-52 rounded-full",
                  t.hc ? "bg-yellow-400" : "bg-white shadow-sm",
                  activeTab === "review" && "translate-x-52"
                )}
              />
              <button
                role="tab"
                aria-selected={activeTab === "setup"}
                onClick={() => setActiveTab("setup")}
                className={cx(
                  "relative z-10 flex w-52 items-center justify-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-medium transition-colors",
                  activeTab === "setup" ? (t.hc ? "text-slate-950" : "text-indigo-700") : t.tabInactive
                )}
              >
                <FolderKanban className="h-4 w-4" aria-hidden="true" />
                Setup Reference Rules
              </button>
              <button
                role="tab"
                aria-selected={activeTab === "review"}
                onClick={() => setActiveTab("review")}
                className={cx(
                  "relative z-10 flex w-52 items-center justify-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-medium transition-colors",
                  activeTab === "review" ? (t.hc ? "text-slate-950" : "text-indigo-700") : t.tabInactive
                )}
              >
                <ClipboardList className="h-4 w-4" aria-hidden="true" />
                Vendor Review Dashboard
              </button>
            </div>
          </div>

          {/* Right: quick action toolbar */}
          <div className="flex flex-wrap items-center justify-start gap-2 lg:justify-end">
            {/* Live API status */}
            <span
              className={cx(
                "hidden items-center gap-1.5 rounded-full px-2.5 py-1.5 text-xs font-medium sm:inline-flex",
                t.hc ? "border-2 border-slate-700 text-slate-100" : "border border-slate-200 bg-white text-slate-600"
              )}
            >
              <span className="relative flex h-2 w-2">
                <span className={cx("absolute inline-flex h-full w-full animate-ping rounded-full opacity-75", apiStatus.ping)} />
                <span className={cx("relative inline-flex h-2 w-2 rounded-full", apiStatus.dot)} />
              </span>
              {apiStatus.label}
            </span>

            {/* High contrast / WCAG toggle */}
            <button
              type="button"
              onClick={() => setHighContrast((v) => !v)}
              aria-pressed={highContrast}
              className={cx(
                "flex h-9 w-9 items-center justify-center rounded-lg border transition-colors",
                t.hc ? "border-2 border-slate-100 text-slate-100 hover:bg-slate-100 hover:text-slate-950" : "border-slate-200 text-slate-500 hover:border-indigo-300 hover:text-indigo-700"
              )}
              title={highContrast ? "Switch to standard contrast" : "Switch to high contrast"}
            >
              {highContrast ? <Sun className="h-4 w-4" aria-hidden="true" /> : <Moon className="h-4 w-4" aria-hidden="true" />}
              <span className="sr-only">Toggle high contrast mode</span>
            </button>

            {/* Global read-aloud */}
            <button
              type="button"
              onClick={() => (speaking ? stopSpeaking() : speak(globalStatusText()))}
              aria-pressed={speaking}
              className={cx(
                "relative flex h-9 items-center gap-1.5 rounded-lg border px-3 text-sm font-medium transition-colors",
                speaking
                  ? t.hc
                    ? "border-2 border-yellow-400 bg-yellow-400 text-slate-950"
                    : "border-indigo-200 bg-indigo-50 text-indigo-700"
                  : t.hc
                  ? "border-2 border-slate-100 text-slate-100 hover:bg-slate-100 hover:text-slate-950"
                  : "border-slate-200 text-slate-500 hover:border-indigo-300 hover:text-indigo-700"
              )}
              title="Read the current screen status aloud"
            >
              {speaking && (
                <span
                  className={cx("absolute -left-0.5 -top-0.5 h-2 w-2 animate-ping rounded-full", t.hc ? "bg-slate-950" : "bg-indigo-500")}
                  aria-hidden="true"
                />
              )}
              <Volume2 className={cx("h-4 w-4", speaking && "animate-pulse")} aria-hidden="true" />
              {speaking ? "Stop" : "Read aloud"}
            </button>
          </div>
        </div>
      </header>

      {/* aria-live region: announces status changes to screen readers */}
      <div aria-live="polite" className="sr-only">
        {liveMessage}
      </div>

      {healthError && (
        <div className={cx("border-b px-4 py-2 text-center text-sm sm:px-6", t.hc ? "border-cyan-300 bg-slate-950 text-cyan-300" : "border-rose-200 bg-rose-50 text-rose-700")}>
          Can&rsquo;t reach the backend at <code className="font-mono">{API_BASE}</code>. Start it with{" "}
          <code className="font-mono">uvicorn main:app --reload --port 8000</code> from the <code className="font-mono">backend/</code> folder.
        </div>
      )}

      <main id="main-content" className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        {activeTab === "setup" ? (
          <SetupTab
            t={t}
            refFiles={refFiles}
            setRefFiles={setRefFiles}
            refIndexing={refIndexing}
            refProgress={refProgress}
            refStage={refStage}
            refResult={refResult}
            refError={refError}
            kb={kb}
            presets={health?.presets || []}
            onIndex={handleIndexReference}
            onIndexSample={handleIndexSamplePolicies}
            onClear={handleClearReference}
          />
        ) : (
          <ReviewTab
            t={t}
            speak={speak}
            kb={kb}
            presets={health?.presets || []}
            vendorFiles={vendorFiles}
            setVendorFiles={setVendorFiles}
            processing={processing}
            vendorProgress={vendorProgress}
            vendorStage={vendorStage}
            caseError={caseError}
            currentCase={currentCase}
            statusMeta={statusMeta}
            onProcessVendor={handleProcessVendor}
            onLoadPreset={handleLoadPreset}
            reviewerNotes={reviewerNotes}
            setReviewerNotes={setReviewerNotes}
            notesSaved={notesSaved}
            notesError={notesError}
            recording={recording}
            transcribing={transcribing}
            onStartRecording={startRecording}
            onStopRecording={stopRecording}
            onSaveNotes={handleSaveNotes}
            emailModalOpen={emailModalOpen}
            emailTo={emailTo}
            setEmailTo={setEmailTo}
            emailSubject={emailSubject}
            setEmailSubject={setEmailSubject}
            emailBody={emailBody}
            setEmailBody={setEmailBody}
            emailSending={emailSending}
            emailResult={emailResult}
            onOpenEmailModal={openEmailModal}
            onCloseEmailModal={() => setEmailModalOpen(false)}
            onSendEmail={handleSendEmail}
            decisionLoading={decisionLoading}
            onDecision={handleDecision}
            onDownloadAudit={handleDownloadAudit}
          />
        )}
      </main>

      <footer className={cx("border-t px-4 py-6 text-center text-xs sm:px-6", t.hc ? "border-slate-700 text-slate-500" : "border-slate-200 text-slate-400")}>
        Deterministic checks + grounded RAG for supplier onboarding — every recommendation cites the retrieved policy
        clause next to the extracted vendor text.
      </footer>
    </div>
  );
}

/* ============================================================================
 * Tab 1: Setup Reference Rules
 * ========================================================================== */
function SetupTab({ t, refFiles, setRefFiles, refIndexing, refProgress, refStage, refResult, refError, kb, presets, onIndex, onIndexSample, onClear }) {
  return (
    <div className="grid gap-6 lg:grid-cols-5">
      <section className={cx("rounded-xl p-6 lg:col-span-3", t.card)}>
        <h2 className={cx("flex items-center gap-2 font-display text-xl font-semibold", t.heading)}>
          <ScrollText className="h-5 w-5" aria-hidden="true" />
          Reference policy documents
        </h2>
        <p className={cx("mt-1 text-sm", t.muted)}>
          Upload the buyer&rsquo;s checklists, acceptance rules and approval matrices. Each file is chunked, embedded and
          indexed into ChromaDB so vendor reviews can retrieve grounded policy clauses.
        </p>

        <div className="mt-4">
          <Dropzone
            t={t}
            files={refFiles}
            inputId="reference-upload"
            disabled={refIndexing}
            hint="Procurement policy, onboarding checklist, or approval matrix PDFs"
            onAddFiles={(files) => setRefFiles((prev) => [...prev, ...files])}
            onRemoveFile={(i) => setRefFiles((prev) => prev.filter((_, idx) => idx !== i))}
          />
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={onIndex}
            disabled={!refFiles.length || refIndexing}
            className={cx("rounded-lg px-4 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-50", t.primaryBtn)}
          >
            {refIndexing ? (
              <span className="inline-flex items-center gap-2">
                <Spinner hc={t.hc} /> Indexing…
              </span>
            ) : (
              "Index into knowledge base"
            )}
          </button>

          {presets.length > 0 && (
            <button type="button" onClick={onIndexSample} disabled={refIndexing} className={cx("rounded-lg px-4 py-2 text-sm font-semibold disabled:opacity-50", t.secondaryBtn)}>
              Index bundled sample policy
            </button>
          )}

          {kb?.ready && (
            <button type="button" onClick={onClear} className={cx("rounded-lg px-4 py-2 text-sm font-semibold", t.secondaryBtn)}>
              Clear knowledge base
            </button>
          )}
        </div>

        {refStage && (
          <div className="mt-5" aria-live="polite">
            <ProgressSteps t={t} stage={refStage} progress={refProgress} labels={["Uploading", "Extracting & embedding", "Indexed"]} />
          </div>
        )}

        {refError && (
          <p role="alert" className={cx("mt-4 flex items-start gap-2 rounded-lg px-3 py-2 text-sm", t.hc ? "border-2 border-cyan-300 text-cyan-300" : "bg-rose-50 text-rose-700 border border-rose-200")}>
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            {refError}
          </p>
        )}

        {refResult && (
          <div className={cx("mt-5 rounded-lg p-4 text-sm", t.cardDim)}>
            <p className="font-semibold">Indexing result</p>
            <ul className="mt-2 space-y-1">
              {refResult.indexed.map((item, i) => (
                <li key={i} className="flex items-center justify-between gap-3 font-mono text-xs">
                  <span className="truncate">{item.filename}</span>
                  <span>{item.error ? "― skipped" : `${item.chunks} chunks`}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <aside className={cx("rounded-xl p-6 lg:col-span-2", t.cardDim)}>
        <h3 className={cx("flex items-center gap-2 font-display text-lg font-semibold", t.heading)}>
          <ListChecks className="h-5 w-5" aria-hidden="true" />
          Knowledge base status
        </h3>
        {kb ? (
          <>
            <dl className="mt-3 space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className={t.muted}>Ready for retrieval</dt>
                <dd className="font-semibold">{kb.ready ? "Yes" : "No"}</dd>
              </div>
              <div className="flex justify-between">
                <dt className={t.muted}>Indexed passages</dt>
                <dd className="font-mono">{kb.chunks}</dd>
              </div>
            </dl>
            {kb.documents?.length > 0 && (
              <ul className="mt-3 space-y-1 text-xs">
                {kb.documents.map((doc) => (
                  <li key={doc} className={cx("truncate rounded px-2 py-1 font-mono", t.hc ? "border border-slate-700" : "bg-white")}>
                    {doc}
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <p className={cx("mt-2 text-sm", t.muted)}>Connecting to the backend…</p>
        )}

        <div className={cx("mt-5 border-t pt-4", t.divider)}>
          <p className={cx("text-xs font-semibold uppercase tracking-wide", t.muted)}>No policy PDFs handy?</p>
          <p className={cx("mt-1 text-sm", t.muted)}>
            Run <code className="font-mono">python generate_test_pdfs.py</code> from the project root to generate a
            reference policy plus three sample vendor packages under <code className="font-mono">test_docs/</code>, then
            use &ldquo;Index bundled sample policy&rdquo; above.
          </p>
        </div>
      </aside>
    </div>
  );
}

function ProgressSteps({ t, stage, progress, labels }) {
  const stageIndex = { uploading: 0, indexing: 1, analyzing: 1, done: 2, error: -1 }[stage] ?? -1;
  return (
    <div>
      <div className="flex items-center gap-2">
        {labels.map((label, i) => {
          const active = i <= stageIndex;
          return (
            <div key={label} className="flex flex-1 items-center gap-2">
              <span
                className={cx(
                  "flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold",
                  active
                    ? t.hc
                      ? "bg-yellow-400 text-slate-950"
                      : "bg-indigo-600 text-white"
                    : t.hc
                    ? "border-2 border-slate-700 text-slate-500"
                    : "border border-slate-300 text-slate-400"
                )}
                aria-hidden="true"
              >
                {stage === "done" || i < stageIndex ? <CheckCircle2 className="h-3.5 w-3.5" /> : i + 1}
              </span>
              <span className={cx("text-xs", active ? (t.hc ? "text-white" : "text-slate-900") : t.muted)}>{label}</span>
              {i < labels.length - 1 && <span className={cx("h-px flex-1", t.hc ? "bg-slate-700" : "bg-slate-200")} aria-hidden="true" />}
            </div>
          );
        })}
      </div>
      {stage === "uploading" && (
        <div className={cx("mt-2 h-1.5 w-full overflow-hidden rounded-full", t.hc ? "bg-slate-800" : "bg-slate-200")}>
          <div className={cx("h-full transition-all", t.hc ? "bg-yellow-400" : "bg-indigo-600")} style={{ width: `${progress}%` }} />
        </div>
      )}
    </div>
  );
}

/* ============================================================================
 * Tab 2: Vendor Review Dashboard (3 panels)
 * ========================================================================== */
function ReviewTab(props) {
  const { t, currentCase, caseError } = props;

  return (
    <div>
      {!currentCase && !props.processing && (
        <p className={cx("mb-4 text-sm", t.muted)}>Upload a vendor package or load a sample preset to start a review.</p>
      )}
      {caseError && (
        <p role="alert" className={cx("mb-4 flex items-start gap-2 rounded-lg px-3 py-2 text-sm", t.hc ? "border-2 border-cyan-300 text-cyan-300" : "bg-rose-50 text-rose-700 border border-rose-200")}>
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          {caseError}
        </p>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <IntakePanel {...props} />
        <InspectorPanel {...props} />
        <DecisionPanel {...props} />
      </div>

      {props.emailModalOpen && <EmailModal {...props} />}
    </div>
  );
}

/* ---- Panel 1: Intake & Files ------------------------------------------------ */
function IntakePanel({ t, vendorFiles, setVendorFiles, processing, vendorProgress, vendorStage, onProcessVendor, presets, onLoadPreset, currentCase, kb }) {
  return (
    <section className={cx("rounded-xl p-6", t.card)} aria-labelledby="panel1-heading">
      <h2 id="panel1-heading" className={cx("font-display text-lg font-semibold", t.heading)}>
        1. Intake &amp; Files
      </h2>

      {!kb?.ready && (
        <p className={cx("mt-2 flex items-start gap-2 rounded-lg px-3 py-2 text-xs", t.hc ? "border-2 border-slate-100 text-slate-100" : "bg-amber-50 text-amber-700 border border-amber-200")}>
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          No reference policy is indexed yet, so citations will fall back to deterministic checks only. Index one in the
          Setup tab for grounded policy retrieval.
        </p>
      )}

      <div className="mt-4">
        <Dropzone
          t={t}
          files={vendorFiles}
          inputId="vendor-upload"
          disabled={processing}
          hint="Registration cert, tax doc, bank proof, insurance cert, compliance declaration"
          onAddFiles={(files) => setVendorFiles((prev) => [...prev, ...files])}
          onRemoveFile={(i) => setVendorFiles((prev) => prev.filter((_, idx) => idx !== i))}
        />
      </div>

      <button
        type="button"
        onClick={onProcessVendor}
        disabled={!vendorFiles.length || processing}
        className={cx("mt-4 w-full rounded-lg px-4 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-50", t.primaryBtn)}
      >
        {processing ? (
          <span className="inline-flex items-center gap-2">
            <Spinner hc={t.hc} /> Processing…
          </span>
        ) : (
          "Process documents"
        )}
      </button>

      {vendorStage && (
        <div className="mt-4">
          <ProgressSteps t={t} stage={vendorStage} progress={vendorProgress} labels={["Uploading", "Extract, verify & retrieve", "Ready"]} />
        </div>
      )}

      {presets.length > 0 && (
        <div className={cx("mt-6 border-t pt-4", t.divider)}>
          <p className={cx("flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide", t.muted)}>
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
            Load sample test cases
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {presets.map((preset) => (
              <button
                key={preset.name}
                type="button"
                onClick={() => onLoadPreset(preset.name)}
                disabled={processing}
                title={preset.description}
                aria-label={`Load sample: ${preset.label}. ${preset.description}`}
                className={cx(
                  "rounded-full px-3.5 py-1.5 text-xs font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                  t.chip,
                  t.hc ? "hover:bg-slate-100 hover:text-slate-950" : "hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-700"
                )}
              >
                {preset.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {currentCase && (
        <div className={cx("mt-6 border-t pt-4", t.divider)}>
          <p className={cx("flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide", t.muted)}>
            <ListChecks className="h-3.5 w-3.5" aria-hidden="true" />
            Document checklist
          </p>
          <ul className="mt-2 space-y-2">
            {currentCase.checklist.map((row) => (
              <li key={row.doc_type + (row.filename || "")} className={cx("rounded-lg p-3", t.cardDim)}>
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-semibold">{row.label}</p>
                    <p className={cx("text-xs", t.muted)}>{row.filename || "Not submitted"}</p>
                  </div>
                  <StatusBadge t={t} kind={BADGE_TO_KIND[row.badge] || "gap"} label={row.state} />
                </div>
                <p className={cx("mt-1.5 text-xs", t.muted)}>{row.detail}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

/* ---- Panel 2: Grounded RAG Inspector ---------------------------------------- */
function InspectorPanel({ t, currentCase }) {
  return (
    <section className={cx("rounded-xl p-6", t.card)} aria-labelledby="panel2-heading">
      <h2 id="panel2-heading" className={cx("flex items-center gap-2 font-display text-lg font-semibold", t.heading)}>
        <GitCompare className="h-5 w-5" aria-hidden="true" />
        2. Grounded RAG Inspector
      </h2>

      {!currentCase ? (
        <p className={cx("mt-3 text-sm", t.muted)}>Extracted fields and side-by-side citations will appear here after a case is processed.</p>
      ) : (
        <>
          <div className={cx("mt-4 rounded-lg p-4", t.cardDim)}>
            <p className={cx("flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider", t.muted)}>
              <FileText className="h-3.5 w-3.5" aria-hidden="true" />
              Extracted key-values
            </p>
            <dl className="mt-2 grid grid-cols-1 gap-x-4 gap-y-1.5 sm:grid-cols-2">
              {Object.entries(currentCase.extracted).map(([key, value]) => (
                <div key={key} className="flex justify-between gap-2 text-xs">
                  <dt className={t.muted}>{prettifyKey(key)}</dt>
                  <dd className="truncate text-right font-mono">{prettifyValue(key, value)}</dd>
                </div>
              ))}
            </dl>
            <p className={cx("mt-2 text-[11px]", t.muted)}>
              Extraction mode: <span className="font-mono">{currentCase.ai.extraction_mode}</span>
            </p>
          </div>

          <div className="mt-5">
            <p className={cx("text-xs font-semibold uppercase tracking-wider", t.muted)}>Policy vs. vendor evidence</p>
            {currentCase.citations.length === 0 ? (
              <p className={cx("mt-2 text-sm", t.muted)}>No citations were retrieved — index a reference policy for grounded comparisons.</p>
            ) : (
              <ul className="mt-2 space-y-3">
                {currentCase.citations.map((c, i) => {
                  const matchLabel =
                    c.verdict === "pass" ? "Policy match confirmed" : c.verdict === "fail" ? "Policy coverage gap detected" : "Partial match — review needed";
                  return (
                    <li key={i} className={cx("rounded-lg p-3", t.cardDim)}>
                      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                        <span className={cx("font-mono text-[11px]", t.muted)}>
                          {c.policy_source} · {c.policy_clause}
                        </span>
                        <StatusBadge t={t} kind={c.verdict === "pass" ? "pass" : c.verdict === "fail" ? "fail" : "gap"} label={matchLabel} />
                      </div>
                      <div className="grid gap-2 sm:grid-cols-2">
                        <div
                          className={cx(
                            "rounded-r-lg p-3",
                            t.hc ? "border-l-4 border-slate-100 bg-slate-950" : "border-l-4 border-indigo-500 bg-indigo-50/40"
                          )}
                        >
                          <p className={cx("text-[10px] font-semibold uppercase tracking-wide", t.muted)}>Retrieved policy clause</p>
                          <p className="mt-1 text-xs leading-snug">{c.policy_text}</p>
                        </div>
                        <div className={cx("rounded-lg p-3", t.hc ? "border border-slate-700" : "border border-slate-200 bg-white")}>
                          <p className={cx("text-[10px] font-semibold uppercase tracking-wide", t.muted)}>Extracted vendor text ({c.vendor_document})</p>
                          <p className="mt-1 text-xs leading-snug">{c.vendor_text}</p>
                        </div>
                      </div>
                      {c.rationale && <p className={cx("mt-2 text-xs italic", t.muted)}>{c.rationale}</p>}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </>
      )}
    </section>
  );
}

/* ---- Panel 3: Decision & Action Hub ----------------------------------------- */
function DecisionPanel({
  t,
  speak,
  currentCase,
  statusMeta,
  reviewerNotes,
  setReviewerNotes,
  notesSaved,
  notesError,
  recording,
  transcribing,
  onStartRecording,
  onStopRecording,
  onSaveNotes,
  onOpenEmailModal,
  decisionLoading,
  onDecision,
  onDownloadAudit,
}) {
  return (
    <section className={cx("rounded-xl p-6", t.card)} aria-labelledby="panel3-heading">
      <h2 id="panel3-heading" className={cx("font-display text-lg font-semibold", t.heading)}>
        3. Decision &amp; Action Hub
      </h2>

      {!currentCase ? (
        <p className={cx("mt-3 text-sm", t.muted)}>The AI validation summary, reviewer notes and decision actions will appear here once a case is loaded.</p>
      ) : (
        <>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <StatusBadge t={t} kind={statusMeta?.kind || "gap"} label={statusMeta?.label} />
            <span className={cx("font-mono text-xs", t.muted)}>{currentCase.case_id}</span>
            {currentCase.decision && (
              <div className="ml-auto">
                <StampMark t={t} outcome={currentCase.decision.outcome} />
              </div>
            )}
          </div>

          <div
            className={cx(
              "mt-4 rounded-lg p-4",
              t.hc ? "border-2 border-slate-700 bg-slate-900" : "border border-indigo-100 bg-gradient-to-br from-indigo-50 to-purple-50"
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <p className={cx("flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide", t.hc ? "text-slate-100" : "text-indigo-700")}>
                <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
                AI validation summary
              </p>
              <button
                type="button"
                onClick={() => speak(currentCase.ai.summary)}
                className={cx("flex shrink-0 items-center gap-1 text-xs font-semibold underline", t.hc ? "text-yellow-300" : "text-indigo-700")}
              >
                <Volume2 className="h-3.5 w-3.5" aria-hidden="true" />
                Listen to summary
              </button>
            </div>
            <p className="mt-2 text-sm leading-relaxed">{currentCase.ai.summary}</p>
            <div className="mt-2 flex flex-wrap gap-3 text-[11px]">
              <span className={t.muted}>
                Recommendation: <span className="font-semibold">{currentCase.ai.recommendation}</span>
              </span>
              <span className={t.muted}>
                Risk level: <span className="font-semibold">{currentCase.ai.risk_level}</span>
              </span>
            </div>
            {currentCase.ai.outstanding_actions?.length > 0 && (
              <ul className="mt-3 list-disc space-y-1 pl-4 text-xs">
                {currentCase.ai.outstanding_actions.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
            )}
          </div>

          <div className="mt-5">
            <div className="flex items-center justify-between">
              <label htmlFor="reviewer-notes" className={cx("text-xs font-semibold uppercase tracking-wide", t.muted)}>
                Reviewer notes
              </label>
              <button
                type="button"
                onClick={recording ? onStopRecording : onStartRecording}
                aria-pressed={recording}
                disabled={transcribing}
                className={cx(
                  "relative flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-60",
                  recording ? (t.hc ? "bg-yellow-400 text-slate-950" : "bg-rose-600 text-white") : t.secondaryBtn
                )}
                title="Dictate notes with your microphone"
              >
                {recording && (
                  <span className={cx("absolute -left-1 -top-1 h-2.5 w-2.5 animate-ping rounded-full", t.hc ? "bg-slate-950" : "bg-rose-400")} aria-hidden="true" />
                )}
                <Mic className="h-3.5 w-3.5" aria-hidden="true" />
                {recording ? "Stop" : transcribing ? "Transcribing…" : "Dictate"}
              </button>
            </div>
            {(recording || transcribing) && (
              <p className={cx("mt-1 text-xs", t.hc ? "text-cyan-300" : "text-indigo-600")}>
                {recording ? "Listening via Whisper…" : "Transcribing audio…"}
              </p>
            )}
            <textarea
              id="reviewer-notes"
              rows={4}
              value={reviewerNotes}
              onChange={(e) => setReviewerNotes(e.target.value)}
              onBlur={onSaveNotes}
              placeholder="Record a compliance rationale or resubmission note…"
              className={cx("mt-2 w-full rounded-lg px-3 py-2 text-sm", t.input)}
            />
            <div className="mt-1 flex items-center justify-between">
              {notesError ? (
                <p role="alert" className={cx("text-xs", t.hc ? "text-cyan-300" : "text-rose-600")}>
                  {notesError}
                </p>
              ) : (
                <span className={cx("text-xs", t.muted)}>{notesSaved ? "Saved." : "Saved automatically when you click away."}</span>
              )}
              <button type="button" onClick={onSaveNotes} className={cx("text-xs font-semibold underline", t.hc ? "text-yellow-300" : "text-indigo-700")}>
                Save now
              </button>
            </div>
          </div>

          <div className="mt-5 grid grid-cols-1 gap-2">
            <button type="button" onClick={onOpenEmailModal} className={cx("flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold", t.secondaryBtn)}>
              <Mail className="h-4 w-4" aria-hidden="true" />
              {currentCase.status === "READY_FOR_APPROVAL" ? "Send approval notice via Resend" : "Send correction notice via Resend"}
            </button>

            {currentCase.status === "READY_FOR_APPROVAL" && (
              <button
                type="button"
                onClick={() => onDecision("approved")}
                disabled={decisionLoading !== null}
                className={cx("flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-50", t.primaryBtn)}
              >
                {decisionLoading === "approved" ? (
                  <>
                    <Spinner hc={t.hc} /> Approving…
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                    Approve vendor
                  </>
                )}
              </button>
            )}

            {currentCase.status === "RISK_EXCEPTION" && (
              <button
                type="button"
                onClick={() => onDecision("exception_approved")}
                disabled={decisionLoading !== null}
                className={cx("flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-50", t.primaryBtn)}
              >
                {decisionLoading === "exception_approved" ? (
                  <>
                    <Spinner hc={t.hc} /> Recording…
                  </>
                ) : (
                  <>
                    <ShieldAlert className="h-4 w-4" aria-hidden="true" />
                    Approve with documented exception
                  </>
                )}
              </button>
            )}

            <button
              type="button"
              onClick={() => onDecision("resubmission_requested")}
              disabled={decisionLoading !== null}
              className={cx("flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-50", t.dangerBtn)}
            >
              {decisionLoading === "resubmission_requested" ? (
                <>
                  <Spinner hc={t.hc} /> Recording…
                </>
              ) : (
                <>
                  <Undo2 className="h-4 w-4" aria-hidden="true" />
                  Request resubmission
                </>
              )}
            </button>

            <button type="button" onClick={onDownloadAudit} className={cx("flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold", t.secondaryBtn)}>
              <Download className="h-4 w-4" aria-hidden="true" />
              Download audit log (JSON)
            </button>
          </div>

          {currentCase.events?.length > 0 && (
            <div className={cx("mt-5 border-t pt-4", t.divider)}>
              <p className={cx("text-xs font-semibold uppercase tracking-wide", t.muted)}>Case timeline</p>
              <ol className="mt-2 space-y-2">
                {currentCase.events.map((ev, i) => (
                  <li key={i} className="text-xs">
                    <span className={cx("font-mono", t.muted)}>{ev.at}</span>
                    <span className="ml-2 font-semibold">{ev.actor}</span>
                    <p className={t.muted}>{ev.detail}</p>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </>
      )}
    </section>
  );
}

/* ============================================================================
 * Email modal
 * ========================================================================== */
function EmailModal({ t, emailTo, setEmailTo, emailSubject, setEmailSubject, emailBody, setEmailBody, emailSending, emailResult, onCloseEmailModal, onSendEmail }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 p-4" role="dialog" aria-modal="true" aria-labelledby="email-modal-heading">
      <div className={cx("w-full max-w-lg rounded-xl p-6", t.hc ? "bg-slate-950 border-2 border-slate-100 text-slate-100" : "bg-white text-slate-900 shadow-glow-lg")}>
        <div className="flex items-start justify-between">
          <h3 id="email-modal-heading" className={cx("flex items-center gap-2 font-display text-lg font-semibold", t.heading)}>
            <Mail className="h-5 w-5" aria-hidden="true" />
            Review &amp; send notice
          </h3>
          <button
            type="button"
            onClick={onCloseEmailModal}
            aria-label="Close"
            className={cx("rounded-md p-1", t.hc ? "text-slate-100 hover:bg-slate-100 hover:text-slate-950" : "text-slate-400 hover:bg-slate-100 hover:text-slate-700")}
          >
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        </div>

        <div className="mt-4 space-y-3">
          <div>
            <label htmlFor="email-to" className={cx("text-xs font-semibold uppercase tracking-wide", t.muted)}>
              To
            </label>
            <input
              id="email-to"
              type="email"
              value={emailTo}
              onChange={(e) => setEmailTo(e.target.value)}
              className={cx("mt-1 w-full rounded-lg px-3 py-2 text-sm", t.input)}
            />
          </div>
          <div>
            <label htmlFor="email-subject" className={cx("text-xs font-semibold uppercase tracking-wide", t.muted)}>
              Subject
            </label>
            <input
              id="email-subject"
              type="text"
              value={emailSubject}
              onChange={(e) => setEmailSubject(e.target.value)}
              className={cx("mt-1 w-full rounded-lg px-3 py-2 text-sm", t.input)}
            />
          </div>
          <div>
            <label htmlFor="email-body" className={cx("text-xs font-semibold uppercase tracking-wide", t.muted)}>
              Message
            </label>
            <textarea
              id="email-body"
              rows={8}
              value={emailBody}
              onChange={(e) => setEmailBody(e.target.value)}
              className={cx("mt-1 w-full rounded-lg px-3 py-2 text-sm", t.input)}
            />
          </div>
        </div>

        {emailResult && (
          <p
            className={cx(
              "mt-3 rounded-lg px-3 py-2 text-xs",
              emailResult.sent ? (t.hc ? "border-2 border-yellow-400 text-yellow-300" : "bg-emerald-50 text-emerald-700 border border-emerald-200") : t.hc ? "border-2 border-slate-700" : "bg-amber-50 text-amber-700 border border-amber-200"
            )}
          >
            {emailResult.sent ? `Sent via Resend (id ${emailResult.id}).` : emailResult.message || "Rendered as a preview — no RESEND_API_KEY configured."}
          </p>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onCloseEmailModal} className={cx("rounded-lg px-4 py-2 text-sm font-semibold", t.secondaryBtn)}>
            Close
          </button>
          <button
            type="button"
            onClick={onSendEmail}
            disabled={emailSending || !emailTo}
            className={cx("flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-50", t.primaryBtn)}
          >
            {emailSending ? (
              <>
                <Spinner hc={t.hc} /> Sending…
              </>
            ) : (
              <>
                <Send className="h-4 w-4" aria-hidden="true" />
                Send notice
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
