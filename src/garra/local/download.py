"""Bulk files behind the local indexes, downloaded into data/local/raw/<group>/.

Downloads resume (.part + Range) and a file whose size matches the server's is skipped,
so re-running continues where it stopped. Directory-style sources (Open Targets parquet
folders) are listed at run time. manifest.json in each group records url, bytes, time.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from garra.local import RAW

UA = {"User-Agent": "garra-rufa-local/1 (research use)"}
CHUNK = 1 << 20
OBO = "https://purl.obolibrary.org/obo/"
OT_RELEASE = "26.09"
OT = f"https://ftp.ebi.ac.uk/pub/databases/opentargets/platform/{OT_RELEASE}/output/"
ORPHA = "https://www.orphadata.com/data/xml/"
GTEX = "https://storage.googleapis.com/adult-gtex/"
PUBTATOR = "https://ftp.ncbi.nlm.nih.gov/pub/lu/PubTator3/"
CLINVAR = "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/"
ORPHA_CLASSIFICATIONS = (146, 147, 148, 150, 152, 156, 181, 182, 183, 184, 185, 186, 187, 188,
                         189, 193, 194, 195, 196, 197, 198, 199, 200, 201, 202, 203, 204, 205,
                         209, 212, 216, 231, 233, 235)
# Open Targets datasets read by the local GraphQL emulation (opentargets.py)
OT_DATASETS = ("target", "disease", "disease_phenotype", "drug_molecule",
               "drug_mechanism_of_action", "drug_warning", "clinical_indication",
               "clinical_target", "association_overall_direct", "interaction", "reactome",
               "go", "mouse_phenotype", "pharmacogenomics", "baseline_expression", "biosample",
               "homology", "target_safety_event", "chemical_probe", "target_tractability",
               "openfda_significant_adverse_drug_reactions", "evidence_europepmc")


@dataclass(frozen=True)
class File:
    group: str
    url: str
    name: str


def _files() -> dict[str, list[File]]:
    g: dict[str, list[File]] = {}

    def add(group, url, name=None):
        g.setdefault(group, []).append(File(group, url, name or url.rsplit("/", 1)[-1]))

    for f in ("mondo.obo", "doid.obo", "uberon.obo", "cl.obo", "go.obo", "chebi/chebi_lite.obo",
              "hp.obo", "fma.owl"):
        add("ontology", OBO + f)
    add("ontology", "https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/"
                    "hgnc_complete_set.txt")
    add("mesh", "https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/desc2026.xml")
    # supplementary concept records (C ids: most rare diseases), gzipped XML
    add("mesh", "https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/supp2026.gz")
    for p in ("en_product1.xml", "en_product4.xml", "en_product6.xml", "en_product7.xml",
              "en_product9_ages.xml", "en_product9_prev.xml"):
        add("orphadata", ORPHA + p)
    for c in ORPHA_CLASSIFICATIONS:
        add("orphadata", f"{ORPHA}en_product3_{c}.xml")
    for f in ("phenotype.hpoa", "genes_to_phenotype.txt", "genes_to_disease.txt"):
        add("hpo", f"{OBO}hp/hpoa/{f}")
    add("monarch", "https://data.monarchinitiative.org/monarch-kg/latest/monarch-kg.tar.gz")
    add("clinvar", CLINVAR + "tab_delimited/variant_summary.txt.gz")
    add("clinvar", CLINVAR + "tab_delimited/var_citations.txt")
    add("gtex", GTEX + "bulk-gex/v10/rna-seq/GTEx_Analysis_v10_RNASeQCv2.4.2_gene_median_tpm.gct.gz")
    add("gtex", GTEX + "bulk-qtl/v10/single-tissue-cis-qtl/GTEx_Analysis_v10_eQTL.tar")
    add("gtex", GTEX + "bulk-qtl/v10/single-tissue-cis-qtl/GTEx_Analysis_v10_sQTL.tar")
    add("gtex", GTEX + "references/v10/reference-tables/"
                       "GTEx_Analysis_2021-02-11_v10_WholeGenomeSeq_953Indiv.lookup_table.txt.gz")
    add("gtex", "https://gtexportal.org/api/v2/dataset/tissueSiteDetail?datasetId=gtex_v10"
                "&itemsPerPage=1000", "tissueSiteDetail.json")
    add("hpa", "https://www.proteinatlas.org/download/proteinatlas.json.gz")
    for f in ("disease2pubtator3.gz", "gene2pubtator3.gz", "chemical2pubtator3.gz",
              "mutation2pubtator3.gz", "relation2pubtator3.gz"):
        add("pubtator", PUBTATOR + f)
    add("clinicaltrials", "https://clinicaltrials.gov/api/v2/studies/download?format=json.zip",
        "ctg-studies.json.zip")
    return g


def _ot_files() -> list[File]:
    out = []
    for ds in OT_DATASETS:
        html = _open(OT + ds + "/").read().decode()
        for name in sorted(set(re.findall(r'href="([^"/?]+\.parquet)"', html))):
            out.append(File("opentargets", f"{OT}{ds}/{name}", f"{ds}/{name}"))
    return out


def groups() -> list[str]:
    return [*_files(), "opentargets"]


def _open(url: str, start: int = 0, tries: int = 6):
    for i in range(tries):
        headers = dict(UA)
        if start:
            headers["Range"] = f"bytes={start}-"
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                          timeout=120)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or i == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if i == tries - 1:
                raise
        time.sleep(2 ** i)


_lock = threading.Lock()
_done_bytes = 0


def fetch(f: File) -> tuple[File, int, str]:
    """Download one file (resuming). Returns (file, bytes, status)."""
    global _done_bytes
    dest = RAW / f.group / f.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    if dest.is_file() and dest.stat().st_size > 0:
        return f, dest.stat().st_size, "present"
    have = part.stat().st_size if part.is_file() else 0
    for attempt in range(5):
        try:
            resp = _open(f.url, have)
            if have and resp.status != 206:  # server ignored Range: start over
                have = 0
            with resp, open(part, "ab" if have else "wb") as out:
                while chunk := resp.read(CHUNK):
                    out.write(chunk)
                    have += len(chunk)
                    with _lock:
                        _done_bytes += len(chunk)
            part.replace(dest)
            return f, have, "downloaded"
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            if attempt == 4:
                return f, have, f"FAILED: {e}"
            time.sleep(5 * (attempt + 1))
            have = part.stat().st_size if part.is_file() else 0
    return f, have, "FAILED"


def _record(results: list[tuple[File, int, str]]) -> None:
    by_group: dict[str, dict] = {}
    for f, n, status in results:
        if status.startswith("FAILED"):
            continue
        man = by_group.setdefault(f.group, _load_manifest(f.group))
        man[f.name] = {"url": f.url, "bytes": n,
                       "fetched": man.get(f.name, {}).get("fetched") if status == "present"
                       else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    for group, man in by_group.items():
        (RAW / group / "manifest.json").write_text(json.dumps(man, indent=1, sort_keys=True))


def _load_manifest(group: str) -> dict:
    p = RAW / group / "manifest.json"
    return json.loads(p.read_text()) if p.is_file() else {}


def download(only: list[str] | None = None, workers: int = 6) -> int:
    files = [f for grp, fs in _files().items() if not only or grp in only for f in fs]
    if not only or "opentargets" in only:
        files += _ot_files()
    print(f"{len(files)} files", file=sys.stderr)
    t0 = time.time()
    results, failed = [], 0

    def ticker(stop: threading.Event):
        while not stop.wait(15):
            el = time.time() - t0
            print(f"  ... {_done_bytes / 1e9:.2f} GB in {el / 60:.1f} min "
                  f"({_done_bytes / el / 1e6:.1f} MB/s)", file=sys.stderr, flush=True)

    stop = threading.Event()
    threading.Thread(target=ticker, args=(stop,), daemon=True).start()
    with ThreadPoolExecutor(workers) as ex:
        for fut in as_completed([ex.submit(fetch, f) for f in files]):
            f, n, status = fut.result()
            results.append((f, n, status))
            failed += status.startswith("FAILED")
            if status != "present":
                print(f"{status:10} {n / 1e6:9.1f} MB  {f.group}/{f.name}", file=sys.stderr,
                      flush=True)
    stop.set()
    _record(results)
    print(f"done: {len(results) - failed} ok, {failed} failed, "
          f"{_done_bytes / 1e9:.2f} GB new in {(time.time() - t0) / 60:.1f} min",
          file=sys.stderr)
    return 1 if failed else 0


def raw(group: str, name: str) -> Path:
    return RAW / group / name


def raw_dir(group: str) -> Path:
    return RAW / group
