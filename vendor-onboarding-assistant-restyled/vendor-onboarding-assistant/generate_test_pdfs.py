"""
generate_test_pdfs.py
======================
Builds a folder of synthetic PDF documents used to demo and test the AI
Vendor Onboarding & Approval Assistant end to end, without needing to
source real (and sensitive) vendor paperwork.

Output layout (matches what backend/main.py expects under TEST_DOCS_DIR):

    test_docs/
      Reference_Policy/
        Procurement_Policy.pdf
        Onboarding_Checklist.pdf
      Vendor_Happy_Path/        -> all documents valid, USD 1.5M cover
      Vendor_Expired_Tax/       -> tax certificate expired, bank proof missing
      Vendor_Low_Insurance/     -> USD 500k cover against a USD 1M minimum

Run:
    python generate_test_pdfs.py

Then start the backend and either drag the Reference_Policy PDFs into the
"Setup Reference Rules" tab, or call POST /api/index-sample-policies, and
use the "Load Sample Preset" buttons in the Vendor Review Dashboard.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

# --------------------------------------------------------------------------- #
# Paths & shared styles
# --------------------------------------------------------------------------- #
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_docs")
TODAY = date.today()

_styles = getSampleStyleSheet()
TITLE = ParagraphStyle(
    "DocTitle", parent=_styles["Title"], fontSize=17, leading=21,
    spaceAfter=4, textColor=colors.HexColor("#16233B"),
)
SUBTITLE = ParagraphStyle(
    "DocSubtitle", parent=_styles["Normal"], fontSize=10,
    textColor=colors.HexColor("#6B6B6B"), spaceAfter=14,
)
H2 = ParagraphStyle(
    "H2", parent=_styles["Heading2"], fontSize=12.5, leading=15,
    spaceBefore=14, spaceAfter=6, textColor=colors.HexColor("#16233B"),
)
BODY = ParagraphStyle("Body", parent=_styles["Normal"], fontSize=10.5, leading=15.5, spaceAfter=6)
FIELD = ParagraphStyle("Field", parent=_styles["Normal"], fontSize=10.5, leading=16, spaceAfter=4)
FOOTNOTE = ParagraphStyle(
    "Footnote", parent=_styles["Normal"], fontSize=8.3,
    textColor=colors.HexColor("#8A8A8A"), spaceBefore=18,
)

LINE = colors.HexColor("#D9D3C1")


def _fmt(d: date) -> str:
    """Human-readable date, deliberately not ISO so the extractor's date
    parsing (dateutil, fuzzy=True) is exercised the same way it would be
    against a real scanned certificate."""
    return d.strftime("%B %d, %Y")


def build_pdf(path: str, title: str, subtitle: str, blocks: list[tuple[str, object]]) -> None:
    """blocks: list of (kind, content) where kind in {"h2", "field", "body", "hr"}."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc = SimpleDocTemplate(
        path, pagesize=LETTER,
        topMargin=0.9 * inch, bottomMargin=0.9 * inch,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
        title=title,
    )
    story = [
        Paragraph(title, TITLE),
        Paragraph(subtitle, SUBTITLE),
        HRFlowable(width="100%", color=LINE, thickness=1),
        Spacer(1, 10),
    ]
    for kind, content in blocks:
        if kind == "h2":
            story.append(Paragraph(content, H2))
        elif kind == "field":
            label, value = content
            story.append(Paragraph(f"<b>{label}:</b> {value}", FIELD))
        elif kind == "body":
            story.append(Paragraph(content, BODY))
        elif kind == "hr":
            story.append(Spacer(1, 4))
            story.append(HRFlowable(width="100%", color=LINE, thickness=0.6))
            story.append(Spacer(1, 4))
    story.append(Paragraph("Synthetic test document generated for demo purposes only.", FOOTNOTE))
    doc.build(story)
    print(f"  \u2713 {os.path.relpath(path, os.path.dirname(OUT_DIR))}")


