# garra rufa

A rare disease discovery workspace with a minimal, animated landing page and researcher, doctor, and patient perspectives. The design reuses Bloom’s local fonts, sprout mark, and colour palette.

## Run locally

Requires Node.js 22.13 or newer (native `node:sqlite`) and Python 3.11 or newer.
From the repository root, set up the backend once:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[research]'
cd webapp
npm ci
# Copy .env.example to .env.local only if no .env.local exists.
npm run dev:all
```

Open **http://127.0.0.1:3000**. `dev:all` starts the Python research service and
Next.js together. In the original standalone hackathon folder, it also detects a
backend checkout in `.backend/`. Set `GARRA_BACKEND_DIR` if your backend lives
elsewhere. Existing `.env.local`, accounts, and local data should be preserved.
Workspace AI uses the user's ChatGPT plan; no API key is required for that flow.

Click **My space** or **Sign in**, choose a role, then **Continue with ChatGPT**. The workspace requires a verified ChatGPT sign-in; guest workspace creation and legacy password sign-in are disabled. Both entry points use the same role selector. Existing guest records remain on disk and can be adopted by a new ChatGPT registration from the same browser, but guest sessions cannot access the workspace.

Landing-page AI questions use the app's server-side OpenAI API key, including when an already-signed-in user returns to the landing page. Workspace AI questions use that user's ChatGPT plan only, with no fallback to API billing. Private records and attachments are accepted only through authenticated workspace requests.

ChatGPT subscription access uses OpenAI's documented **local-app** flow. Open the app at **http://127.0.0.1:3000**, not `localhost`. The app dynamically registers itself, so no OAuth client secret or pre-provisioned client ID is needed for local use. Public/paid hosting requires separate approval from OpenAI; this implementation deliberately disables the local OAuth/inference flow on non-loopback origins. See [OpenAI's subscription access documentation](https://developers.openai.com/siwc/token-sharing-open-source) and [hosted sign-in requirements](https://developers.openai.com/siwc/website).

The app stores the stable host ID, browser-scoped registration list, encrypted tokens and one-time OAuth transactions in SQLite. It verifies state, PKCE, signature, issuer, audience, expiry and nonce, uses the verified issuer/client/subject as identity, and never merges accounts by email. Token refresh is serialized, rotating tokens are saved together, and signing out attempts revocation before finishing locally. Session cookies are HTTP-only; OAuth secrets are never stored in browser storage. The loopback HTTP flow is restricted to local use.

Sign-in without the plan-use scope still authenticates the account, but cannot perform workspace inference. Plan policy failures and usage limits appear as clear notices; the plan status and **Manage usage** link appear only beneath the workspace Home prompt; they never switch to API billing. The account menu displays the signed-in identity and sign-out action, without repeating sign-in prompts throughout the workspace.

## Current experience

- Sparse black 3D graphs use exclusively straight edges. A complete human figure keeps a permanent anatomical silhouette, with elliptical front/back anatomy, continuous 3D rotation, gentle breathing, and evolving short internal connections; there is no pointer control. Reduced-motion preferences are respected.
- A common workspace shell provides global search, an assistant, recent chats, projects, documents, research papers, and community. Doctors also have patient records; patients have a journey view.
- Individual conversations can be deleted from the sidebar; **Clear chat history** removes all chats in the current workspace after confirmation, without removing other records.
- Projects and notes persist in SQLite. PDF, PNG, JPG, WebP, text, and CSV uploads support private storage, downloads, and explicit attachment to assistant requests.
- **Related condition** accepts existing conditions or a new name. Saving a new condition creates its own community; normalized names and known aliases reuse the same community. Communities have opt-in profiles, conversations, and condition-specific published research. Members can join several communities and delete their own posts. Saving a private record does not join a community or share its contents.
- Files are sent to OpenAI only when attached to a submitted question. Searching other workspace records requires enabling **My workspace**.
- Landing-page search uses `gpt-6-luna` through the API. Workspace requests select `gpt-6-astra` when it appears in the signed-in account’s live model catalog, otherwise the first visible available model. Subscription inference uses streaming Responses requests with `store: false`; incomplete and failed streams are never treated as answers.
- Voice input is available in every composer. Tap the microphone to start dictation immediately; words appear live in the question field alongside your existing draft. Tap again to stop, then edit and send normally. Escape cancels and restores the original draft. Browser speech recognition needs no application API key and may process speech through the browser vendor’s service; cancellation cannot undo that processing. Chrome and Safari support dictation; Safari may require Siri enabled. Browsers without speech recognition can use the optional ElevenLabs fallback: tap to record, tap to stop and automatically transcribe. Garra Rufa does not save the audio. Dictation stops after two minutes and requires HTTPS or loopback.
- AI answers show retrieved sources. Provider failures fall back to explicitly labelled database search; unavailable literature is reported rather than replaced with invented papers.

## Data and architecture

`src/lib/knowledge.ts` is the reproducible seed collection: eight curated condition records (CMT, Ehlers-Danlos, Fabry, Marfan, Huntington, SMA, Pompe, Rett), with source URLs, associated genes, mechanisms, features, and patient organization links. There is no separate seed command: importing this module loads the same collection. Expand the array to add reviewed records. It is a focused prototype, not a comprehensive rare disease database.

`src/lib/ai.ts` gives the model read-only tools for the connected Python repository:

- `search_knowledge` runs the repository graph adapters (MONDO, Monarch, ClinicalTrials.gov).
- `find_contacts` resolves source-listed Orphanet/ERN resources and related trials. It preserves the distinction between a centre, organisation, trial, and individual doctor.
- `find_papers` uses the separate `src/query-test/literature` pipeline: PubMed, Europe PMC, PubTator, and LitVar where the query type applies, including metadata enrichment and identifier deduplication. Exact PMID and rsID searches are supported. A recent matching graph can supply additional literature queries and cited publications.
- `search_workspace` reads only the current user's owned/shared records when explicitly enabled.

`src/lib/research.ts` calls the local Python bridge, validates its response, and
passes verified source records to the model. Retrieved records appear in compact
grey-green cards directly after the citing paragraphs. The Research papers page
uses the same literature pipeline through `/api/research/papers`. Cards show a bold title and short subtitle; full identifiers, authors, abstracts,
and retrieval dates remain in the stored source records for grounding and follow-ups.
Missing contact fields remain absent.

The adapters use downloaded local indexes when available, then live public
sources. GitHub contains the code; it does not host a running database service.
This setup has not downloaded the complete bulk datasets. Interactive searches
are bounded samples, with 65-second worker timeouts and three concurrent workers.
Public results have a short memory cache; the literature provider cache expires
after 24 hours. Provider failures and verification pages are reported as partial
or unavailable results. They are never converted into invented records or a
claim that no evidence exists. Publication and sharing require explicit UI actions.

`src/lib/store.ts` stores users, sessions, records, sharing grants, uploads, and request limits in SQLite. Record contents and file bytes use AES-256-GCM encryption. Session tokens are hashed; legacy passwords use scrypt. Authorization checks apply to every private record and download, with guest sessions rejected at the HTTP boundary. The landing artwork is decorative and does not represent individual clinical records.

`src/lib/conditions.ts` maintains the persistent condition/community registry, aliases, memberships, and shared posts. User-added condition names are community categories, separate from the eight reviewed medical reference records. Community profiles and posts are visible to people using the platform; documents and saved notes require their existing explicit sharing flows.

## Configuration and hosting

- `OPENAI_API_KEY`: server-only API credential for landing-page AI only. Never used for workspace inference.
- `OPENAI_SEARCH_MODEL`: defaults to `gpt-6-luna`.
- `OPENAI_WORKSPACE_MODEL`: preferred ChatGPT plan model; defaults to `gpt-6-astra`, subject to the user’s live catalog.
- `ELEVENLABS_API_KEY`: optional server-only credential with speech-to-text access. If absent, the microphone uses browser dictation without an app API key.
- `ELEVENLABS_STT_MODEL`: defaults to `scribe_v2`.
- `GARRA_RESEARCH_URL`: server-only backend origin, defaults to `http://127.0.0.1:8787`.
- `GARRA_BACKEND_DIR` / `GARRA_PYTHON`: optional backend checkout and interpreter paths for `dev:all`.
- `GARRA_RESEARCH_CACHE_DIR`: optional Python cache directory, defaults to backend `data/literature-cache`.
- `GARRA_DATA_DIR`: persistent database directory; defaults to `./data`.
- `DATA_ENCRYPTION_KEY`: required stable 64-character hex key for hosted Supabase storage; optional locally. If omitted, a local key is generated in the data directory. Preserve the key with the database.
- `APP_ORIGIN`: leave unset for local use, or use the exact `http://127.0.0.1:<port>` origin. A non-loopback deployment origin disables local ChatGPT sign-in and subscription inference.

