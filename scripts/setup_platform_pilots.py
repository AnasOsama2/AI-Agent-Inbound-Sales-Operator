"""Explicit local demo setup through real Salma APIs; never activates live channels."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from pypdf import PdfReader
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from salma.auth import hash_password  # noqa: E402
from salma.db import session  # noqa: E402
from salma.platform_models import User  # noqa: E402

ACCESS = ROOT / "data" / "local-access.json"
PDF = Path("C:/Users/user/Downloads/Bright_Nile_Dental_Clinic_Knowledge_Pack.pdf")


def save_access(value):
    ACCESS.parent.mkdir(parents=True, exist_ok=True)
    temporary = ACCESS.with_suffix(".json.pending")
    with open(temporary, "w", encoding="utf-8") as target:
        json.dump(value, target, ensure_ascii=False, indent=2)
    temporary.replace(ACCESS)


def price(identifier, name, name_ar, amount, duration="", conditions="", kind="fixed", unit=""):
    return {
        "id": identifier,
        "name": name,
        "name_ar": name_ar,
        "currency": "EGP",
        "price_type": kind,
        "amount": amount,
        "duration": duration,
        "conditions": conditions,
        "unit": unit,
        "offers": [],
    }


def clinic_catalog():
    rows = [
        price(
            "S01",
            "Consultation and exam",
            "كشف وفحص الأسنان",
            300,
            "20 minutes",
            "Deducted from treatment cost when treatment starts within 14 days.",
        ),
        price(
            "S02",
            "Teeth cleaning (scaling and polishing)",
            "تنظيف وتلميع الأسنان",
            600,
            "40 minutes",
            "Recommended every 6 months.",
        ),
        price(
            "S03",
            "Tooth-colored filling",
            "حشو بلون الأسنان",
            700,
            "40 minutes",
            "Per tooth; larger cavities may cost more after examination.",
            "base",
            "tooth",
        ),
        price(
            "S04",
            "Root canal (single-canal tooth)",
            "علاج عصب لسن بقناة واحدة",
            1800,
            "60–90 minutes",
            "Multi-canal teeth cost more; final quote after X-ray.",
            "starting_from",
        ),
        price(
            "S05",
            "Simple extraction",
            "خلع بسيط",
            500,
            "20 minutes",
            "Surgical extractions are priced by Dr. Dina after examination.",
        ),
        price(
            "S06",
            "Wisdom tooth surgical removal",
            "خلع جراحي لضرس العقل",
            1500,
            "45–60 minutes",
            "Panoramic X-ray required first; final quote after examination.",
            "starting_from",
        ),
        price(
            "S07",
            "In-clinic teeth whitening",
            "تبييض الأسنان في العيادة",
            3500,
            "75 minutes",
            "One session. Pregnancy or active gum disease suitability questions go to staff.",
        ),
        price(
            "S08",
            "Porcelain crown",
            "تركيبة بورسلين",
            4500,
            "2 visits",
            "Final material and price confirmed at examination.",
            "starting_from",
            "tooth",
        ),
        price(
            "S09",
            "Dental implant (single)",
            "زراعة سن واحد",
            14000,
            "Several visits",
            "Consultation with Dr. Karim and 3D scan required; never a final chat quote.",
            "starting_from",
            "implant",
        ),
        price(
            "S10",
            "Metal braces (full treatment)",
            "تقويم معدني كامل",
            18000,
            "12–24 months",
            "Orthodontic consultation required. Monthly follow-up charged separately.",
            "starting_from",
        ),
        price(
            "S11",
            "Clear aligners",
            "تقويم شفاف",
            35000,
            "8–18 months",
            "Case assessment by Dr. Hossam required.",
            "starting_from",
        ),
        price("S12", "Dental X-ray (single)", "أشعة أسنان واحدة", 150, "5 minutes"),
        price(
            "S13",
            "Children's checkup and fluoride",
            "كشف أطفال وفلورايد",
            450,
            "30 minutes",
            "Ages 3–12; a parent or guardian must attend.",
        ),
        price(
            "S14",
            "Emergency visit",
            "زيارة طوارئ",
            500,
            "Same-day subject to availability",
            "For pain, swelling or broken tooth. Treatment costs extra; "
            "staff confirms availability.",
        ),
        price("S12-PAN", "Panoramic X-ray", "أشعة بانوراما", 350),
        price("S10-FOLLOWUP", "Monthly braces follow-up", "متابعة تقويم شهرية", 500, unit="month"),
        price(
            "PKG-FAMILY",
            "Family Checkup package",
            "باقة كشف العائلة",
            1900,
            conditions="Exam, single X-ray and cleaning for 2 people. "
            "Published regular total 2100 EGP.",
        ),
        price(
            "PKG-SMILE",
            "Bright Smile package",
            "باقة الابتسامة المشرقة",
            3900,
            conditions="Cleaning and in-clinic whitening. Published regular total 4100 EGP.",
        ),
        price(
            "PKG-KIDS",
            "Kids Starter package",
            "باقة بداية الأطفال",
            800,
            conditions="Children's checkup and fluoride for 2 children. Regular total 900 EGP.",
        ),
        price(
            "POLICY-LATE",
            "Late cancellation or no-show booking fee",
            "رسوم إلغاء متأخر أو عدم حضور",
            150,
            conditions="Added to the next visit for cancellation under 4 hours or a no-show.",
        ),
        price(
            "POLICY-INSTALLMENTS",
            "Installment eligibility minimum",
            "الحد الأدنى لطلب تقسيط",
            10000,
            conditions="This is an eligibility threshold, not a service price. "
            "Applies to implants, "
            "braces, aligners and crowns. Partner provider decides approval and terms.",
        ),
        price(
            "POLICY-REFERRAL",
            "Refer-a-friend reduction",
            "خصم ترشيح صديق",
            100,
            conditions="This amount is a reduction, not a charge. Both patient and referred friend "
            "receive it on their next visit. One promotion per visit; do not combine offers.",
        ),
    ]
    rows[0]["offers"] = [
        {
            "name": "First-visit exam + X-ray",
            "amount": 350,
            "conditions": "New patients only; exam and single X-ray package, instead of "
            "450 EGP. One promotion per visit.",
            "valid_until": "2026-12-31",
        }
    ]
    rows[1]["offers"] = [
        {
            "name": "Student cleaning",
            "amount": 540,
            "conditions": "Valid student ID shown at the visit. One promotion per visit.",
        }
    ]
    rows[6]["offers"] = [
        {
            "name": "Student whitening",
            "amount": 3150,
            "conditions": "Valid student ID shown at the visit. One promotion per visit.",
        }
    ]
    return rows


def clinic_facts(source):
    pages = PdfReader(source).pages
    section_text = "\n".join(page.extract_text() or "" for page in pages[1:5])
    section_text = section_text.split("8. Handoff and escalation rules", 1)[0]
    for marker in ("12 Mostafa El-Nahas", "300", "35,000", "31 Dec 2026", "Student discount"):
        if marker not in section_text:
            raise RuntimeError("Unexpected clinic source; review its business facts before seeding")
    # This curated transcription includes facts from sections 1–7 only. Agent instructions,
    # test conversations, hypothetical calendar capabilities and ROI assumptions are excluded.
    return """BRIGHT NILE DENTAL CLINIC — FICTIONAL DEMO BUSINESS
