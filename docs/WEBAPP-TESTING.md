# Test the connected app

Follow the setup in the root README, then run `npm run dev:all` from `webapp/`.
Keep that terminal open and visit **http://127.0.0.1:3000**. The standalone
hackathon folder supports the same command with the backend in `.backend/`.
Configure Supabase Auth and the server-side OpenAI API key in `.env.local`.

1. Choose **My space**, select a role, then **Continue**. The next step should
   show the white Google sign-in button and email account form. Use the small
   link below the submit button to switch between sign-in and registration.
   Create a disposable test account; with confirmations disabled it should open
   your workspace immediately. Sign out and back in to check persistence.
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

Both AI surfaces use the app's OpenAI API account. Users do not sign into ChatGPT.
Paper search itself uses the dedicated repository pipeline. Google login requires
the OAuth setup in [the guide](../webapp/docs/GOOGLE-SIGN-IN.md).

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

The website is public at **https://garra-rufa.vercel.app**. Vercel hosts the Next
app and a protected Python research service. Supabase stores hosted workspace
records and authenticates users; OpenAI API billing powers the assistant.

After deploying, run from `webapp/`:

```sh
GARRA_TEST_ORIGIN=https://garra-rufa.vercel.app GARRA_TEST_AI=1 node --env-file=.env.local scripts/test-account-auth.mjs
```

This creates and cleans up two synthetic accounts. It checks signup, role
persistence, login, immediate logout, record isolation, CSRF rejection, and a real
paper answer. Google OAuth needs the owner's provider credentials before its
interactive flow can be tested. GitHub pushes do not deploy automatically; use
`vercel deploy --prod` from `webapp/`.
