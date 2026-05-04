"""Step 3: classify content into a domain or theme.

fixed_domains mode (v1): rule-based keyword matching against each domain's
slug and description. Emergent mode (v1): reads theme candidates from vault
top-level folders and markdown frontmatter, then scores input by word
frequency (duplicates counted) so single-word themes break ties correctly.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

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

# Language-keyed stop-word lists. Used to prevent common connectors,
# pronouns, and articles from dominating tokenization-based scoring.
# Critical for emergent classification: without language-aware filtering,
# pt-BR braindumps surfaced pronouns like "eu" and conjunctions like
# "que" as proposed themes (Elio feedback 2026-05-04).
#
# Tokens are matched against the lowercased input. Both accented and
# unaccented forms are listed for languages that use diacritics so we
# handle either spelling.
_STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset({
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "had", "has", "have", "i", "in", "is", "it", "of", "on", "or",
        "the", "this", "that", "to", "was", "were", "will", "with",
        "but", "if", "so", "do", "does", "did", "no", "not",
        "me", "my", "we", "you", "your", "they", "them",
    }),
    "pt-BR": frozenset({
        # Articles
        "a", "o", "as", "os", "um", "uma", "uns", "umas",
        # Personal pronouns
        "eu", "tu", "ele", "ela", "nos", "nós", "vos", "vós", "eles", "elas",
        "me", "te", "se", "lhe", "lhes", "mim", "ti", "si",
        # Possessives
        "meu", "minha", "meus", "minhas",
        "seu", "sua", "seus", "suas",
        "nosso", "nossa", "nossos", "nossas",
        # Demonstratives
        "este", "esta", "estes", "estas", "isto",
        "esse", "essa", "esses", "essas", "isso",
        "aquele", "aquela", "aqueles", "aquelas", "aquilo",
        # Prepositions and contractions
        "de", "do", "da", "dos", "das",
        "em", "no", "na", "nos", "nas",
        "por", "pelo", "pela", "pelos", "pelas",
        "para", "pra", "com", "sem", "sob", "sobre", "ate", "até",
        # Conjunctions
        "e", "ou", "mas", "porém", "porem", "porque", "pois", "se",
        "que", "quando", "como", "onde", "ainda", "embora",
        # Common verbs (most-frequent indicative-mode forms)
        "ser", "estar", "ter", "ir", "haver", "fazer", "dizer",
        "sou", "somos", "são", "sao", "era", "foram", "foi", "fui",
        "estou", "está", "esta", "estamos", "estão", "estao", "estava",
        "tenho", "tem", "temos", "tinha",
        "vou", "vai", "vamos", "vão", "vao",
        "há", "ha", "havia", "houve",
        "faz", "fez",
        # Negation and affirmation
        "não", "nao", "sim", "talvez", "já", "ja",
        # Common adverbs
        "muito", "pouco", "mais", "menos",
        "agora", "depois", "antes", "sempre", "nunca",
        "aqui", "ali", "lá", "la", "também", "tambem", "então", "entao", "assim",
        # Question words
        "quem", "qual", "quais", "quanto", "quantos",
    }),
}
# pt is treated as an alias for pt-BR (overlap is sufficient for stopword purposes).
_STOPWORDS["pt"] = _STOPWORDS["pt-BR"]
_DEFAULT_STOPWORD_LANG = "en"

# Guardrails for proposing brand-new emergent themes from text alone
# (Elio feedback 2026-05-04). Without these, single-occurrence pronouns
# and short fragments slipped through stopword filtering and became
# proposed themes ("eu", "que"). With these:
# - tokens shorter than _MIN_THEME_WORD_LEN are rejected as candidates
#   (filters most slang, articles, and pronouns that fall below 4 chars)
# - candidates must appear at least _MIN_THEME_FREQUENCY times in the
#   text, so a topic-of-discussion has to be repeated to qualify
# Existing-theme matching (folder names, frontmatter) is unaffected
# because the user already validated those by naming them.
_MIN_THEME_WORD_LEN = 4
_MIN_THEME_FREQUENCY = 2


def _stopwords_for(language: str) -> frozenset[str]:
    return _STOPWORDS.get(language, _STOPWORDS[_DEFAULT_STOPWORD_LANG])


@dataclass(frozen=True)
class ClassificationResult:
    primary: str
    secondary: tuple[str, ...]
    confidence: float
    uncertain: bool
    mode: Mode


def _tokenize(text: str, language: str = _DEFAULT_STOPWORD_LANG) -> set[str]:
    stopwords = _stopwords_for(language)
    return {t for t in _WORD_RE.findall(text.lower()) if t not in stopwords}


def classify(text: str, config: Config) -> ClassificationResult:
    if config.mode == "emergent":
        return _classify_emergent(text, config)

    if not config.domains:
        raise ConfigError(
            "fixed_domains classify requires Config.domains to be non-empty"
        )

    input_tokens = _tokenize(text, config.language)

    scored: list[tuple[str, int]] = []
    for domain in config.domains:
        vocab = _tokenize(domain.slug + " " + domain.description, config.language)
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


# ---------------------------------------------------------------------------
# Emergent mode helpers
# ---------------------------------------------------------------------------

_SKIP_SYSTEM_PREFIXES = ("_", ".")

_FM_FENCE_RE = re.compile(r"\n---\s*(?:\n|$)")


def _parse_frontmatter_yaml(text: str) -> dict[str, object]:
    if not text.startswith("---"):
        return {}
    after_open = text[3:].lstrip("\r\n")
    close_match = _FM_FENCE_RE.search("\n" + after_open)
    if close_match is None:
        return {}
    yaml_text = ("\n" + after_open)[: close_match.start()]
    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _collect_emergent_themes(vault_path: Path) -> set[str]:
    """Return theme candidates from top-level vault folders and note frontmatter."""
    themes: set[str] = set()

    # Top-level directories (excluding system folders)
    try:
        for entry in vault_path.iterdir():
            if entry.is_dir() and not entry.name.startswith(_SKIP_SYSTEM_PREFIXES):
                themes.add(entry.name)
    except OSError:
        pass

    # Markdown file frontmatter theme values
    for root, dirs, files in os.walk(vault_path):
        dirs[:] = sorted(
            d for d in dirs if not d.startswith(_SKIP_SYSTEM_PREFIXES)
        )
        for fname in sorted(files):
            if not fname.endswith(".md"):
                continue
            path = Path(root) / fname
            try:
                text = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError):
                continue
            fm = _parse_frontmatter_yaml(text)
            theme_val = fm.get("theme")
            if isinstance(theme_val, str) and theme_val.strip():
                themes.add(theme_val.strip())

    return themes


def _propose_theme_from_text(text: str, language: str = _DEFAULT_STOPWORD_LANG) -> str:
    """Return most frequent significant token from text as a proposed theme name.

    Applies three guardrails (Elio feedback 2026-05-04):
    1. Stopword filter (language-aware) removes pronouns, articles, connectors.
    2. Minimum word length floor rejects short noise tokens.
    3. Minimum frequency floor requires the candidate to be repeated in the text.

    Returns "" when no token meets all three thresholds. Caller treats empty
    as a signal to ask the user to pick a theme rather than auto-proposing.
    """
    stopwords = _stopwords_for(language)
    words = [
        w for w in _WORD_RE.findall(text.lower())
        if w not in stopwords and len(w) >= _MIN_THEME_WORD_LEN
    ]
    if not words:
        return ""
    counter = Counter(words)
    top_word, top_count = counter.most_common(1)[0]
    if top_count < _MIN_THEME_FREQUENCY:
        return ""
    return top_word


def _classify_emergent(text: str, config: Config) -> ClassificationResult:
    themes = _collect_emergent_themes(config.vault_path)
    stopwords = _stopwords_for(config.language)

    if not themes:
        proposed = _propose_theme_from_text(text, config.language)
        return ClassificationResult(
            primary=proposed,
            secondary=(),
            confidence=0.0,
            uncertain=True,
            mode="emergent",
        )

    # Frequency-based scoring: count raw word occurrences (not deduped set)
    # so that a theme slug appearing multiple times in the input scores higher.
    # Single-word emergent themes have no description vocabulary to widen
    # the overlap window, so frequency breaks ties that set-intersection leaves
    # ambiguous.
    word_counts: Counter[str] = Counter(
        w for w in _WORD_RE.findall(text.lower()) if w not in stopwords
    )

    scored: list[tuple[str, int]] = []
    for theme in sorted(themes):
        vocab = _tokenize(theme, config.language)
        base_hits = sum(word_counts.get(t, 0) for t in vocab)
        bonus = _SLUG_BONUS if word_counts.get(theme.lower(), 0) > 0 else 0
        scored.append((theme, base_hits + bonus))

    scored.sort(key=lambda item: (-item[1], item[0]))

    primary_theme, primary_score = scored[0]
    runner_up_score = scored[1][1] if len(scored) > 1 else 0

    secondary = tuple(
        t for t, score in scored[1:]
        if score >= 1 and score >= primary_score * _SECONDARY_RATIO
    )

    if primary_score == 0:
        proposed = _propose_theme_from_text(text, config.language)
        return ClassificationResult(
            primary=proposed or primary_theme,
            secondary=(),
            confidence=0.0,
            uncertain=True,
            mode="emergent",
        )

    denom = max(_MIN_EVIDENCE, primary_score + runner_up_score)
    confidence = primary_score / denom
    threshold = config.classification_confidence_threshold
    return ClassificationResult(
        primary=primary_theme,
        secondary=secondary,
        confidence=confidence,
        uncertain=confidence < threshold,
        mode="emergent",
    )