```sh
npm run build
# Keep npm run dev:backend running in another terminal.
npm start
```

For the local demo, keep the server bound to loopback and persist `GARRA_DATA_DIR` outside the deployment bundle. For public hosting, first obtain OpenAI hosted access approval and implement its registered OAuth flow, then use HTTPS and a canonical `APP_ORIGIN`. Local development uses SQLite; Vercel uses the Supabase persistence adapter. Vercel projects are prepared; configuring their server secrets and completing a verified deployment are still required. Real clinical use requires further identity, governance, consent, retention, and operational work beyond this prototype.

## Checks

```sh
npm run typecheck
npm test
npm run build
```

Tests cover session/password handling, private-record isolation, explicit read-only sharing, publication boundaries, encryption integrity, reasoning routing, guest-session isolation, OAuth identity validation, callback replay rejection, encrypted token storage, rotating refreshes, account separation, subscription stream completion, billing-path isolation, canonical condition creation, transaction rollback, community memberships, and post ownership.


## Supabase connection

The connected cloud project is `mhmbyajftzuyqffpajfb` (Europe / Zurich).
Set `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` in
`.env.local`; see `.env.example`. The publishable key is intended for client use.
Never use a Supabase secret or service-role key in a `NEXT_PUBLIC_` variable.

Run `npm run check:supabase` to verify both the Database and Auth APIs without
creating users or writing any data. Supabase project management is also available
through the authenticated Supabase plugin.

