"""Named switches for the pipeline improvements of runs/study/FINDINGS.md (R1-R9).

Every improvement is ON by default and guarded in the code by `if improvements.on(name)`;
with all of them off (GARRA_IMPROVEMENTS=none) the pipeline behaves exactly as before.

  input_identity     R1 build.py: the input's xref-id nodes are merged into it; disease
                     nodes whose label is a qualified form of an input name ("late-onset
                     Pompe disease", "Niemann-Pick disease type C1") or the unqualified
                     parent of the input ("Rett syndrome" for "Atypical Rett syndrome")
                     count as the input (direct, bridge 1.0)
  canonicalize       R2 build.py: one key per concept (case, Greek letters, salt /
                     formulation suffixes; drug and therapy together), readable labels
                     (no ALL CAPS), plus an LLM pass grouping same-concept labels per kind
  paper_scoring      R3 build.py: candidate score = noisy-OR over papers (best path per
                     paper) x evidence tier (clinical / replicated / single preclinical);
                     n_papers, best_level, tier per candidate
  generic_penalty    R4 build.py: ubiquitous techniques (sequencing, MRI, western blot, ...)
                     x GENERIC_W and flagged generic; transfer outcome measures not bridged
                     by a shared phenotype / process x OTHER_ENDPOINT_W
  gap_check          R5 gaps.py: a gap is "tested" when a direct candidate has the same
                     modality on the same target; literature check also with a core name;
                     gaps of one canonical intervention merged
  query_hygiene      R6 transfer.py / registries.py / evidence main: names sanitised
                     before Europe PMC / ClinicalTrials.gov, thin transfer searches retried
                     without the setting group, warnings for 0-paper mechanisms and
                     registry failures
  symptom_anchor     R7 resolve.py / main.py: --symptoms items that are HPO phenotypes are
                     never pinned as diseases; ranking deduplicated across ORPHA / OMIM /
                     MONDO; focus deduplicated and gated (coverage >= 2/3, score >= 0.85 of
                     the top); the anchor is printed
  symptom_axis       R8 literature/plan.py + evidence: phenotype-level treatment queries in
                     symptom mode; typed symptoms bridge with SYMPTOM_W
  diverse_selection  R9 evidence/screen.py: full-text selection prefers papers that add a
                     solution not covered yet (relevance order kept within a level)

Overrides: environment variable GARRA_IMPROVEMENTS or --improvements SPEC on main.py,
literature/main.py and evidence/main.py (the flag wins). SPEC is a comma list read left to
right from "all on": "none" / "all" reset, "-name" switches one off, "+name" or "name" on:
  GARRA_IMPROVEMENTS=none                      old behaviour
  --improvements=-canonicalize,-gap_check      all but these two (note the "=": argparse
                                               reads a value starting with "-" as an option;
                                               "all,-canonicalize,-gap_check" works without)
  --improvements none,+input_identity          only this one
"""
import os

FLAGS = ("input_identity", "canonicalize", "paper_scoring", "generic_penalty", "gap_check",
         "query_hygiene", "symptom_anchor", "symptom_axis", "diverse_selection")
ENV = "GARRA_IMPROVEMENTS"

_state: dict[str, bool] = {}


def parse(spec: str | None) -> dict[str, bool]:
    """Flag -> on for a SPEC (see module docstring); unknown names raise ValueError."""
    state = dict.fromkeys(FLAGS, True)
    for tok in (spec or "").replace(" ", "").split(","):
        if not tok:
            continue
        if tok in ("all", "none"):
            state = dict.fromkeys(FLAGS, tok == "all")
            continue
        val, name = (tok[0] != "-", tok[1:]) if tok[0] in "+-" else (True, tok)
        if name not in state:
            raise ValueError(f"unknown improvement {name!r}; known: {', '.join(FLAGS)}")
        state[name] = val
    return state


def configure(spec: str | None) -> dict[str, bool]:
    """Set the state from SPEC (None: from $GARRA_IMPROVEMENTS); returns it."""
    _state.clear()
    _state.update(parse(os.environ.get(ENV) if spec is None else spec))
    return dict(_state)


def on(name: str) -> bool:
    if name not in FLAGS:
        raise KeyError(name)
    if not _state:
        configure(None)
    return _state[name]


def active() -> dict[str, bool]:
    """Every flag and whether it is on (for the output JSON)."""
    if not _state:
        configure(None)
    return dict(_state)


def spec() -> str:
    """The current state as a SPEC string ("all", "none" or "all,-x,-y")."""
    st = active()
    if all(st.values()):
        return "all"
    if not any(st.values()):
        return "none"
    off = [k for k, v in st.items() if not v]
    on_ = [k for k, v in st.items() if v]
    return ("all," + ",".join("-" + k for k in off) if len(off) <= len(on_)
            else "none," + ",".join("+" + k for k in on_))


def add_argument(ap) -> None:
    ap.add_argument("--improvements", metavar="SPEC",
                    help="switch the FINDINGS.md improvements: 'none', 'all', 'all,-x,-y' "
                         "(or --improvements=-x,-y: all but), 'none,+x' (only); names: "
                         + ", ".join(FLAGS)
                         + f" (default: ${ENV} or all)")


def apply_args(args) -> dict[str, bool]:
    """Configure from args.improvements (else the environment); the effective SPEC is
    written back to args.improvements and the environment (child processes see it)."""
    try:
        configure(getattr(args, "improvements", None))
    except ValueError as e:
        raise SystemExit(f"--improvements: {e}")
    args.improvements = spec()
    os.environ[ENV] = args.improvements
    return active()