All names, addresses, contacts, emergency lines and prices below are invented for demonstration.
No contact in this document is a real medical or emergency service.

ABOUT
Opened in 2016. Private family and cosmetic dental clinic in Nasr City, Cairo.
Gentle, transparent care with clear prices and visible sterilization standards.

LOCATION, HOURS AND CONTACT
12 Mostafa El-Nahas Street, 3rd floor, Nasr City, Cairo; next to City Pharmacy and opposite
Al-Salam Bank. Five-minute walk from Mostafa El-Nahas tram stop. Street parking available;
a paid garage is 100 metres away. Elevator and ground-floor accessible drop-off available.
Saturday–Thursday 10:00 AM–9:00 PM; Friday closed except the demo emergency line.
Closed on official holidays; dates announced a week ahead.
Demo phone/WhatsApp: +20 100 555 0142 (fictional, not real).
Demo emergency line: +20 100 555 0199 (fictional, not a real emergency service).
Emergency-line availability is not guaranteed. The demo clinic lists severe pain, swelling and
knocked-out teeth as urgent reasons to contact staff. No doctor response is promised.

DOCTORS
Dr. Karim Mansour, owner: general/cosmetic dentistry and implants; Saturday, Monday, Wednesday.
Implant and smile-makeover consultations.
Dr. Salma Fouad: general dentistry, fillings and root canals; Saturday–Thursday.
Especially experienced with children and nervous patients.
Dr. Hossam Adel: orthodontics, braces and aligners; Sunday, Tuesday, Thursday consultations.
Dr. Dina Samir: oral surgery and extractions; Sunday and Tuesday.
Hygienist Mona: scaling and polishing; Saturday–Thursday.