- Browser components: `createSupabaseBrowserClient()` from `src/lib/supabase/client.ts`.
- Public server queries: `createSupabaseServerClient()` from `src/lib/supabase/server.ts`.

The `garra_*` tables and restricted server functions are defined in
`supabase/migrations/20261004042901_garra_hosted_storage.sql` and applied to this
project. Runtime callers use the async boundary in `src/lib/persistence.ts`.
`GARRA_STORAGE=supabase` selects cloud persistence; local development defaults to
SQLite. Vercel refuses SQLite so a cold start cannot silently lose workspace data.
No local accounts, tokens, uploads, or records are automatically copied to Supabase.

Set server-only `SUPABASE_SECRET_KEY` and a stable `DATA_ENCRYPTION_KEY` on the host.
All cloud tables have RLS enabled and browser grants revoked. There are deliberately
no browser policies: the app validates ChatGPT identities, manages its own hashed
sessions, and checks ownership/sharing before returning decrypted data. A ChatGPT
identity is not assumed to be a Supabase Auth user. Supabase's informational
[no-policy advisor](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy)
is expected for this server-only access model. Service-role credentials stay on the server.

Run the live synthetic-data contract checks with:

```sh
node --env-file=.env.local --import tsx scripts/test-hosted-storage.ts
```

The checks create unique test users and records, verify isolation, sharing,
encryption, one-time OAuth attempts, concurrent refresh/rate limits, and denial
of browser access; they remove only their own synthetic records in `finally`.

Two Vercel projects are prepared: `garra-rufa` for this Next app and
`garra-rufa-research` for the Python repository backend. The backend's
`api/index.py` reuses the existing graph and dedicated paper pipelines. Set the
same private `GARRA_RESEARCH_TOKEN` on both services and set `GARRA_RESEARCH_URL`
on the web app to the deployed backend's HTTPS origin. The Python build fetches
the two public HPO files needed by the anatomical index; disposable provider
caches live in `/tmp`. No private workspace data goes to that service.

The hosted app does not enable the local dynamic-registration ChatGPT OAuth flow.
For public ChatGPT-plan usage, complete OpenAI's
[hosted-app interest form](https://openai.com/form/sign-in-with-chatgpt-interest/)
and integrate the client registration/scopes granted through that process.
Do not reuse a local dynamic client registration as a public hosted app.
The current deployment configuration intentionally does not provide an OpenAI
API key; it cannot silently switch workspace requests to API billing.
