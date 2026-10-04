"""Verify quote presence with whitespace normalization only; never fuzzy-match meaning."""
import re


class Verifier:
    def __init__(self, doc):
        self.passages = {p.id: re.sub(r"\s+", " ", p.text).strip() for p in doc.passages}

    def check(self, quote: str, passage: str) -> str | None:
        quote = re.sub(r"\s+", " ", quote).strip()
        if len(quote.split()) < 5:
            return None
        pid = passage.strip().strip("[]").split("|")[0].strip().upper()
        # Require the cited location; do not silently relocate or repair a quote.
        if pid in self.passages and quote in self.passages[pid]:
            return pid
        return None