# --------------------------------------------------------------------------- #
# Reference policy documents
# --------------------------------------------------------------------------- #
def procurement_policy() -> None:
    build_pdf(
        os.path.join(OUT_DIR, "Reference_Policy", "Procurement_Policy.pdf"),
        "Supplier Procurement &amp; Onboarding Policy",
        "Policy owner: Procurement &amp; Compliance | Effective for all new supplier onboarding",
        [
            ("h2", "Clause 1 \u2014 Mandatory Onboarding Documents"),
            ("body", "Every prospective supplier must submit five mandatory documents before a "
                      "purchase order can be issued: a Business Registration Certificate, a Tax "
                      "Registration Certificate, a Bank Account Proof, a Certificate of Insurance, "
                      "and a signed Compliance Declaration. A supplier package is incomplete until "
                      "all five documents have been received."),
            ("h2", "Clause 2 \u2014 Tax Registration Validity"),
            ("body", "The Tax Registration Certificate submitted by a supplier must be valid and "
                      "unexpired as of the date of review. An expired tax certificate is a blocking "
                      "deficiency and the package must be returned to the supplier for resubmission "
                      "before any further review takes place."),
            ("h2", "Clause 3 \u2014 Minimum Insurance Coverage"),
            ("body", "Clause 3.1: Every supplier must carry General Liability insurance with a "
                      "minimum coverage limit of USD 1,000,000 per occurrence. Clause 3.2: a "
                      "certificate of insurance showing coverage below this threshold is a policy "
                      "deviation, not an automatic rejection, and must be routed to a Compliance "
                      "Reviewer for a documented exception decision rather than returned to the "
                      "supplier automatically."),
            ("h2", "Clause 4 \u2014 Insurance Currency"),
            ("body", "The insurance policy evidenced by the Certificate of Insurance must remain in "
                      "force for at least 30 days beyond the date of review. A policy expiring "
                      "within that window should be flagged so a renewal certificate can be "
                      "requested before contract start."),
            ("h2", "Clause 5 \u2014 Legal Entity Name Consistency"),
            ("body", "The legal entity name must appear identically across the Business "
                      "Registration Certificate, Tax Registration Certificate, Bank Account Proof "
                      "and Certificate of Insurance. Any mismatch in the stated legal entity name "
                      "across submitted documents is a policy deviation requiring compliance "
                      "review before approval."),
            ("h2", "Clause 6 \u2014 Document Legibility"),
            ("body", "Any submitted document from which no machine-readable text can be extracted, "
                      "or which is otherwise illegible, is treated as not submitted. An illegible "
                      "scanned document must be rejected and a clearer copy requested from the "
                      "supplier."),
            ("h2", "Clause 7 \u2014 Tax Identification Legibility"),
            ("body", "The tax identification number printed on the Tax Registration Certificate "
                      "must be clearly legible. A certificate from which no tax identification "
                      "number can be read is a policy deviation and should be sent back for a "
                      "clearer resubmission."),
            ("h2", "AR-1 \u2014 Standard Approval"),
            ("body", "AR-1: where all deterministic checks pass and no policy deviation is found, "
                      "the Procurement Officer may approve the supplier directly. No further "
                      "sign-off is required."),
            ("h2", "AR-2 \u2014 Compliance Exception Approval"),
            ("body", "AR-2: where a policy deviation is found \u2014 such as insurance coverage "
                      "below the USD 1,000,000 minimum, or an entity name mismatch \u2014 the case "
                      "must be routed to a Compliance Reviewer, who records a written exception "
                      "rationale before final sign-off. The Compliance Reviewer's decision is final "
                      "for that exception."),
            ("h2", "AR-3 \u2014 Resubmission"),
            ("body", "AR-3: where a mandatory document is missing, expired, or illegible, the "
                      "Procurement Officer issues an automated correction notice to the supplier "
                      "and holds the case open pending resubmission."),
        ],
    )


