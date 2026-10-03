"""Registry of sources this project is allowed to read.

A documented download path is not a source. Each row below was checked with a
real HTTP request on 2026-10-03. Bulk files are fetched only from these URLs.
Query APIs are called only for one disease at a time. Manual rows have no
bulk file; the fetcher refuses them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Access = Literal["bulk", "query", "manual"]

# PURL redirects for HPO land on short-lived GitHub release URLs. Those hosts
# are allowed only as redirects from an approved start URL, never as the
# citation URL stored in the catalog.
REDIRECT_HOST_SUFFIXES: tuple[str, ...] = (
    "github.com",
    "githubusercontent.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
)

MB = 1024 * 1024


@dataclass(frozen=True)
class Source:
    id: str
    access: Access
    publisher: str
    license: str
    url: str
    hosts: tuple[str, ...]
    role: str
    filename: str = ""
    expected: bytes = b""
    min_bytes: int = 1
    max_bytes: int = 80 * MB
    default: bool = False
    note: str = ""


def _bulk(
    source_id: str,
    *,
    publisher: str,
    license: str,
    url: str,
    hosts: tuple[str, ...],
    role: str,
    filename: str,
    expected: bytes,
    min_bytes: int,
    default: bool = True,
    max_bytes: int = 80 * MB,
    note: str = "",
) -> Source:
    return Source(
        id=source_id,
        access="bulk",
        publisher=publisher,
        license=license,
        url=url,
        hosts=hosts,
        role=role,
        filename=filename,
        expected=expected,
        min_bytes=min_bytes,
        max_bytes=max_bytes,
        default=default,
        note=note,
    )


BULK_SOURCES: dict[str, Source] = {
    "hpo_genes_to_disease": _bulk(
        "hpo_genes_to_disease",
        publisher="Human Phenotype Ontology consortium",
        license="Free to reuse; keep the HPO citation",
        url="https://purl.obolibrary.org/obo/hp/hpoa/genes_to_disease.txt",
        hosts=("purl.obolibrary.org",),
        role="Gene to disease edges, with the source database named on each row",
        filename="genes_to_disease.txt",
        expected=b"ncbi_gene_id",
        min_bytes=100_000,
        note="Stable PURL. It redirects to a GitHub release asset that expires; cite the PURL, not the redirect.",
    ),
    "hpo_disease_phenotype": _bulk(
        "hpo_disease_phenotype",
        publisher="Human Phenotype Ontology consortium",
        license="Free to reuse; keep the HPO citation",
        url="https://purl.obolibrary.org/obo/hp/hpoa/phenotype.hpoa",
        hosts=("purl.obolibrary.org",),
        role="Disease to phenotype edges, including evidence and frequency",
        filename="phenotype.hpoa",
        expected=b"database_id",
        min_bytes=1_000_000,
        note="About 35 MB. Header comments come before the database_id column.",
    ),
    "hpo_genes_to_phenotype": _bulk(
        "hpo_genes_to_phenotype",
        publisher="Human Phenotype Ontology consortium",
        license="Free to reuse; keep the HPO citation",
        url="https://purl.obolibrary.org/obo/hp/hpoa/genes_to_phenotype.txt",
        hosts=("purl.obolibrary.org",),
        role="Gene to phenotype edges",
        filename="genes_to_phenotype.txt",
        expected=b"ncbi_gene_id",
        min_bytes=1_000_000,
        default=False,
        note="About 20 MB. Off by default because disease-phenotype already covers the first demo.",
    ),
    "hpo_ontology": _bulk(
        "hpo_ontology",
        publisher="Human Phenotype Ontology consortium",
        license="Free to reuse; keep the HPO citation",
        url="https://purl.obolibrary.org/obo/hp.obo",
        hosts=("purl.obolibrary.org",),
        role="Symptom labels and the phenotype hierarchy",
        filename="hp.obo",
        expected=b"format-version:",
        min_bytes=1_000_000,
        default=False,
    ),
    "orphadata_diseases": _bulk(
        "orphadata_diseases",
        publisher="Orphanet / Inserm",
        license="CC BY 4.0",
        url="https://www.orphadata.com/data/xml/en_product1.xml",
        hosts=("www.orphadata.com",),
        role="Rare disease names, synonyms, OrphaCodes, and cross-references",
        filename="en_product1.xml",
        expected=b"<JDBOR",
        min_bytes=1_000_000,
        note="Release date is inside the JDBOR date attribute. Verified file date 2026-06-23.",
    ),
    "orphadata_phenotypes": _bulk(
        "orphadata_phenotypes",
        publisher="Orphanet / Inserm",
        license="CC BY 4.0",
        url="https://www.orphadata.com/data/xml/en_product4.xml",
        hosts=("www.orphadata.com",),
        role="Orphanet disease to HPO clinical signs",
        filename="en_product4.xml",
        expected=b"<JDBOR",
        min_bytes=1_000_000,
    ),
    "orphadata_genes": _bulk(
        "orphadata_genes",
        publisher="Orphanet / Inserm",
        license="CC BY 4.0",
        url="https://www.orphadata.com/data/xml/en_product6.xml",
        hosts=("www.orphadata.com",),
        role="Rare disease to gene associations",
        filename="en_product6.xml",
        expected=b"<JDBOR",
        min_bytes=1_000_000,
        note="About 22 MB. Verified file date 2026-06-23.",
    ),
    "orphadata_classification": _bulk(
        "orphadata_classification",
        publisher="Orphanet / Inserm",
        license="CC BY 4.0",
        url="https://www.orphadata.com/data/xml/en_product7.xml",
        hosts=("www.orphadata.com",),
        role="Disease to parent-disease classification edges",
        filename="en_product7.xml",
        expected=b"<JDBOR",
        min_bytes=100_000,
        default=False,
    ),
    "orphadata_natural_history": _bulk(
        "orphadata_natural_history",
        publisher="Orphanet / Inserm",
        license="CC BY 4.0",
        url="https://www.orphadata.com/data/xml/en_product9_ages.xml",
        hosts=("www.orphadata.com",),
        role="Age of onset and inheritance for a disease",
        filename="en_product9_ages.xml",
        expected=b"<JDBOR",
        min_bytes=100_000,
        default=False,
    ),
    "monarch_causal_gene_to_disease": _bulk(
        "monarch_causal_gene_to_disease",
        publisher="Monarch Initiative",
        license="Retain the knowledge-source column from each row",
        url="https://data.monarchinitiative.org/monarch-kg-dev/latest/tsv/all_associations/causal_gene_to_disease_association.all.tsv.gz",
        hosts=("data.monarchinitiative.org",),
        role="Causal gene to disease edges already normalized by Monarch",
        filename="causal_gene_to_disease_association.all.tsv.gz",
        expected=b"\x1f\x8b",
        min_bytes=50_000,
        note="The latest pointer moves. The manifest records the final URL and sha256 for the file actually saved.",
    ),
    "monarch_disease_to_phenotype": _bulk(
        "monarch_disease_to_phenotype",
        publisher="Monarch Initiative",
        license="Retain the knowledge-source column from each row",
        url="https://data.monarchinitiative.org/monarch-kg-dev/latest/tsv/all_associations/disease_to_phenotypic_feature_association.all.tsv.gz",
        hosts=("data.monarchinitiative.org",),
        role="Disease to phenotype edges",
        filename="disease_to_phenotypic_feature_association.all.tsv.gz",
        expected=b"\x1f\x8b",
        min_bytes=100_000,
    ),
    "monarch_gene_to_pathway": _bulk(
        "monarch_gene_to_pathway",
        publisher="Monarch Initiative",
        license="Retain the knowledge-source column from each row",
        url="https://data.monarchinitiative.org/monarch-kg-dev/latest/tsv/all_associations/gene_to_pathway_association.all.tsv.gz",
        hosts=("data.monarchinitiative.org",),
        role="Gene to pathway edges used to cluster diseases that share a mechanism",
        filename="gene_to_pathway_association.all.tsv.gz",
        expected=b"\x1f\x8b",
        min_bytes=100_000,
    ),
    "clinvar_gene_condition": _bulk(
        "clinvar_gene_condition",
        publisher="NCBI ClinVar",
        license="Public NCBI data; cite ClinVar",
        url="https://ftp.ncbi.nlm.nih.gov/pub/clinvar/gene_condition_source_id",
        hosts=("ftp.ncbi.nlm.nih.gov",),
        role="Gene to condition edges with Mondo, OMIM, and source identifiers",
        filename="gene_condition_source_id",
        expected=b"#GeneID\t",
        min_bytes=100_000,
        note="Use this 1 MB table. Do not start from variant_summary.txt.gz.",
    ),
    "clinvar_variant_summary": _bulk(
        "clinvar_variant_summary",
        publisher="NCBI ClinVar",
        license="Public NCBI data; cite ClinVar",
        url="https://ftp.ncbi.nlm.nih.gov/pub/clinvar/tab_delimited/variant_summary.txt.gz",
        hosts=("ftp.ncbi.nlm.nih.gov",),
        role="Variant-level clinical significance. Too large for the default pull.",
        filename="variant_summary.txt.gz",
        expected=b"\x1f\x8b",
        min_bytes=1_000_000,
        max_bytes=600 * MB,
        default=False,
        note="About 450 MB compressed. Refused unless fetch is called with allow_large=True.",
    ),
}


QUERY_SOURCES: dict[str, Source] = {
    "clinicaltrials": Source(
        id="clinicaltrials",
        access="query",
        publisher="U.S. National Library of Medicine, ClinicalTrials.gov",
        license="Public domain U.S. government work; link the study page",
        url="https://clinicaltrials.gov/api/v2/studies",
        hosts=("clinicaltrials.gov",),
        role="Studies and registries for one condition",
        note="No API key. Search one disease; do not download the full study catalog.",
    ),
    "nih_reporter": Source(
        id="nih_reporter",
        access="query",
        publisher="National Institutes of Health, RePORTER",
        license="Public NIH data; cite the project URL",
        url="https://api.reporter.nih.gov/v2/projects/search",
        hosts=("api.reporter.nih.gov",),
        role="Active grants, principal investigators, and institutions",
        note="POST JSON. No API key.",
    ),
    "pubmed": Source(
        id="pubmed",
        access="query",
        publisher="NCBI PubMed",
        license="NLM terms; cite the PMID",
        url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        hosts=("eutils.ncbi.nlm.nih.gov",),
        role="Papers and investigators for one disease",
        note="Set NCBI_EMAIL. NCBI asks callers to identify themselves.",
    ),
}


MANUAL_SOURCES: dict[str, Source] = {
    "orphanet_patient_orgs": Source(
        id="orphanet_patient_orgs",
        access="manual",
        publisher="Orphanet / Inserm",
        license="Not a public download",
        url="https://www.orphadata.com/docs/Catalogue_Orphadata.pdf",
        hosts=("www.orphadata.com",),
        role="Patient organisations and registries",
        note="On-request product. It needs a data-transfer agreement. This package does not download it.",
    ),
    "nord": Source(
        id="nord",
        access="manual",
        publisher="National Organization for Rare Disorders",
        license="Website terms; no bulk file",
        url="https://rarediseases.org/",
        hosts=("rarediseases.org",),
        role="Patient-group directory for one disease page",
        note="Open the disease page by hand. Do not crawl the directory.",
    ),
    "global_genes": Source(
        id="global_genes",
        access="manual",
        publisher="Global Genes",
        license="Website terms; no bulk file",
        url="https://globalgenes.org/",
        hosts=("globalgenes.org",),
        role="Patient-group directory",
        note="Open the disease page by hand. Do not crawl the directory.",
    ),
    "eurordis": Source(
        id="eurordis",
        access="manual",
        publisher="EURORDIS",
        license="Website terms; no bulk file",
        url="https://www.eurordis.org/",
        hosts=("www.eurordis.org",),
        role="European rare-disease alliance directory",
        note="Open the member page by hand. Do not crawl the directory.",
    ),
}


def all_sources() -> dict[str, Source]:
    merged = {**BULK_SOURCES, **QUERY_SOURCES, **MANUAL_SOURCES}
    if len(merged) != len(BULK_SOURCES) + len(QUERY_SOURCES) + len(MANUAL_SOURCES):
        raise RuntimeError("source ids overlap across catalogs")
    return merged


def validate_catalog() -> None:
    """Reject a registry that could fetch the wrong host or an empty stub."""
    from urllib.parse import urlparse

    for source in all_sources().values():
        host = urlparse(source.url).hostname
        if host not in source.hosts:
            raise ValueError(f"{source.id}: url host {host} is not in hosts {source.hosts}")
        if source.access == "bulk":
            if not source.filename or not source.expected:
                raise ValueError(f"{source.id}: bulk source needs a filename and an expected marker")
            if source.min_bytes < 1000:
                raise ValueError(f"{source.id}: min_bytes is too small to catch an empty stub")
            if source.max_bytes < source.min_bytes:
                raise ValueError(f"{source.id}: max_bytes is below min_bytes")
        if source.access == "manual" and source.default:
            raise ValueError(f"{source.id}: manual sources are not fetched")
        if source.id == "clinvar_variant_summary" and source.default:
            raise ValueError("clinvar_variant_summary must stay off by default")
