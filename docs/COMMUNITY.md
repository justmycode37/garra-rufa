# Community layer

The existing Python UI bridge now serves three separate public catalog sections:

- `disease`: communities organized around explicit disease identifiers.
- `mechanism`: cross-disease groups organized around explicit biological process identifiers.
- `project`: collaboration projects linked to one or more existing communities/groups.

This implements backend discovery and a UI contract. It does not modify the separately hosted frontend. There are no real community records until a curated catalog is supplied. The default response retains all three sections with empty item lists.

## Run

```sh
PYTHONPATH=src python3 -m garra.ui --communities examples/communities.demo.json
```

The example is explicitly fictional (`demo: true`), including its owner and project. It must be displayed with a Demo badge and must not be presented as an existing patient organization or validated cross-disease connection. Remove the option to serve an empty production catalog, or pass a curated JSON file using the same schema with `demo: false`.

## UI contract

- `GET /api/communities`: returns `sections` in disease, mechanism, project order; each has `kind`, `label`, `items`, and `count`.
- `GET /api/communities?kind=disease&disease_id=MONDO%3A0009290`: filter by exact disease identifier.
- `GET /api/communities?kind=mechanism&process_id=GO%3A0005980`: filter by exact process identifier.
- `GET /api/communities/{id}`: public record, with `project_ids` linking a community to projects. A project carries its parent `community_ids`.

Optional filters combine with AND. Unknown identifiers return empty lists; unknown, repeated, or invalid filters return 400. Unknown detail IDs return 404. Existing origin restrictions also apply to these routes.

Render three Community tabs using `sections[].label`. Cards show name, description, topic IDs, and `match_reasons`. An empty section should say “No communities listed for this topic yet.” Counts mean catalog records, never member counts. Respect `capabilities`: join, post, and create-project actions are currently disabled.

From a disease result, pass its canonical ID to `disease_id`. From a graph connection, let the user explicitly select a shared process ID for `process_id`. Do not send body coordinates, patient details, or symptom lists. Filtering is exact ID matching; reconcile aliases through the existing identity bridge first. No similarity threshold determines community membership.

## Catalog fields and research links

Top level: `schema_version: 1`, boolean `demo`, and `records`.
All records require `id` (lowercase slug), `kind`, `name`, `description`, `disease_ids`, `process_ids`, and `evidence`. Disease groups require a disease ID; mechanism groups require a process ID. Namespaced IDs must be verified by the catalog curator; syntax validation is not ontology verification.

Each optional evidence link has `claim_id`, namespaced `publication_id`, HTTP(S) `url`, and `review_status: unreviewed`. Use claim and publication IDs from the evidence graph to open exact passages, context, limitations, and contradictions in the evidence view. Catalog links alone do not verify a claim, resolve publication aliases, or establish a shared mechanism. The community API deliberately does not promote them to reviewed evidence.

Projects additionally require `community_ids`, `owner` (public organization or team name), `goal`, `access_conditions`, nonempty `reuse_checks`, `next_step`, `status`, and `asset_urls`. Status is `proposed`, `seeking_collaborators`, `active`, or `completed`. Asset links can be empty when an asset has yet to be identified. These fields describe a proposed collaboration; they do not establish permission to access an asset or its suitability for another disease.

Catalogs are validated on startup, copied into memory, and read-only. Edit the catalog and restart to update it. Public text fields must contain only publishable information; validation cannot detect private information embedded in prose. Render text as text, never raw HTML.

## Remaining product work

The frontend must consume these routes and render the three sections. Deploy the backend behind a suitable application server/proxy before connecting the hosted UI; the development server binds only to localhost. Credentials remain server-side.

Authenticated, opt-in membership, posting, project creation, moderation, and private participant records require a separate persistent service with authorization. They are intentionally not exposed through this public API. Browsing or selecting symptoms never creates membership. Real project outcomes and time-to-asset measurements are needed before claiming a 10× improvement.
