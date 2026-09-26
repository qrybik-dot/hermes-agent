---
name: call-card-verification
description: "Use when creating call cards. Verifies against transcripts."
version: 1.2.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Calls, Verification, Zero-Hallucination, Quality-Guard]
    related_skills: [meeting-canonical-ingest, knowledge-card-curation]
---

# Call Card Verification & Zero-Hallucination Protocol

## Autonomous Execution Rule (User Mandate)
- When the user says «сохранить звонок» / «сохрани звонки», interpret this as an immediate command to complete the **ENTIRE pipeline autonomously** into the canonical Knowledge Vault.
- Do NOT stop at Telegram inline review buttons, do NOT ask intermediate confirmations or permissions to save.
- Canonical save-flow: `call_on_demand.py` (fetch -> process -> save-known) -> read-back verification via MCP.
- Pull via on-demand Syncthing fetch -> process/transcribe -> save to Knowledge Vault via MCP -> verify with read-back.
- **The ONLY allowed clarification:** ask WHICH specific calls to save only if the user did not explicitly state the contact, phone, or date/topic.

## When to Use
- User asks to process, analyze, summarize, or create a call card from audio recordings or call transcripts of a phone call.
- Before saving call review cards into Vault/Obsidian or presenting phone call summaries to the user.
- **Boundary & Routing:** For Granola meetings, notes, and meeting transcripts: `granola_owner: meeting-canonical-ingest`. Route explicitly to `meeting-canonical-ingest`. Do not duplicate Granola ingestion logic here.

## Execution Requirements: Samsung Selective Call Intake & Knowledge Saving
- When selectively searching, pulling and processing Samsung phone call recordings:
  1. **Canonical Tool:** `/srv/hermes-memory/app/standalone_call_worker/call_on_demand.py` executed via python in `/srv/hermes-memory/venv/call_worker_venv/bin/python`.
  2. **Caller ID vs Phone Book (Dual Search Strategy):**
     - Samsung naming format: `Вызов <МеткаИлиИмяИлиНомер>_<ГГММДД>_<ЧЧММСС>.m4a`.
     - **Important:** Samsung uses automated Caller ID (АОН / спам-фильтры / Яндекс / 2ГИС / Сбер). Even if a contact is NOT in the user's address book, Samsung often labels the file with an automated organization name (e.g. `PIK SZ finansy`, `ООО УПРАВЛЕНИЕ ПЕРСОНАЛОМ`, `Ренессанс Банк`), and the phone number is NOT present in the filename!
     - **Dual Search Rule:**
       * Pass **BOTH** `--contact-hint "<Организация/Имя>"` AND `--phone-hint "<ЦифрыНомера>"` whenever both are available.
       * Set a generous time window (`--start` and `--end` spanning at least 30–60 min around the expected time) and `--timeout 90` to allow Syncthing to handshake and pull.
     ```bash
     /srv/hermes-memory/venv/call_worker_venv/bin/python \
       /srv/hermes-memory/app/standalone_call_worker/call_on_demand.py fetch \
       --date YYYY-MM-DD --start HH:MM --end HH:MM \
       --contact-hint "<Hint>" --phone-hint "<Digits>" --timeout 90
     ```
  3. **Process Staged Recording:** Once the candidate ID is retrieved from `fetch`:
     ```bash
     /srv/hermes-memory/venv/call_worker_venv/bin/python \
       /srv/hermes-memory/app/standalone_call_worker/call_on_demand.py process \
       --date YYYY-MM-DD --start HH:MM --end HH:MM \
       --candidate-id <ID> --confirm PROCESS_SELECTED
     ```
  4. **Save to Knowledge Vault via Canonical Save-Known:**
     - The fastest, zero-loss canonical way to persist both summary card and all transcript parts into Knowledge is `save-known`:
     ```bash
     /srv/hermes-memory/venv/call_worker_venv/bin/python \
       /srv/hermes-memory/app/standalone_call_worker/call_on_demand.py save-known \
       --call-id <CALL_ID> --mode full --confirm SAVE_SELECTED
     ```
     - Verify with read-back via `mcp__knowledge__knowledge_search`.

  5. **Commitments, Action Items & Follow-ups (Explicit Authority Gate):**
     - Ingest agreements, deadlines, and scheduled discussions mentioned in the conversation.
     - **No Implicit Side Effects:** Calendar event creation, reminder scheduling, or task creation requires an **explicit user request**. Do NOT automatically create external events or set reminders without explicit user direction.
     - Proactive suggestions for follow-ups or action items must remain strictly non-mutating text suggestions within the call summary.

## Golden Rules
1. **SUPER-CLEAR HEADINGS (MANDATORY FORMAT):** Always format the top card heading with exact date, start time, contact/company, and a self-contained summary of what was discussed so the essence is obvious immediately from the title.
   - Format: `# 📞 YYYY-MM-DD (HH:MM) — Имя Контакта («Компания»): Суть обсуждения и ключевой итог`
   - Example: `# 📞 2026-09-07 (17:41) — Игорь Газизов («Блоксели»): План аудита найма педагогов, подготовка пути кандидата и условия договора`
2. **ZERO ASSUMPTIONS & ZERO EXTERNAL TEMPLATES:** Never project generic business models, popular startup patterns (e.g. AI-calling bots, cold-lead auto-dialers, generic sales pitches), or prior assumptions onto a call unless literally stated in the audio.
3. **VERBATIM CITATION REQUIREMENT:** Every single bullet in Summary, Context, Findings, Budget, and Next Steps MUST trace back to an exact spoken sentence in the transcript. If it was not spoken in the audio, it does not exist.
4. **EXPLICIT SPEAKER ATTRIBUTION & AMBIGUITY HANDLING:** Clearly attribute who said what (e.g. "Anton proposed X", "Igor stated Y").
   - **Critical rule on ambiguity:** If the speaker of a crucial statement, requirement, number, or decision is unclear or ambiguous from the audio, **NEVER GUESS OR INVENT**. Explicitly mark it: `[Спикер не определен достоверно / требует уточнения]` or ask the user directly before finalizing and saving the card.
   - If there is ANY doubt regarding who agreed to what, clarify with the user instead of assuming.
5. **CROSS-CHECK PASS (PRE-SAVE SANITY CHECK):** Before presenting or persisting a call card, run a strict verification pass comparing the drafted bullet points against raw transcript search matches.

## Verification Checklist
- [ ] Has the raw transcript been read in full (not sampled or summarized blindly)?
- [ ] Are all named tools, software, metrics, and figures (e.g. "BPM", "LMS", "Skyeng", "200 000 ₽", "20 школ") exact mentions from the audio?
- [ ] Is there ANY mention of technology or proposals not in the transcript? If yes, PURGE immediately.
- [ ] Are speaker attributions 100% verified? If unclear who said it, is it explicitly flagged or asked?
- [ ] Are action items and deadlines explicitly agreed upon, with exact dates/times mentioned by the participants?
- [ ] Is the duration, date, and contact identity verified against telephony and audio metadata?
