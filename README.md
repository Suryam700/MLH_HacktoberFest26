# CivicFix AI — From citizen evidence to trackable civic issues

CivicFix AI is a prototype civic issue intelligence app. It accepts a citizen photo and description, validates whether evidence appears to show a public civic issue, extracts a structured report, calculates a transparent priority score, suggests possible duplicates, and tracks later evidence. It can compare a first photo with a later one, but its assessment is not proof of an official resolution.

## MVP capabilities

- **Evidence validation:** the model returns `valid`, `irrelevant`, or `ambiguous` and a confidence score. Irrelevant, ambiguous, or low-confidence evidence is stopped with clarification prompts.
- **Structured analysis:** Pydantic validates category, severity, public impact, safety risk, evidence quality, missing information and complaint text. Original citizen evidence is stored separately from model output.
- **Priority score:** defaults to `35% severity + 25% public impact + 20% safety risk + 10% confidence + 10% freshness`. Weights and the freshness-decay interval are configurable in `config/scoring.json`; these are not official policy or emergency advice.
- **Duplicate suggestions:** category and lexical text similarity, plus distance when coordinates are available. Geolocated candidates must be within 120 metres and pass a similarity threshold; without coordinates, the text threshold is higher. A resident chooses whether to group a new report. This MVP uses deterministic token similarity, not semantic embeddings/vector search.
- **Issue clusters:** one issue can contain multiple citizen reports, a first/latest photo, and community confirmations.
- **Before/after comparison:** returns `resolved`, `partially_resolved`, `still_open`, or `uncertain`, with confidence and limitations. It does not prove a repair or change an official complaint.
- **Prioritized dashboard and map:** map pins use only coordinates supplied by residents.
- **Official handoff:** opens the configured portal. Residents submit there themselves and manually record any reference or status.

## Architecture

```text
Streamlit UI (app.py)
  ├─ evidence intake, review/edit, dashboard, map
  ├─ civic validation + structured multimodal analysis (ai.py)
  │    ├─ Gemini API (default; GOOGLE_API_KEY from .env)
  │    ├─ Ollama local model (optional)
  │    └─ clearly labeled unverified local-template fallback
  ├─ Pydantic contracts (models.py)
  ├─ priority + freshness (priority.py)
  ├─ duplicate matching (deduplication.py)
  ├─ issue workflow (issue_service.py)
  ├─ editable authority routes (config/routes.json)
  └─ SQLite issues, reports, confirmations, model runs (storage.py)
```

The supplied prompt recommends React/TypeScript and FastAPI for a larger deployment. This one-day MVP keeps Streamlit so the complete workflow can run in one Python app, with the AI, scoring, matching, schemas and persistence kept modular for later extraction into APIs and a separate frontend.

## Run on Windows

1. Create and activate the environment, then install dependencies:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

2. Keep your existing `.env` in the project root. It must contain `GOOGLE_API_KEY`; the app loads it automatically. Gemini is the default provider and `gemini-3.8-flash` is the default model. Optional settings are `AI_PROVIDER`, `GEMINI_MODEL`, `CIVICFIX_DB`, `OLLAMA_BASE_URL` and `OLLAMA_MODEL`.

3. Start the app:

   ```powershell
   streamlit run app.py
   ```

For local Ollama instead, set `AI_PROVIDER=ollama` in `.env`, install/start Ollama, then pull a vision-capable model such as `gemma3:4b`. Hardware needs depend on the model and machine.

SQLite and photo evidence are saved under `data/civicfix.sqlite3` by default. `.env` and `data/` are Git-ignored. Never commit API keys or private citizen evidence.

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

## Main files

- `app.py` — report flow, issue dashboard, confirmations and resolution review.
- `src/civicfix/ai.py` — Gemini/Ollama analysis and before/after comparison.
- `src/civicfix/models.py` — validated analysis, issue and resolution contracts.
- `src/civicfix/priority.py` — deterministic score and freshness decay.
- `config/scoring.json` — priority weights and freshness interval.
- `src/civicfix/deduplication.py` — category, distance and lexical candidate matching.
- `src/civicfix/issue_service.py` — score and duplicate workflow helpers.
- `src/civicfix/storage.py` — SQLite schema, migration and persistence.
- `src/civicfix/routing.py`, `config/routes.json` — authority routing configuration.

## Contributions

Open an issue describing the change, keep model/provider code behind the existing adapter boundary, and avoid committing `.env`, the SQLite database, or real citizen photos. For jurisdiction changes, include the source used to verify each official route.
