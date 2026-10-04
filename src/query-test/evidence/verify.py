"""Check that an LLM evidence quote really is in the paper.

Text and quote are compared as sequences of lower-case alphanumeric tokens (unicode
normalised, so dashes, spaces, ligatures, Greek letters and markup do not matter). A quote
is verified when it occurs in the cited passage, else anywhere in the document, either
exactly or with >= MIN_MATCH of its tokens matched in order inside one window (the model
sometimes drops a citation marker or fixes a typo). Quotes shorter than MIN_TOKENS are not
evidence.
"""
import re
import unicodedata
from difflib import SequenceMatcher

MIN_TOKENS = 5
MIN_MATCH = 0.9
TOKEN = re.compile(r"[a-z0-9]+")


def tokens(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return TOKEN.findall(s)


def _fuzzy(q: list[str], t: list[str]) -> bool:
    n = len(q)
    starts = set()
    for k in range(min(4, n)):  # anchors: any of the first tokens, shifted back
        starts.update(i - k for i, x in enumerate(t) if x == q[k])
    for k in range(1, min(4, n) + 1):  # or the last ones (start of quote garbled)
        starts.update(i - n + k for i, x in enumerate(t) if x == q[-k])
    for s in sorted(starts):
        win = t[max(0, s - 3): max(0, s) + n + 6]
        m = SequenceMatcher(None, q, win, autojunk=False)
        if sum(b.size for b in m.get_matching_blocks()) >= MIN_MATCH * n:
            return True
    return False


def _contains(q: list[str], t: list[str]) -> bool:
    return " " + " ".join(q) + " " in " " + " ".join(t) + " "


class Verifier:
    def __init__(self, doc):
        self.passages = {p.id: tokens(p.text) for p in doc.passages}
        self.order = list(self.passages)

    def check(self, quote: str, passage: str) -> str | None:
        """Passage id where the quote was found, or None."""
        q = tokens(quote)
        if len(q) < MIN_TOKENS:
            return None
        pid = passage.strip().strip("[]").split("|")[0].strip().upper()
        if pid in self.passages and (_contains(q, self.passages[pid])
                                     or _fuzzy(q, self.passages[pid])):
            return pid
        for p in self.order:
            if _contains(q, self.passages[p]):
                return p
        for p in self.order:  # quote spanning two passages is rare; check one by one
            if _fuzzy(q, self.passages[p]):
                return p
        return None