SERVICES
Consultations, cleaning, tooth-colored fillings, root canals, extractions, whitening, crowns,
implants, metal braces, clear aligners, X-rays, children's checkups/fluoride and emergency visits.
Durations, prices, starting-price limitations, packages and monetary offers are in the approved
structured services catalog. Personalized treatment and suitability require staff assessment.
Cleaning takes about 40 minutes. Whitening results generally last 6–12 months depending on
coffee, tea and smoking; this is an estimate and not a guarantee.

PAYMENT AND INSURANCE
Cash, Visa/Mastercard, InstaPay and Vodafone Cash accepted.
Installments are offered through a partner provider for eligible treatments; the provider decides
approval and terms. Implant, braces and aligner deposits are set by Dr. Karim at consultation.
Normal visits require no deposit. No direct contracts with insurance companies.
Detailed invoices can be provided for reimbursement requests. The insurer decides coverage.

VISIT POLICIES
Name, phone number, service and preferred day/time are needed for appointment requests.
Staff confirms all appointment availability and bookings in this MVP; no live calendar is connected.
Consultation/cleaning slots are generally 30–40 minutes; individual treatment durations vary.
Cancellation or rescheduling is free up to four hours before the appointment; late cancellation
or no-show may add the catalog booking fee to the next visit.
Fifteen-minute late-arrival grace period; later arrival may require rescheduling.
Children aged three and over are seen; parent or guardian must accompany them.
Pregnancy, medication, diagnosis and personal suitability questions require clinical staff.
The source describes appointment reminders, but this demo does not send scheduled reminders.

GENERAL FAQ
Modern local anesthesia and gentle techniques are used; individual comfort cannot be guaranteed.
Instruments are autoclaved after each patient and single-use items discarded.
Female clinicians include Dr. Salma Fouad, Dr. Dina Samir and hygienist Mona.
Same-day emergency visits may be available; staff must verify availability.

