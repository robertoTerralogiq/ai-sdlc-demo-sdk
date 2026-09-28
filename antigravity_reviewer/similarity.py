"""Title normalization and the near-duplicate predicate for findings.

Gemini reworks the wording of a finding's title between runs and between
batches, so two comments describing the same problem rarely share an exact
title. This module normalizes a title down to a token set and an identifier
set, and defines when two findings are close enough to be treated as the
same problem rather than two different ones.

The rule is two-window. Within `NEAR_DELTA` lines it tolerates loose
rewording: a shared-token overlap or jaccard bar, same as before. Beyond
that and out to `FAR_DELTA` it demands a subset-strength token match
(`overlap >= FAR_MIN_OVERLAP`), because the far window exists only to catch
the case where the model cites a different line for the same problem
between runs, and loose wording similarity alone is not enough evidence at
that distance.

An identifier gate sits alongside the token bar: when both titles name a
code symbol (a backticked span, or a bare word with an underscore or an
inner capital) and those identifier sets are disjoint, the pair is rejected
outright regardless of token similarity. This is what keeps `find_discount`
from merging with `find_orders_with_discount`. Beyond `NEAR_DELTA`, a title
that names a symbol may also not merge with one that names none at all.

Thresholds and the identifier gate were validated against 18 labeled pairs
drawn from live GitLab MR !1 and MR !3, scoring 18/18. The one known
remaining miss is "In-memory redemption counter is not durable or safe for
concurrency" vs "In-memory dictionary for redemptions is not scalable or
persistent" at the same line, which shares only `memory` and `redemption`
(overlap 0.40, jaccard 0.22) -- under both the near-window bars.
"""

from __future__ import annotations

import re
from typing import NamedTuple, Optional

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_BACKTICKED_RE = re.compile(r"`([^`]+)`")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_INNER_CAP_RE = re.compile(r"[a-z][A-Z]")

_STOPWORDS = {
    "a", "an", "the", "is", "are", "not", "no", "in", "on", "of", "to", "for",
    "and", "or", "when", "if", "does", "do", "can", "could", "should", "be",
    "used", "use", "using", "all", "from", "with", "without", "potential",
    "potentially", "this", "that", "it", "its", "into", "at", "by", "as",
}

_MIN_TOKEN_LEN = 3
_MIN_STEM_LEN = 3
_SUFFIXES = ("ing", "ies", "ed", "es", "s")
_VOWELS = set("aeiou")

# Near-duplicate thresholds, validated against 18 labeled pairs from MR !1
# and MR !3 (see module docstring).
NEAR_DELTA = 1
FAR_DELTA = 6
MIN_SHARED_TOKENS = 2
MIN_OVERLAP = 0.5
MIN_JACCARD = 0.25
FAR_MIN_OVERLAP = 1.0


def _collapse_double(token: str) -> str:
    # Applied unconditionally, not only after a suffix was stripped: doing it
    # conditionally would make "call" and "calls" stem to different tokens
    # ("call" untouched, "calls" -> "call" -> collapsed again to "cal").
    if len(token) > 3 and token[-1] == token[-2] and token[-1] not in _VOWELS:
        return token[:-1]
    return token


def _stem(token: str) -> str:
    for suffix in _SUFFIXES:
        # Guard the remainder, not the input length: "logs" must reach "log"
        # the same way "logged" does, or a 4-letter plural never matches its
        # singular.
        if token.endswith(suffix) and len(token) - len(suffix) >= _MIN_STEM_LEN:
            token = token[: -len(suffix)]
            break
    return _collapse_double(token)


def title_tokens(title: str) -> set:
    """Normalize a finding title into a set of comparable content tokens."""
    tokens = set()
    for raw in _TOKEN_RE.findall(title.lower()):
        if len(raw) < _MIN_TOKEN_LEN:
            continue
        if raw in _STOPWORDS:
            continue
        tokens.add(_stem(raw))
    return tokens


def identifiers(title: str) -> set:
    """Code symbols named in a title: backticked spans and camel/snake words."""
    found = set()
    for span in _BACKTICKED_RE.findall(title):
        span = span.strip()
        if span:
            found.add(span.lower())
    stripped = _BACKTICKED_RE.sub(" ", title)
    for word in _WORD_RE.findall(stripped):
        # Only a genuine code shape counts: an underscore, or a capital that
        # is not the first letter. A capitalised English word ("Floating",
        # "Missing") or an all-caps word ("API", "SQL") is not an identifier.
        if "_" in word or _INNER_CAP_RE.search(word):
            found.add(word.lower())
    return found


class Signature(NamedTuple):
    file: str
    line: Optional[int]
    tokens: set
    identifiers: set


def signature(file: str, line: Optional[int], title: str) -> Signature:
    """Build a `Signature` once per finding, computing tokens and identifiers."""
    return Signature(file=file, line=line, tokens=title_tokens(title), identifiers=identifiers(title))


def is_near_duplicate(a: Signature, b: Signature) -> bool:
    """True when two findings describe the same problem.

    Rejected outright for different files, an unknown line on either side,
    or a line delta beyond `FAR_DELTA`. Otherwise the titles must share at
    least `MIN_SHARED_TOKENS` tokens, and the identifier gate applies: if
    both titles name identifiers and those sets are disjoint, this returns
    False regardless of similarity.

    Within `NEAR_DELTA` lines, `overlap >= MIN_OVERLAP or jaccard >=
    MIN_JACCARD` is enough. Beyond that and out to `FAR_DELTA`, the titles
    must also agree on whether they name any identifier at all, and the
    token overlap must be subset-strength (`overlap >= FAR_MIN_OVERLAP`).
    """
    if a.file != b.file or a.line is None or b.line is None:
        return False
    delta = abs(a.line - b.line)
    if delta > FAR_DELTA:
        return False

    shared = a.tokens & b.tokens
    if len(shared) < MIN_SHARED_TOKENS:
        return False

    smaller = min(len(a.tokens), len(b.tokens))
    union = a.tokens | b.tokens
    overlap = len(shared) / smaller if smaller else 0.0
    jaccard = len(shared) / len(union) if union else 0.0

    if a.identifiers and b.identifiers and not (a.identifiers & b.identifiers):
        # Both name code symbols and they disagree: different bugs, whatever
        # the wording similarity says.
        return False

    if delta <= NEAR_DELTA:
        return overlap >= MIN_OVERLAP or jaccard >= MIN_JACCARD

    # Beyond the near window the titles must agree much harder, and a title
    # that names a symbol may not be merged with one that names none.
    if bool(a.identifiers) != bool(b.identifiers):
        return False
    return overlap >= FAR_MIN_OVERLAP
