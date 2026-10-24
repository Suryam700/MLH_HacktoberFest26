# CivicFix AI — From citizen evidence to trackable civic issues

CivicFix AI is a prototype civic issue intelligence app. It accepts citizen evidence, assigns stable CivicFix issue IDs, scores and clusters reports, records prototype authority responses, and separately assesses whether before/after photos appear to show improvement. Authority submissions are not connected to or verified by a real government department.

## MVP capabilities

- **Evidence validation:** the model returns `valid`, `irrelevant`, or `ambiguous` and a confidence score. Irrelevant, ambiguous, or low-confidence evidence is stopped with clarification prompts.
- **Structured analysis:** Pydantic validates category, severity, public impact, safety risk, evidence quality, missing information and complaint text. Original citizen evidence is stored separately from model output.
- **Priority score:** defaults to `35% severity + 25% public impact + 20% safety risk + 10% confidence + 10% freshness`. Weights and the freshness-decay interval are configurable in `config/scoring.json`; these are not official policy or emergency advice.
- **Duplicate suggestions:** category and lexical text similarity, plus distance when coordinates are available. Geolocated candidates must be within 120 metres and pass a similarity threshold; without coordinates, the text threshold is higher. A resident chooses whether to group a new report. This MVP uses deterministic token similarity, not semantic embeddings/vector search.
- **Issue clusters:** one issue can contain multiple citizen reports, a first/latest photo, and community confirmations.
- **Stable CivicFix IDs:** issues receive canonical `CF-####` IDs; IDs from merged clusters remain searchable aliases.
- **Authority Portal:** gated prototype portal searches by CivicFix ID, marks work in progress and records department, authority, status, notes, date, reference and multiple after photos.
- **Government Resolutions:** citizens can search authority submissions and view government status separately from AI evidence assessment. Weak evidence remains pending or uncertain.
- **Before/after comparison:** multimodal AI can compare the original image with multiple authority photos and return status, confidence, same-location likelihood, issue persistence, evidence quality and limitations. It is not official verification.
- **Prioritized dashboard and maps:** map pins use only resident-confirmed coordinates. Google Maps and Street View are available with a restricted browser key; OpenStreetMap is the fallback.
- **Official handoff:** opens the configured portal. Residents submit there themselves and manually record any reference or status.

## Architecture

```text
Streamlit utility shell (app.py + civicfix/ui.py)
  ├─ Home, Scan, My Reports, All Reports, Civic Map, Issues
  ├─ Government Resolutions, Authority Portal, Insights, Profile
  ├─ camera/gallery intake, map pin selection, report review
  ├─ civic validation + structured multimodal analysis (ai.py)
  │    ├─ Gemini API (default; GOOGLE_API_KEY from .env)
  │    ├─ Ollama local model (optional)
  │    └─ clearly labeled unverified local-template fallback
  ├─ Pydantic contracts (models.py)
  ├─ priority + freshness (priority.py)
  ├─ duplicate matching (deduplication.py)
  ├─ issue workflow (issue_service.py)
  ├─ editable authority routes (config/routes.json)
  └─ SQLite issues, reports, confirmations, authority resolutions/evidence, model runs (storage.py)
```

The UI is styled as a civic utility and operations tool rather than a chat screen. The current MVP keeps Streamlit so the complete workflow runs in one Python app; the AI, scoring, matching, schemas and persistence stay modular for later extraction into React/FastAPI if deployment needs justify it.

## Run on Windows

1. Create and activate the environment, then install dependencies:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

2. Keep your existing `.env` in the project root. It must contain `GOOGLE_API_KEY` for Gemini. `CIVICFIX_REVIEW_KEY` unlocks reviewer tools; `CIVICFIX_AUTHORITY_KEY` gates prototype authority submissions. Optional settings include `AI_PROVIDER`, `GEMINI_MODEL`, `CIVICFIX_DB`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `GOOGLE_MAPS_BROWSER_KEY` and `CIVICFIX_REPO_URL`.

3. Start the app:

   ```powershell
   streamlit run app.py
   ```

For local Ollama instead, set `AI_PROVIDER=ollama` in `.env`, install/start Ollama, then pull a vision-capable model such as `gemma3:4b`. Hardware needs depend on the model and machine.

SQLite and photo evidence are saved under `data/civicfix.sqlite3` by default. `.env` and `data/` are Git-ignored. Never commit API keys or private citizen evidence.