PUBLISHED OFFERS
First-visit exam plus X-ray offer runs through 31 December 2026 for new patients.
Student cleaning/whitening offers require a valid student ID at the visit.
Refer-a-friend offer applies to both the patient and friend on their next visit.
One promotion per visit; offers are not combined. Amounts are in the approved catalog.
Ramadan evening hours may extend to 8 PM–midnight when announced for that year;
no current seasonal dates or active extended-hours announcement is supplied.
"""


def rules():
    return [
        {
            "name": "Explicit human request",
            "conditions": "The customer explicitly asks to speak "
            "with a human. Asking whether Salma is a real person alone is not this request.",
            "destination": "staff",
            "acknowledgment": "I will ask a team member to help you.",
            "examples": ["I want to speak to a human", "عايز اكلم موظف"],
        },
        {
            "name": "Negotiated discount or final custom quote",
            "conditions": "Negotiating an "
            "unpublished discount, price match, free treatment or final personalized quote. "
            "Published "
            "promotion enquiries and starting-from price enquiries are answered without transfer.",
            "destination": "owner",
            "acknowledgment": "I will ask the owner to review your request.",
            "examples": ["Can you give me 40% off?", "Can you match another price?"],
        },
        {
            "name": "Medical advice or individual suitability",
            "conditions": "Diagnosis, medication, "
            "dosage, pregnancy or personalized treatment suitability requires a clinician.",
            "destination": "doctor",
            "acknowledgment": "I cannot give medical advice. I will pass "
            "your question to clinical staff.",
            "examples": ["Should I take antibiotics?"],
        },
        {
            "name": "Urgent symptoms",
            "conditions": "Severe pain, swelling, bleeding, trauma or a "
            "knocked-out tooth. Flag urgency and hand to staff; never diagnose or promise a reply.",
            "destination": "doctor",
            "acknowledgment": "I will flag this for staff. This is a fictional "
            "demo clinic: its listed emergency number +20 100 555 0199 is not a real emergency "
            "service, and no doctor response is promised.",
            "examples": ["Severe swelling and pain"],
        },
        {
            "name": "Complaint",
            "conditions": "Customer complains about care or service, or requests "
            "compensation. Acknowledge without promising compensation or response time.",
            "destination": "owner",
            "acknowledgment": "I am sorry about your experience. I will "
            "pass your complaint to the owner.",
            "examples": ["I waited 40 minutes last time."],
        },
        {
            "name": "Unknown business information",
            "conditions": "After searching approved knowledge "
            "and catalog, a necessary business fact is absent. Clarify ambiguous requests first.",
            "destination": "staff",
            "acknowledgment": "I do not have that information yet. I will "
            "ask a team member to help.",
            "examples": ["What do veneers cost?"],
        },
        {
            "name": "Appointment confirmation",
            "conditions": "The customer requests an appointment "
            "and name, phone, service and preferred day/time have been collected. Before that, "
            "continue collecting missing fields. General booking policy enquiries do not transfer.",
            "destination": "staff",
            "acknowledgment": "I have your appointment preferences. Staff "
            "will check availability and confirm; this is not a confirmed booking.",
            "examples": ["Please confirm my appointment request."],
        },
    ]


def require_ok(response):
    if response.is_error:
        # Never print response request headers, cookies, passwords or invite tokens.
        raise RuntimeError(
            f"API request failed: {response.status_code} {response.request.url.path}"
        )
    return response.json()


def api_login(client, credentials):
    result = require_ok(client.post("/api/auth/login", json=credentials))
    client.headers["X-CSRF-Token"] = result["csrf_token"]
    return result


def ensure_document(client, bid, filename, content):
    path = f"/api/businesses/{bid}/documents"
    existing = require_ok(client.get(path))["documents"]
    row = next((item for item in existing if item["filename"] == filename), None)
    if row is None:
        row = require_ok(
            client.post(path, files={"file": (filename, content.encode("utf-8"), "text/plain")})
        )
    identifier = row["id"]
    deadline = time.monotonic() + 180
    while row["status"] not in ("ready", "failed") and time.monotonic() < deadline:
        time.sleep(2)
        row = next(
            item for item in require_ok(client.get(path))["documents"] if item["id"] == identifier
        )
    if row["status"] != "ready":
        raise RuntimeError(f"Document indexing {row['status']}; repair it in Knowledge and rerun")
    require_ok(client.patch(f"{path}/{identifier}", json={"approved": True}))
    return identifier


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--clinic-pdf", type=Path, default=PDF)
    args = parser.parse_args()
    if urlparse(args.api).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("This local demo setup only accepts a loopback API URL")
    facts = clinic_facts(args.clinic_pdf)
    access = json.loads(ACCESS.read_text(encoding="utf-8")) if ACCESS.exists() else {}
    operator = access.get("operator") or {
        "email": os.environ.get("SALMA_OPERATOR_EMAIL", "operator@salma.local"),
        "password": os.environ.get("SALMA_OPERATOR_PASSWORD") or secrets.token_urlsafe(24),
    }
    with session() as db:
        existing = db.scalar(select(User).where(User.email == operator["email"]))
        if existing and existing.role != "operator":
            raise RuntimeError(
                "Configured operator email belongs to an owner; choose another email"
            )
        if (
            existing
            and not access.get("operator")
            and not os.environ.get("SALMA_OPERATOR_PASSWORD")
        ):
            raise RuntimeError(
                "Operator already exists. Supply its existing SALMA_OPERATOR_PASSWORD; "
                "the script never resets passwords"
            )
        if not existing:
            db.add(
                User(
                    email=operator["email"],
                    password_hash=hash_password(operator["password"]),
                    role="operator",
                )
            )
            db.commit()
    access["operator"] = operator
    access.setdefault("businesses", {})
    access.setdefault("owners", {})
    save_access(access)
    with httpx.Client(base_url=args.api, timeout=60) as client:
        api_login(client, operator)
        businesses = require_ok(client.get("/api/businesses"))["businesses"]
        configurations = [
            ("clinic", "Bright Nile Dental Clinic", facts, clinic_catalog()),
            (
                "garage",
                "Harbor Auto Care",
                """HARBOR AUTO CARE — FICTIONAL DEMO GARAGE
