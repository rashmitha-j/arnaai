# ArnaAI — Your Companion for Mental Wellbeing

An AI wellbeing companion that combines **multi-modal emotion detection** (text sentiment + facial expression), a **structured CBT dialogue manager**, an **LLM therapy engine built with LangChain + Gemini**, and **human-in-the-loop escalation** to trained counselors. Built as microservices with Next.js and Flask, deployable to Google Cloud Run.

> ⚠️ ArnaAI is a supportive tool, not a medical device or a replacement for professional care. Before any real-world use, have the safety flows and crisis resources reviewed by qualified mental-health professionals and check the regulations where you operate.

## Architecture

```
 Browser (Next.js)                         Google Cloud Run
 ┌──────────────────────────┐   /api/*   ┌──────────────────────────┐   ID token   ┌──────────────────────┐
 │ Chat UI + insight panel  │──────────▶ │ arnaai-therapy (Flask)   │────────────▶ │ arnaai-emotion       │
 │ face-api.js on webcam ─┐ │  BFF proxy │  • risk assessment       │              │ (Flask, private)     │
 │ (video stays on device)│ │            │  • CBT dialogue manager  │              │  • VADER + lexicon   │
 │ sends 7-number         ◀─┘            │  • LangChain → Gemini    │              │  • text/face fusion  │
 │ expression average       │            │  • escalation service    │              │  • EMA smoothing     │
 │ Counselor dashboard      │            │  • encrypted storage ────┼──▶ Firestore   └──────────────────────┘
 └──────────────────────────┘            └──────────┬───────────────┘
                                                    └──▶ webhook alert (Google Chat / Slack), ids only
```

### How a turn works
1. **Face (optional, consented):** `face-api.js` samples the webcam every 400 ms in the browser. When the user sends a message, the average expression distribution over those frames is attached. Raw video never leaves the device.
2. **Risk assessment** runs on every message with deterministic rules (never depends on the LLM). An LLM safety review can only *raise* the level. Sustained moderate risk across turns escalates.
3. **Emotion fusion** (emotion service): text emotions (VADER valence + negation-aware lexicon) and face expressions share one label space and are fused with adaptive weights, smoothed over turns (EMA) into a profile: dominant emotion, valence, arousal, **distress + trend**, and **incongruence** (words vs. face disagree, e.g. "I'm fine" with a sad face).
4. **High/imminent risk →** escalation is created, Arna is paused, a fixed (non-LLM) safety message with crisis lines is sent, and a counselor takes over the same conversation. **Moderate risk →** flagged for asynchronous counselor review while Arna keeps supporting.
5. **CBT dialogue manager** (explicit state machine for a Beck thought record): check-in → situation → automatic thought → emotion rating → thinking traps → evidence → balanced thought → re-rate → action step → close. Slots are filled by LLM structured extraction (heuristic fallback). Cognitive distortions are detected (all-or-nothing, catastrophizing, labeling, …). **Adaptive delivery:** acute distress diverts to a grounding exercise; "I just want to vent" diverts to supportive listening; both resume where they left off.
6. **LangChain response chain** (Gemini) writes the reply, guided by the stage goal, the thought record so far and the live emotional profile. If the LLM fails, an offline generator answers so the user is never left hanging.

### Secure data handling
- Message text, CBT notes, emotion profiles and counselor notes are **encrypted at rest** (Fernet) before reaching the database; keys live in Secret Manager.
- Per-session bearer tokens (stored hashed); counselor dashboard behind its own token; service-to-service auth (Google ID tokens on Cloud Run + shared key); emotion service is private.
- Facial data is used only with explicit consent, and is ignored server-side without it. Users can delete their conversation at any time (hard delete).
- Rate limiting, input size limits, security headers, escalation alerts contain no conversation content.

## Project layout
```
services/emotion/     Flask emotion microservice (text analysis + multi-modal fusion)
services/therapy/     Flask therapy engine (LangChain, CBT manager, risk, escalation, storage)
frontend/             Next.js 16 app: landing, chat, counselor dashboard, /api proxy
deploy/gcp/           Cloud Run deploy script + Cloud Build CI/CD
docker-compose.yml    Run the whole stack locally
```

