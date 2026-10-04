# Test the connected app

Follow the setup in the root README, then run `npm run dev:all` from `webapp/`.
Keep that terminal open and visit **http://127.0.0.1:3000**. The original
standalone hackathon folder supports the same command with the backend checkout
in `.backend/`. Use 127.0.0.1, because the local ChatGPT authorization callback
requires that exact loopback host.

1. Choose **My space**, select your role, then **Continue with ChatGPT**. Complete
   OpenAI's authorization screen and permit plan usage. The workspace should say
   **Using ChatGPT plan**. Plan limits are respected; failures do not spend an API key.
2. Ask **Find two research papers about Marfan syndrome and explain what each studies.**
   The assistant calls the dedicated literature pipeline. Each cited paper should
   appear as a compact card after its paragraph, with a bold truncated title, journal/year
   subtitle, and original source link. Open a link and compare the title/PMID.
3. Ask **What does the repository say about FBN1 and Marfan syndrome?**
   Check the graph source cards and distinguish direct from broader relationships.
4. Ask **Find source-listed expert centres for Marfan syndrome.** The app should
   show only records actually returned, keep organisations/trials distinct from
   individual doctors, and explain unavailable contact providers.
5. Open **Research papers** and search **Marfan syndrome**, then
   **PMID:38517496**. The latter should retrieve the actual identified paper.
   Try **Explore with AI** and verify the follow-up has the paper's identity.
6. Reload a saved conversation. Its cards and publication metadata should remain.

The initial landing-page assistant retains its separate, optional app API-key
billing. To test only ChatGPT-plan billing, enter the workspace before asking.
No API key is needed for workspace questions or the Research papers search.

Provider checks on 2026-10-04 returned real MONDO/Monarch/ClinicalTrials.gov
records and literature from PubMed/Europe PMC/PubTator. Orphanet returned an
interactive connection-verification page. The adapter reports that as a provider
failure; it cannot claim to have retrieved a doctor or centre from that page.
ERN disease lookup depends on the same Orphanet directory. Available records
remain visible with a partial-results notice. Search coverage is bounded and does
not represent the entire bulk database.

## Automated checks

```sh
# repository root
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_research_bridge.py' -v
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_ui_bridge.py' -v
cd webapp
npm run typecheck
npm test
npm run build
```

For a production-build local test, keep `npm run dev:backend` running in one
terminal, run `npm run build && npm start` in another, and visit the same URL.
Stop the development frontend first to free port 3000. Persist the webapp data
directory and its encryption key to retain accounts and workspaces.

## Public hosting

The implemented ChatGPT-plan flow is OpenAI's local/open-source application flow.
It is deliberately disabled on public origins. A public Vercel launch with user
ChatGPT plans requires OpenAI's hosted application approval/registration and the
corresponding OAuth integration. See [OpenAI's documentation](https://developers.openai.com/siwc/token-sharing-open-source)
and [hosted sign-in requirements](https://developers.openai.com/siwc/website).

A hosted product also needs a persistent backend for the Python pipeline and a
durable database/identity setup for encrypted workspaces. The current local
SQLite store is not a shared Vercel serverless database. No public deployment is
claimed by these local checks.