Independent vehicle maintenance garage. Saturday–Thursday 8 AM–6 PM, Friday closed.
Oil and filter change normally takes 45 minutes. Inspection and appointment availability require
staff confirmation. Oil grade and compatibility are checked by the technician; do not diagnose
mechanical faults in chat. Address: Fictional Harbor Service Road, Cairo (demo only).
No real phone or emergency service is provided. Published oil-change package is in the catalog.
""",
                [
                    price(
                        "OIL-01",
                        "Oil and filter change",
                        "تغيير زيت وفلتر",
                        900,
                        "45 minutes",
                        "Published demo package; technician confirms oil grade "
                        "and vehicle compatibility.",
                    )
                ],
            ),
        ]
        for label, name, content, catalog in configurations:
            business = next((item for item in businesses if item["name"] == name), None)
            if business is None:
                business = require_ok(client.post("/api/businesses", json={"name": name}))
                businesses.append(business)
            bid = business["id"]
            document = ensure_document(client, bid, f"{label}-approved-facts-v1.txt", content)
            current = require_ok(client.get(f"/api/businesses/{bid}/draft"))["config"]
            config = {
                **current,
                "name": name,
                "description": "Fictional demo "
                + (
                    "dental clinic in Nasr City, Cairo"
                    if label == "clinic"
                    else "vehicle maintenance garage"
                ),
                "timezone": "Africa/Cairo",
                "languages": ["English", "Egyptian Arabic", "Arabic"],
                "tone": "Warm, calm and concise; match the customer language.",
                "sales_objectives": [
                    "Answer approved business questions",
                    "Collect qualified appointment preferences for staff",
                ],
                "lead_fields": ["name", "phone", "service", "preferred_day_time"],
                "opening_hours": (
                    "Saturday–Thursday 10 AM–9 PM; Friday closed"
                    if label == "clinic"
                    else "Saturday–Thursday 8 AM–6 PM; Friday closed"
                ),
                "contact_details": (
                    {
                        "phone": "+20 100 555 0142",
                        "emergency_phone": "+20 100 555 0199",
                        "fictional": True,
                        "notice": "All contacts are invented; not real medical/emergency services.",
                    }
                    if label == "clinic"
                    else {"fictional": True, "notice": "No real contacts supplied."}
                ),
                "owner_instructions": "This business is fictional. Identify as its AI assistant "
                "when asked; do not transfer merely for an AI identity question. Answer published "
                "offers without negotiation handoff. Collect name, phone, service and preferred "
                "day/time for appointments; staff must confirm availability. No calendar, "
                "reminders, CRM sequences or autonomous payments are connected. No medical or "
                "mechanical diagnosis. All source contact numbers are fictional demo data.",
                "handoff_rules": rules()
                if label == "clinic"
                else [
                    rule
                    for rule in rules()
                    if rule["name"]
                    not in ("Medical advice or individual suitability", "Urgent symptoms")
                ],
                "catalog": catalog,
                "document_ids": list(dict.fromkeys(current.get("document_ids", []) + [document])),
            }
            require_ok(client.put(f"/api/businesses/{bid}/draft", json=config))
            versions = require_ok(client.get(f"/api/businesses/{bid}/versions"))["versions"]
            if (
                versions
                and versions[0]["config"]
                == require_ok(client.get(f"/api/businesses/{bid}/draft"))["config"]
            ):
                version = versions[0]["version"]
            else:
                version = require_ok(client.post(f"/api/businesses/{bid}/publish"))["version"]
            access["businesses"][label] = {"id": bid, "name": name, "version": version}
            owner = access["owners"].get(label) or {
                "email": f"{label}.owner@salma.local",
                "password": secrets.token_urlsafe(24),
                "business_id": bid,
            }
            access["owners"][label] = owner
            save_access(access)
            with httpx.Client(base_url=args.api, timeout=60) as owner_client:
                response = owner_client.post(
                    "/api/auth/login", json={key: owner[key] for key in ("email", "password")}
                )
                if response.status_code == 401:
                    invite = require_ok(
                        client.post(
                            f"/api/businesses/{bid}/invites", json={"email": owner["email"]}
                        )
                    )
                    require_ok(
                        owner_client.post(
                            "/api/auth/accept-invite",
                            json={"token": invite["token"], "password": owner["password"]},
                        )
                    )
                else:
                    require_ok(response)
                identity = require_ok(owner_client.get("/api/auth/me"))
                if bid not in {row["id"] for row in identity["businesses"]}:
                    raise RuntimeError(
                        "Existing owner does not have access to the expected workspace"
                    )
            save_access(access)
    print("Configured 2 fictional business workspaces and 2 owner accounts.")
    for business in access["businesses"].values():
        print(f"{business['name']}: {business['id']} (version {business['version']})")
    print(f"Private local access record: {ACCESS}")


if __name__ == "__main__":
    main()