def onboarding_checklist() -> None:
    build_pdf(
        os.path.join(OUT_DIR, "Reference_Policy", "Onboarding_Checklist.pdf"),
        "Supplier Onboarding Checklist",
        "Section 2 \u2014 required documents for a complete supplier package",
        [
            ("h2", "Section 2.1 \u2014 Required Documents"),
            ("body", "Business Registration Certificate requirement for supplier onboarding: a "
                      "current certificate of business registration issued by the registrar of "
                      "companies."),
            ("body", "Tax Registration Certificate requirement for supplier onboarding: a valid, "
                      "unexpired tax registration certificate showing a legible tax identification "
                      "number."),
            ("body", "Bank Account Proof requirement for supplier onboarding: a bank letter or "
                      "void cheque confirming the account holder name and account number."),
            ("body", "Certificate of Insurance requirement for supplier onboarding: evidence of "
                      "general liability coverage meeting the minimum coverage limit set out in "
                      "the Procurement Policy."),
            ("body", "Compliance Declaration requirement for supplier onboarding: a signed "
                      "declaration confirming anti-bribery, code of conduct and sanctions "
                      "compliance."),
            ("h2", "Section 2.2 \u2014 Review Notes"),
            ("body", "Reviewers should confirm the legal entity name is identical across every "
                      "document in the package before referring a case for approval. Any document "
                      "that cannot be read, including illegible scanned document uploads, should be "
                      "treated as not submitted and sent back to the supplier."),
        ],
    )


# --------------------------------------------------------------------------- #
# Vendor document builders (one function per document type)
# --------------------------------------------------------------------------- #
def registration_certificate(folder, company, reg_number, signatory, sign_date):
    build_pdf(
        os.path.join(folder, "Registration_Certificate.pdf"),
        "Certificate of Business Registration",
        "Issued by the Registrar of Companies",
        [
            ("h2", "Entity Details"),
            ("field", ("Legal Entity Name", company)),
            ("field", ("Registration Number", reg_number)),
            ("field", ("Registration Type", "Private Limited Company")),
            ("field", ("Certificate Status", "Active")),
            ("hr", None),
            ("h2", "Certification"),
            ("body", "This certifies that the above entity is duly incorporated and registered "
                      "in good standing as of the date of issue."),
            ("field", ("Authorised Signatory", signatory)),
            ("field", ("Date of Signature", _fmt(sign_date))),
        ],
    )


def tax_certificate(folder, company, tin, expiry_date, status_label):
    build_pdf(
        os.path.join(folder, "Tax_Registration_Certificate.pdf"),
        "Tax Registration Certificate",
        "Issued by the National Revenue Authority",
        [
            ("h2", "Taxpayer Details"),
            ("field", ("Legal Entity Name", company)),
            ("field", ("Tax Identification Number", tin)),
            ("field", ("Certificate Status", status_label)),
            ("hr", None),
            ("h2", "Validity"),
            ("body", "This tax registration certificate confirms the taxpayer is registered under "
                      "the applicable VAT and corporate tax regime."),
            ("field", ("Valid Through", _fmt(expiry_date))),
        ],
    )


def bank_proof(folder, company, account_number, ifsc, sign_date):
    build_pdf(
        os.path.join(folder, "Bank_Account_Proof.pdf"),
        "Bank Account Proof",
        "Confirmation letter issued by the account-holding bank",
        [
            ("h2", "Account Details"),
            ("field", ("Account Holder Name", company)),
            ("field", ("Account Number", account_number)),
            ("field", ("IFSC / SWIFT Code", ifsc)),
            ("field", ("Account Type", "Corporate Current Account")),
            ("hr", None),
            ("body", "This letter confirms that the named entity maintains an active bank account "
                      "in good standing as of the date below."),
            ("field", ("Date of Signature", _fmt(sign_date))),
        ],
    )


def insurance_certificate(folder, company, policy_number, coverage_amount, expiry_date):
    build_pdf(
        os.path.join(folder, "Certificate_of_Insurance.pdf"),
        "Certificate of Insurance",
        "General Liability \u2014 Evidence of Coverage",
        [
            ("h2", "Policy Details"),
            ("field", ("Named Insured", company)),
            ("field", ("Policy Number", policy_number)),
            ("field", ("Coverage Limit (General Liability, Per Occurrence)",
                       f"USD {coverage_amount:,.0f}")),
            ("field", ("Policy Expiry Date", _fmt(expiry_date))),
            ("hr", None),
            ("body", "This certificate is evidence of insurance coverage carried by the named "
                      "insured as of the date of issue and does not amend, extend or alter the "
                      "coverage afforded by the policy listed above."),
        ],
    )


