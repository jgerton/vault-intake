"""Step 3: classify content into a domain or theme.

fixed_domains mode (v1): rule-based keyword matching against each domain's
slug and description. Emergent mode raises NotImplementedError until the
emergent track ships.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .config import Config, ConfigError


Mode = Literal["fixed_domains", "emergent"]


# Minimum total evidence (primary + runner-up matches) before confidence
# can reach 1.0. With sparse hits, confidence is suppressed even when the
# primary is unchallenged.
_MIN_EVIDENCE = 5

# A non-primary domain qualifies as secondary when its score is at least
# this fraction of the primary score (and at least 1 hit).
_SECONDARY_RATIO = 0.4

# Bonus weight added when the input mentions the domain's slug literally.
# Treats explicit slug mentions as stronger evidence than description-only
# token overlap (a slug hit lands at 1 + _SLUG_BONUS = 3 total points).
_SLUG_BONUS = 2

# Match runs of Unicode letters (excludes digits, underscores, punctuation).
# Tokens are lowercased first so the regex never sees uppercase forms.
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# Minimal stop-word list to prevent common English connectors from
# spuriously matching domain descriptions during tokenization.
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "had", "has", "have", "i", "in", "is", "it", "of", "on", "or",
    "the", "this", "that", "to", "was", "were", "will", "with",
    "but", "if", "so", "do", "does", "did", "no", "not",
    "me", "my", "we", "you", "your", "they", "them",
})


@dataclass(frozen=True)
class ClassificationResult:
    primary: str
    secondary: tuple[str, ...]
    confidence: float
    uncertain: bool
    mode: Mode


def _tokenize(text: str) -> set[str]:
    return {t for t in _WORD_RE.findall(text.lower()) if t not in _STOPWORDS}


def classify(text: str, config: Config) -> ClassificationResult:
    if config.mode == "emergent":
        raise NotImplementedError(
            "emergent mode classify is not implemented in v1; "
            "use classification_mode: fixed_domains for now"
        )

    if not config.domains:
        raise ConfigError(
            "fixed_domains classify requires Config.domains to be non-empty"
        )

    input_tokens = _tokenize(text)

    scored: list[tuple[str, int]] = []
    for domain in config.domains:
        vocab = _tokenize(domain.slug + " " + domain.description)
        base_hits = len(input_tokens & vocab)
        slug_token = domain.slug.lower()
        bonus = _SLUG_BONUS if slug_token in input_tokens else 0
        scored.append((domain.slug, base_hits + bonus))

    # Stable sort by score descending; ties preserve config order so the
    # first-listed domain wins as the deterministic default.
    scored.sort(key=lambda item: -item[1])

    primary_slug, primary_score = scored[0]
    runner_up_score = scored[1][1] if len(scored) > 1 else 0

    secondary = tuple(
        slug
        for slug, score in scored[1:]
        if score >= 1 and score >= primary_score * _SECONDARY_RATIO
    )

    if primary_score == 0:
        confidence = 0.0
    else:
        denom = max(_MIN_EVIDENCE, primary_score + runner_up_score)
        confidence = primary_score / denom

    threshold = config.classification_confidence_threshold
    return ClassificationResult(
        primary=primary_slug,
        secondary=secondary,
        confidence=confidence,
        uncertain=confidence < threshold,
        mode="fixed_domains",
    )