Set a strong `CIVICFIX_REVIEW_KEY` in the server `.env` to unlock the **All reports** page, reviewer-only evidence, issue merging and status controls. The page lists every saved user report, supports search and category/status filters, and can export the filtered list as CSV. Without the key, private report details remain hidden. This is a lightweight prototype gate, not production account authentication. My Reports uses a browser-session identifier rather than an account.

`CIVICFIX_AUTHORITY_KEY` is a separate prototype credential for **Authority Portal**. This portal is not a real government integration. AI evidence comparison is sent to the selected provider only after explicit consent. Government status and AI evidence assessment remain separate.

Google Maps is optional. Put a **browser-restricted** Maps JavaScript API key in `GOOGLE_MAPS_BROWSER_KEY`; enable billing, the Maps JavaScript API, and the Geocoding API for location search. Restrict the key to your app's HTTP referrers (for local development, `http://localhost:8501/*`) and the APIs you use. The Maps key is visible to the browser by design and must not be your server-side Gemini `GOOGLE_API_KEY`. When absent, CivicFix falls back to OpenStreetMap and explains that Street View is unavailable.

## Demo flow

Use a clear pothole or open-drain photo with a landmark, then show validation, editable extraction, priority factors and the duplicate candidate prompt. Submit a second report from a nearby coordinate to demonstrate resident-controlled grouping. Open the issue dashboard, add a later photo, and show the before/after result including uncertainty and limitations. Also try an irrelevant or dark image to demonstrate that the validator asks for better evidence instead of creating a complaint. Use synthetic/demo photos with permission; do not present illustrative scores as official statistics.

## Jurisdiction and routing

`config/routes.json` currently points to the Uttar Pradesh JanSunwai portal for Kanpur Nagar. Verify the jurisdiction, department mapping and links before use elsewhere. CivicFix does not automate OTP/CAPTCHA, submit complaints, or receive official status updates.

## Reliability limits

- Priority is a ranking aid, not an official risk classification.
- Duplicate matching can miss synonyms and can suggest a false match. Reports are grouped only after the resident selects the candidate.
- Before/after photos may differ in angle, lighting or location; inconclusive comparisons must remain uncertain.
- Confirmation is limited to once per Streamlit browser session; this is not identity verification or abuse-resistant voting.
- Consent is requested before sending report content/photos to Gemini. Ollama uses the configured local endpoint.
- There are no accounts, public hosting controls, official government API, status synchronization or production moderation tools.
- Check the event's open-source/open-weight rules. Hosted Gemini may not meet a requirement for local open-weight inference; the optional Ollama/Gemma provider is available for that route. Verify model licenses and event terms.
- The repository code is under the MIT License (`LICENSE`). The model provider has separate terms and model-specific licensing; check those before submission.

## Evaluation plan

Prepare a labeled sample containing clear civic issues, irrelevant images, ambiguous/dark photos, near-duplicate reports and before/after pairs. Record expected labels, actual results, confidence, false duplicate suggestions, resolution assessments and latency. The app records model name, prompt version, timestamp and validation result; report measured accuracy only after running that benchmark.

Run the local backend workflow checks with:

```powershell
python -m unittest discover -s tests -v
```

## Main files

- `app.py` — report flow, issue dashboard, confirmations and resolution review.
- `src/civicfix/ui.py` — responsive civic utility screens and navigation.
- `src/civicfix/ai.py` — Gemini/Ollama analysis and before/after comparison.
- `src/civicfix/models.py` — validated analysis, issue and resolution contracts.
- `src/civicfix/priority.py` — deterministic score and freshness decay.
- `config/scoring.json` — priority weights and freshness interval.
- `src/civicfix/deduplication.py` — category, distance and lexical candidate matching.
- `src/civicfix/issue_service.py` — score and duplicate workflow helpers.
- `src/civicfix/storage.py` — SQLite schema, migration and persistence.
- `tests/test_workflow.py` — deterministic scoring, dedupe and storage workflow checks.
- `src/civicfix/routing.py`, `config/routes.json` — authority routing configuration.

## Contributions

Open an issue describing the change, keep model/provider code behind the existing adapter boundary, and avoid committing `.env`, the SQLite database, or real citizen photos. For jurisdiction changes, include the source used to verify each official route.
