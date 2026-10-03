"""Self-contained, escaped HTML presentation of validated connection cards."""

from html import escape


def render_connections(result: dict) -> str:
    def e(value):
        return escape(str(value), quote=True)

    def link(url, label):
        return f'<a href="{e(url)}" target="_blank" rel="noopener noreferrer">{e(label)}</a>'

    def bullets(values):
        return "<ul>" + "".join(f"<li>{e(v)}</li>" for v in values) + "</ul>"

    cards = []
    for card in result["cards"]:
        evidence = {v["id"]: v for v in card["evidence"]}

        def references(ids):
            rows = []
            for ref in ids:
                ev = evidence[ref]
                passage = ev.get("passage")
                rows.append(
                    f"<li>{link(ev['url'], ev['source'])} · {e(ev['locator'])}"
                    + (
                        f"<blockquote>{e(passage)}</blockquote>"
                        if passage
                        else '<p class="muted">Source passage not supplied; inspect the linked record.</p>'
                    )
                    + f"<small>Retrieved: {e(ev['retrieved_at'])}</small></li>"
                )
            return "<ul>" + "".join(rows) + "</ul>"

        claims = []
        for claim in card["claims"]:
            claims.append(
                f"<details><summary>{e(claim['statement'])} <span class='tag'>{e(claim['status'].replace('_', ' '))}</span></summary>"
                + f"<p>Relationship: {e(claim['relationship'].replace('_', ' '))}</p>"
                + "<h4>Supporting evidence</h4>"
                + (
                    references(claim.get("supporting_evidence_ids", []))
                    if claim.get("supporting_evidence_ids")
                    else "<p>No supporting source supplied.</p>"
                )
                + (
                    "<h4>Contradictory evidence</h4>"
                    + references(claim["contradicting_evidence_ids"])
                    if claim.get("contradicting_evidence_ids")
                    else '<p class="muted">No contradictory evidence supplied; absence of a record is not confirmation.</p>'
                )
                + f"<p><strong>Context:</strong> {e(claim.get('context') or 'Not supplied')}</p>"
                + f"<p><strong>Limitations:</strong> {e(claim.get('limitations') or 'Not supplied; expert review required')}</p></details>"
            )
        assets = []
        for asset in card["assets"]:
            assets.append(
                f"<details><summary>{e(asset['name'])} <span class='tag'>{e(asset['kind'])}</span></summary>"
                + f"<p>Owner: {e(asset['owner'])}</p><p>Access: {e(asset['access_conditions'])}</p>"
                + link(asset["url"], "Open asset / contact owner")
                + "<h4>Check before reuse</h4>"
                + bullets(asset["reuse_checks"])
                + f"<p class='muted'>Relevant claims: {e(', '.join(asset['claim_ids']))}</p>"
                + references(asset["evidence_ids"])
                + "</details>"
            )
        papers = []
        for paper in card["papers"]:
            access = {
                "open_access": "Open access",
                "restricted": "Restricted full text",
                "unknown": "Full-text access unknown",
            }[paper["access"]]
            full = (
                " · " + link(paper["full_text_url"], "Read open-access full text")
                if paper.get("full_text_url")
                else ""
            )
            papers.append(
                f"<details><summary>{e(paper['title'])} <span class='tag'>{e(access)}</span></summary>"
                + link(paper["url"], "Open abstract / publisher")
                + full
                + f"<p>Supports or discusses: {e(', '.join(paper['linked_to']))}</p>"
                + f"<p>Study type: {e(paper.get('study_type') or 'Not supplied')}</p>"
                + f"<p>Model / population: {e(paper.get('population') or 'Not supplied')}</p>"
                + f"<p>Limitations: {e(paper.get('limitations') or 'Not supplied')}</p>"
                + references(
                    [v["id"] for v in card["evidence"] if paper["id"] in v.get("paper_ids", [])]
                )
                + "</details>"
            )
        coverage = (
            ", ".join(f"{k}: {v}" for k, v in sorted(card["coverage"].items())) or "Not supplied"
        )
        cards.append(
            f"<article><div class='card-head'><div><small>{e(card['id'])}</small><h2>{e(card['name'])}</h2></div>"
            + f"<div class='score'>{e(card['similarity']['display_label'])}<small>Phenotype similarity</small></div></div>"
            + f"<p>{e(card['why_shown'])}</p><p class='muted'>Coverage — {e(coverage)}</p>"
            + f"<p class='muted'>{link(card['similarity']['source_url'], 'Score source')} · Release {e(card['similarity']['release'])} · Retrieved {e(card['similarity']['retrieved_at'])}</p>"
            + bullets(card["warnings"])
            + "<h3>1. Biological connection</h3>"
            + (
                "".join(claims)
                or "<p>No evidence-linked claims supplied. The similarity result is a discovery hypothesis.</p>"
            )
            + "<h3>2. Potentially reusable research assets</h3>"
            + ("".join(assets) or "<p>No sourced assets supplied for this connection.</p>")
            + "<h3>3. Papers for clinical and research review</h3>"
            + ("".join(papers) or "<p>No claim-linked papers supplied.</p>")
            + "</article>"
        )
    banner = (
        '<div class="demo">SYNTHETIC DEMO — invented diseases, evidence, scores, and assets. Not research findings.</div>'
        if result["demo"]
        else ""
    )
    return (
        """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Research connections</title><style>
*{box-sizing:border-box}body{margin:0;background:#f4f6f5;color:#192d29;font:16px/1.6 system-ui,sans-serif}main{max-width:1000px;margin:40px auto;padding:0 24px}h1{font-size:30px;line-height:1.2}h2{font-size:23px;margin:4px 0}h3{font-size:18px;margin-top:28px}h4{font-size:15px}a{color:#11624b}article{background:white;border:1px solid #dce4e0;border-radius:12px;padding:28px;margin:24px 0}.card-head{display:flex;justify-content:space-between;gap:20px}.score{font-size:25px;font-weight:650;text-align:right;white-space:nowrap}.score small{display:block;font-size:12px;font-weight:400}.muted,small{color:#61716b}details{border-top:1px solid #dce4e0;padding:14px 0}summary{cursor:pointer;font-weight:550}.tag{font-size:11px;font-weight:500;display:inline-block;padding:2px 8px;background:#edf2ef;border-radius:4px;margin-left:6px}blockquote{margin:12px 0;padding:10px 16px;border-left:3px solid #adc4b9;background:#f5f8f6}.demo{padding:14px 20px;background:#fff0c7;color:#5b4317;font-weight:600}footer{font-size:13px;color:#61716b;margin-bottom:30px}@media(max-width:600px){main{padding:0 14px}.card-head{display:block}.score{text-align:left;margin-top:12px}article{padding:18px}h1{font-size:25px}}
</style>"""
        + banner
        + f"<main><header><small>RARE-DISEASE ATLAS / RESEARCH DISCOVERY</small><h1>Connections for {e(result['anchor']['name'])}</h1><p>Inspect why a disease is shown, what may be reusable, and the publications behind each claim.</p><p class='muted'>Similarity is a discovery index, not a probability of shared mechanism or treatment response.</p></header>"
        + ("".join(cards) or f"<article>{e(result['message'])}</article>")
        + f"<footer>{len(result['cards'])} results shown · {len(result['omitted'])} candidates omitted by identity, score, or result limit. Scores compare one provider, metric, and release. Asset suitability requires review.</footer></main></html>"
    )
