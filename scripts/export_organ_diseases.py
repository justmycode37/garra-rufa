"""Export, per atlas body region, every disease with an HPO-annotated finding in that organ.

Source: the local HPO release (src/query-test/sources/_hpoa.py: hp.obo + phenotype.hpoa +
genes_to_disease.txt in data/hpo/). A disease is listed for a region when one of its
phenotype annotations is the region's HPO term (webapp/src/lib/body-regions.json) or a
descendant of it. OMIM, Orphanet and DECIPHER records with the same name are merged (`ids`, only when there
are several; `id` is the first, OMIM before ORPHA before DECIPHER).

Ranking: the expected number of organ findings (sum of the annotation frequencies of the
matched terms; unknown frequency counts 0.5), then the highest single frequency.

Output: webapp/public/organ-diseases/<region>.json, read by the atlas disease list
(webapp/src/components/OrganDiseases.tsx). Re-run after changing body-regions.json or
refreshing data/hpo/:  python scripts/export_organ_diseases.py
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "query-test"))
from sources import _hpoa  # noqa: E402

REGIONS = ROOT / "webapp/src/lib/body-regions.json"
OUT = ROOT / "webapp/public/organ-diseases"
TOP_FEATURES = 4
TOP_GENES = 4
ID_ORDER = {"OMIM": 0, "ORPHA": 1, "DECIPHER": 2}


def frequency_label(f: float) -> str:
    if f == _hpoa.UNKNOWN_FREQ:
        return "unknown"
    if f >= 1.0:
        return "obligate"
    if f >= 0.8:
        return "very frequent"
    if f >= 0.3:
        return "frequent"
    return "occasional"


def name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def main():
    hpo = _hpoa.load()
    if hpo is None:
        sys.exit("HPO data unavailable (data/hpo/)")
    genes: dict[str, list[str]] = {}
    for symbol, links in hpo.g2d.items():
        for dis, assoc in links:
            if _hpoa.causal(assoc):
                genes.setdefault(dis, []).append(symbol)
    regions = json.loads(REGIONS.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    index = {}
    for region_id, region in regions.items():
        root = hpo.canonical(region["hpo"])
        if root is None:
            print(f"skip {region_id}: unknown term {region['hpo']}", file=sys.stderr)
            continue
        subtree = hpo.descendants(root)
        merged: dict[str, dict] = {}
        for dis, terms in hpo.ann.items():
            hits = {t: f for t, f in terms.items() if t in subtree}
            if not hits:
                continue
            name = hpo.dname.get(dis, dis)
            entry = merged.setdefault(name_key(name), {"ids": [], "name": name, "hits": {}, "genes": []})
            entry["ids"].append(dis)
            for t, f in hits.items():
                entry["hits"][t] = max(entry["hits"].get(t, 0), f)
            entry["genes"] += [g for g in genes.get(dis, []) if g not in entry["genes"]]
        diseases = []
        for entry in merged.values():
            hits = entry["hits"]
            ids = sorted(entry["ids"], key=lambda i: (ID_ORDER.get(i.split(":")[0], 9), i))
            # most frequent, then most specific findings first
            top = sorted(hits, key=lambda t: (-hits[t], -hpo.ic(t), hpo.name[t]))
            peak = max(hits.values())
            diseases.append({
                "id": ids[0], **({"ids": ids} if len(ids) > 1 else {}), "name": entry["name"],
                "score": round(sum(hits.values()), 2), "features": len(hits),
                "frequency": frequency_label(peak),
                "top": [hpo.name[t] for t in top[:TOP_FEATURES]],
                "genes": entry["genes"][:TOP_GENES],
                "_peak": peak,
            })
        diseases.sort(key=lambda d: (-d["score"], -d["_peak"], d["name"].lower()))
        for d in diseases:
            del d["_peak"]
        payload = {"region": region_id, "label": region["label"], "hpo": root,
                   "hpo_label": hpo.name[root], "scope": region.get("scope"),
                   "source": "Human Phenotype Ontology annotations (phenotype.hpoa)",
                   "exported": date.today().isoformat(), "total": len(diseases),
                   "diseases": diseases}
        path = OUT / f"{region_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        index[region_id] = len(diseases)
        print(f"{region_id:16} {len(diseases):6} diseases  {path.stat().st_size // 1024:6} KB")
    (OUT / "index.json").write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
