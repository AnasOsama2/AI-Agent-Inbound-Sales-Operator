# Bright Nile clinic knowledge and handoff review

Reviewed 2026-10-09. Status: proposed clinic policy; running agent unchanged.

Source: C:/Users/user/Downloads/Bright_Nile_Dental_Clinic_Knowledge_Pack.pdf
All clinic details, contacts and prices are fictional demo data.

## Verified current behavior

- The active catalog has only a 500 EGP agency consultation and quote-only custom AI/software projects. Four knowledge rows belong to the old agency demo.
- The system prompt explicitly identifies the business as an AI/software agency.
- `mandatory_handoff` matches isolated words such as human, person, agent, discount, booking and quote without understanding the request. This catches identity questions, published promotions and booking-policy questions.
- Knowledge rendering rejects any excerpt containing a digit, along with monetary terms. Hours, addresses, phone numbers, durations and ages would be rejected even with valid evidence.
- Knowledge rendering copies entire retrieved excerpts, rather than composing a short answer to the specific question. Simply removing the numeric guard would not solve relevance or price integrity.
- Actual stored decision-validation events include five keyword-triggered handoffs, two unobserved-evidence handoffs and one missing-facts handoff. These counts include existing test activity; they are not a quality rate for real customers.
- Handoff ownership intentionally persists. Further customer messages do not resume AI; only staff can resume it.

## Proposed boundary

| Request | Behavior | Ownership |
| --- | --- | --- |
| Greeting or vague "price?" | Greet or ask which service; no fabricated facts | AI active |
| Hours, location, accessibility, doctors' published days, general FAQs, insurance policy | Give a short answer supported by the clinic evidence | AI active |
| Published service or package price | Exact structured lookup; preserve unit, conditions and inclusions | AI active |
| Starting-from price | Quote the lower bound with its exam/assessment condition; never a final quote | AI active |
| Published promotion or payment-plan policy | Explain approved terms and validity; no eligibility/credit approval promise or stacking | AI active |
| Negotiated discount, price match, bespoke final quote | Explain limitation, collect relevant context, transfer | Owner |
| Booking policies or general scheduling question | Explain documented policy; distinguish doctor's scheduled days from available slots | AI active |
| Customer wants an appointment | Collect service, name, contact and preferred day/time, then request staff confirmation | Staff after collection |
| Medical diagnosis, medication/dosage, pregnancy or individual treatment suitability | No personalized medical advice; transfer with context | Doctor |
| Severe pain, swelling, bleeding or trauma | Use a separately approved urgent response, include clearly labeled demo emergency contact, escalate immediately; never promise availability | Urgent escalation |
| Complaint or explicit request for a human | Transfer immediately; don't promise staff response times the system cannot ensure | Owner / receptionist |
| "Are you a real person?" | Disclose AI identity and offer a person; transfer only if requested | AI active |
| Truly missing business fact after retrieval | Acknowledge missing information and transfer; answer any independently supported part | Receptionist |
| Ambiguous request | Ask a useful clarifying question instead of transferring immediately | AI active |
| STOP / Arabic opt-out | Acknowledge once and suppress subsequent automated outbound messages until explicit re-opt-in | Opted out, not a handoff |
| Provider failure or invalid/unobserved evidence after a bounded recovery attempt | No invented reply; flag for staff with a specific technical reason | Staff |

Business documents supply facts and proposed business policies. They do not authorize new integrations, outbound follow-ups, CRM writes or calendar changes.

## Source ambiguities and integration limits

- Booking text says calendar success confirms an appointment, but configuration and evaluation require approval. Recommended V1 rule: collect preferences, then staff approval; no slots are claimed available without a real calendar result.
- There is no calendar tool in the current agent. Published doctor days are not appointment availability. Calendar-dependent demo expectations cannot pass yet.
- Published promotions are allowed in section 7, while section 8 broadly says "Discount" requires escalation. Interpret this as negotiation/exception requests; asking about an existing promotion stays with AI.
- The 700 EGP filling price can rise after examination, despite lacking the explicit "Starting from" label. Preserve that caveat.
- Whitening exclusions are documented policy, not permission for Salma to assess an individual patient's suitability.
- The emergency contacts are fictional. They must never be presented as real emergency services in a live deployment. Urgent response wording needs clinical review before live patient use.
- Do not guarantee a same-day manager callback or available emergency visit based only on PDF prose.
- Ramadan hours are seasonal and require an actual announcement; they are not current opening hours. Offer expiry uses Africa/Cairo dates.
- Opt-out, calendar, follow-up scheduling, Sara/Nour integrations and ROI calculations are separate capabilities; the PDF must not be ingested as if these features already exist.

## Concrete implementation design

1. Switch the configured business identity to Bright Nile. Retire the old agency knowledge/catalog from active retrieval while retaining conversation history.
2. Import clinic factual sections with page references. Keep evaluation conversations, ROI assumptions and integration instructions outside customer-answer retrieval.
3. Extend structured service data for fixed/base/starting-from amounts, currency, unit, duration, caveats and sub-items. Include all 14 services, three packages, panoramic X-ray, braces follow-up fee and approved promotions. Python renders/calculates monetary claims from these records.
4. Replace broad word triggers with intent-aware decisions and evidence checks. A price question must not become a final-quote request; mentioning booking must not become booking confirmation.
5. Permit supported nonmonetary numbers. Generate short, grounded answers with separate structured monetary segments instead of copying arbitrary mixed-price PDF chunks.
6. Preserve immediate human takeover and ownership checks. Show the specific handoff reason and a clear staff resume/new simulation action. Retrying a technical validation failure must never repeat successful mutating tools.
7. Add an explicit opt-out state distinct from staff ownership.
8. Verify the PDF's first 11 scenarios in English/Egyptian Arabic, adapted honestly for the missing calendar. Add negatives for hours/phone/durations, published versus negotiated discounts, identity disclosure versus human request, starting-from versus final price, mixed intents, opt-out, missing evidence and persistent ownership.

The scheduled follow-up scenario remains unsupported until an approved scheduler and WhatsApp template integration exist. Do not claim the whole PDF demo passes without them.
