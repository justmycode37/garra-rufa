"""Small UI menu; HPO IDs/labels verified against hp/releases/2026-09-01.

Region assignment and plain-language help are application choices, not HPO
anatomical assertions. This starter menu has not received clinical review.
"""

from copy import deepcopy

REGIONS = [
    {"id": "head", "label": "Head / nervous system"},
    {"id": "eyes", "label": "Eyes"},
    {"id": "ears", "label": "Ears"},
    {"id": "chest", "label": "Chest / breathing"},
    {"id": "abdomen", "label": "Abdomen"},
    {"id": "arms", "label": "Arms / hands"},
    {"id": "legs", "label": "Legs / feet"},
    {"id": "whole_body", "label": "Whole body / other"},
]
# (ID, ontology label, UI label, help, discoverable regions)
_TERMS = [
    (
        "HP:0001324",
        "Muscle weakness",
        "Muscle weakness",
        "Reduced muscle strength; different from feeling tired.",
        ["arms", "legs", "whole_body"],
    ),
    (
        "HP:0001252",
        "Hypotonia",
        "Low muscle tone",
        "Reduced resistance when a muscle is stretched; choose if this finding has been identified, not simply because you feel weak.",
        ["arms", "legs", "whole_body"],
    ),
    (
        "HP:0003394",
        "Muscle spasm",
        "Muscle spasms or cramps",
        "Sudden involuntary muscle contractions.",
        ["arms", "legs", "whole_body"],
    ),
    (
        "HP:0001288",
        "Gait disturbance",
        "Difficulty walking",
        "A change or difficulty in how you walk.",
        ["legs"],
    ),
    (
        "HP:0001260",
        "Dysarthria",
        "Difficulty articulating speech",
        "Difficulty producing clear speech, not difficulty finding words.",
        ["head"],
    ),
    (
        "HP:0001250",
        "Seizure",
        "Seizures",
        "Choose for an identified seizure, rather than assuming unexplained movements are seizures.",
        ["head"],
    ),
    (
        "HP:0000505",
        "Visual impairment",
        "Visual impairment",
        "Significant loss of visual ability; ordinary refractive error corrected by glasses is not this finding.",
        ["eyes"],
    ),
    (
        "HP:0000365",
        "Hearing impairment",
        "Reduced hearing",
        "Reduced ability to hear sounds.",
        ["ears"],
    ),
    (
        "HP:0002094",
        "Dyspnea",
        "Breathing difficulty",
        "A feeling of difficult or labored breathing.",
        ["chest"],
    ),
    ("HP:0002018", "Nausea", "Nausea", "An uneasy stomach with an urge to vomit.", ["abdomen"]),
    (
        "HP:0012378",
        "Fatigue",
        "Fatigue",
        "A feeling of tiredness or lack of energy; different from reduced muscle strength.",
        ["whole_body"],
    ),
]
SYMPTOMS = [
    {
        "id": i,
        "hpo_label": h,
        "label": label,
        "description": desc,
        "regions": regions,
        "source_url": f"https://hpo.jax.org/browse/term/{i}",
    }
    for i, h, label, desc, regions in _TERMS
]
IDS = {s["id"] for s in SYMPTOMS}


def get_catalog():
    return deepcopy(
        {
            "schema_version": 1,
            "version": "body-menu-v1",
            "hpo_release": "2026-09-01",
            "source_url": "https://raw.githubusercontent.com/obophenotype/human-phenotype-ontology/master/hp.obo",
            "review_status": "ontology_ids_verified_ui_mapping_requires_clinical_review",
            "regions": REGIONS,
            "symptoms": SYMPTOMS,
            "instructions": "Select regions to browse, then confirm individual symptoms. Region clicks are not phenotype findings. This is a limited research-discovery menu, not a diagnostic tool.",
        }
    )