def compliance_declaration(folder, company, signatory, sign_date, contact_email):
    build_pdf(
        os.path.join(folder, "Compliance_Declaration.pdf"),
        "Compliance Declaration",
        "Anti-Bribery, Code of Conduct and Sanctions Attestation",
        [
            ("h2", "Declaration"),
            ("body", f"{company} declares that it complies with all applicable anti-bribery and "
                      "anti-corruption laws, has adopted a code of conduct for its employees and "
                      "subcontractors, and confirms it is not listed on any applicable sanctions "
                      "list."),
            ("hr", None),
            ("field", ("Authorised Signatory", signatory)),
            ("field", ("Date of Signature", _fmt(sign_date))),
            ("field", ("Contact Email", contact_email)),
        ],
    )


# --------------------------------------------------------------------------- #
# Vendor packages (the three demo paths)
# --------------------------------------------------------------------------- #
def vendor_happy_path() -> None:
    folder = os.path.join(OUT_DIR, "Vendor_Happy_Path")
    company = "Meridian Facilities Services LLC"
    sign_date = TODAY - timedelta(days=10)
    registration_certificate(folder, company, "REG-48213-MF", "Dana Whitfield, Director", sign_date)
    tax_certificate(folder, company, "TIN-9924-7710", TODAY + timedelta(days=420), "Active")
    bank_proof(folder, company, "AC-771029384", "MFSB0001123", sign_date)
    insurance_certificate(folder, company, "GL-2201938", 1_500_000, TODAY + timedelta(days=270))
    compliance_declaration(folder, company, "Dana Whitfield, Director", sign_date,
                            "compliance@meridianfacilities.com")


def vendor_expired_tax() -> None:
    folder = os.path.join(OUT_DIR, "Vendor_Expired_Tax")
    company = "Northbridge Logistics Pvt Ltd"
    sign_date = TODAY - timedelta(days=14)
    registration_certificate(folder, company, "REG-30982-NL", "Priya Raman, Company Secretary", sign_date)
    tax_certificate(folder, company, "TIN-5561-2290", TODAY - timedelta(days=61), "Expired")
    # Bank Account Proof intentionally omitted -> also exercises the
    # missing-document completeness check alongside the expired tax cert.
    insurance_certificate(folder, company, "GL-3390021", 1_200_000, TODAY + timedelta(days=300))
    compliance_declaration(folder, company, "Priya Raman, Company Secretary", sign_date,
                            "accounts@northbridgelogistics.com")


def vendor_low_insurance() -> None:
    folder = os.path.join(OUT_DIR, "Vendor_Low_Insurance")
    company = "Solace Industrial Supply Co"
    sign_date = TODAY - timedelta(days=7)
    registration_certificate(folder, company, "REG-77341-SI", "Marcus Ade, Managing Partner", sign_date)
    tax_certificate(folder, company, "TIN-1187-6634", TODAY + timedelta(days=365), "Active")
    bank_proof(folder, company, "AC-556602214", "SISC0007788", sign_date)
    insurance_certificate(folder, company, "GL-6603312", 500_000, TODAY + timedelta(days=240))
    compliance_declaration(folder, company, "Marcus Ade, Managing Partner", sign_date,
                            "info@solaceindustrial.com")


def main() -> None:
    print("Generating synthetic test PDFs...\n")
    print("Reference_Policy/ (index these in the Setup tab first):")
    procurement_policy()
    onboarding_checklist()

    print("\nVendor_Happy_Path/  (all documents valid, USD 1.5M cover -> Ready for approval):")
    vendor_happy_path()

    print("\nVendor_Expired_Tax/  (tax certificate expired, bank proof missing -> Resubmission):")
    vendor_expired_tax()

    print("\nVendor_Low_Insurance/  (USD 500k cover vs USD 1M minimum -> Risk / exception):")
    vendor_low_insurance()

    print(f"\nDone. Files written under: {OUT_DIR}")


if __name__ == "__main__":
    main()