## Run locally

**Prerequisites:** Python 3.11+, Node 20+, a Gemini API key from https://aistudio.google.com/apikey (optional: without one, set `LLM_PROVIDER=offline` for template replies).

```bash
cp .env.example .env            # add GOOGLE_API_KEY and a COUNSELOR_TOKEN
# Both Flask services read the root .env themselves (python-dotenv).

# 1) emotion service  (terminal 1)
cd services/emotion && pip install -r requirements.txt
PORT=8081 python app.py

# 2) therapy engine   (terminal 2)
cd services/therapy && pip install -r requirements.txt
PORT=8080 python app.py

# 3) frontend         (terminal 3)
cd frontend && npm install
THERAPY_API_URL=http://127.0.0.1:8080 npm run dev
```
Open http://localhost:3000 for the chat and http://localhost:3000/counselor for the counselor dashboard (sign in with `COUNSELOR_TOKEN`).

**On Windows, one command:** after installing the dependencies (ideally into a `.venv` virtualenv in the project root, which the script picks up automatically), run `start.cmd` from the project root or double-click it. It opens the emotion service, therapy engine and frontend in three separate console windows; close a window to stop that service.

> Service-to-service URLs use `127.0.0.1` rather than `localhost`: on Windows, `localhost` resolves to IPv6 first and every call waits ~2 s before falling back to IPv4.
>
> The free Gemini API tier allows only a few requests per minute (and per day) for each model. Each chat turn makes two calls: slot extraction + safety review on `EXTRACTION_MODEL` (default `gemini-3.5-flash-lite`) and the reply on `GEMINI_MODEL`. Because quotas are per model, splitting them spreads the load. When a quota runs out, Arna automatically falls back to template replies (`model: offline-templates` in the message metadata) until it resets. `LLM_TIMEOUT_SECONDS` (default 20) and `LLM_MAX_RETRIES` (default 1) bound how long a turn can wait on Gemini.

**Or with Docker:** `cp .env.example .env` (fill it in), then `docker compose up --build`.

### Try the human-in-the-loop flow
1. Open the chat in one window and the counselor dashboard in another.
2. Type a message that expresses risk. Arna pauses and shows crisis lines; an *Urgent* case appears in the dashboard.
3. Reply as the counselor. The message appears in the user's chat within a few seconds.
4. Resolve the case and choose "hand back to Arna"; the AI resumes.

## Tests
```bash
cd services/emotion && python -m pytest -q     # text analysis, fusion, smoothing
cd services/therapy && python -m pytest -q     # risk levels, CBT flow, API, escalation, encryption, prompts
```

## Deploy to Google Cloud
```bash
gcloud auth login
export GOOGLE_API_KEY=...        # optional; without it the engine uses Vertex AI Gemini
./deploy/gcp/deploy.sh YOUR_PROJECT_ID asia-south1
```
The script enables the APIs, creates Firestore, Artifact Registry, least-privilege service accounts and Secret Manager secrets, builds the three images with Cloud Build and deploys them to Cloud Run. It prints the live URL and how to read the counselor access code. For CI/CD, connect your GitHub repo to `deploy/gcp/cloudbuild.yaml`.

## Configuration
See `.env.example`. Key settings: `LLM_PROVIDER` (`gemini` | `vertex` | `offline`), `GEMINI_MODEL` (replies, default `gemini-3.8-flash`), `EXTRACTION_MODEL` (extraction + safety review, default `gemini-3.5-flash-lite`), `LLM_TIMEOUT_SECONDS`, `STORAGE_BACKEND` (`sqlite` | `firestore`), `DATA_ENCRYPTION_KEY`, `COUNSELOR_TOKEN`, `ESCALATION_WEBHOOK_URL`, `CRISIS_RESOURCES_JSON` (localise helplines).

## Ideas for next steps
- Speech prosody as a third modality (Cloud Speech-to-Text + pitch/energy features)
- Counselor accounts with Identity Platform instead of a shared access code
- Pub/Sub + on-call paging for critical escalations
- Session summaries and mood trends across conversations (with consent)
- Clinical review and red-teaming of the risk rules with professionals
