"""Step 7: extract candidate next-actions (gated by action signals).

Per build spec lines 171-182, this step scans the note body for action
signals and produces a seed list of candidate next-actions. The skill
orchestrator presents the list to the user for confirmation; v1 over-
supplies because spec line 175 calls this a seed list, not a committed
task list.

Five gate signals (rule-based v1, Option A; model-call v2 deferred):

1. Imperative: verb-first phrasing whose initial word is in the curated
   imperative-verb list.
2. Future intent: "we'll", "I should", "we need to", "going to", etc.
3. Date or deadline: ISO date, relative date phrase, day-of-week with
   prefix, "by" + month, "in N day(s)/week(s)/month(s)".
4. Decision point: "TBD", "we need to decide", "open question", etc.
5. Named follow-up: direct-address verb plus capitalized name, tool
   verb plus capitalized name, or delivery verb with "to <Capitalized>".

Mode-agnostic: spec lines 171-182 do not distinguish modes. The
extraction logic is content-driven, not vault-driven, so fixed_domains
and emergent share the same code path. This is a departure from
Steps 3-6 which all gate on mode.

Function-side behavior is unconditional. The skill orchestrator decides
whether to invoke `extract_next_actions()`; this module returns
`gate_fired=False` with empty proposals when no signals fire (rather
than raising), so the orchestrator can simply skip appending the
"Possíveis próximos passos" section.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .config import Config


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NextAction:
    what: str
    when: str | None
    where: str | None
    effort: str | None
    waiting_on: str | None
    signal: str
    source_excerpt: str


@dataclass(frozen=True)
class NextActionsResult:
    proposals: tuple[NextAction, ...]
    gate_fired: bool
    signals_detected: tuple[str, ...]

    def to_markdown(self) -> str:
        if not self.gate_fired or not self.proposals:
            return ""
        lines = ["## Possíveis próximos passos", ""]
        for proposal in self.proposals:
            parts = [f"- [What] {proposal.what}"]
            if proposal.where:
                parts.append(f"[Where: {proposal.where}]")
            if proposal.when:
                parts.append(f"[When: {proposal.when}]")
            if proposal.effort:
                parts.append(f"[Effort: {proposal.effort}]")
            if proposal.waiting_on:
                parts.append(f"[Waiting on: {proposal.waiting_on}]")
            parts.append(f"[Signal: {proposal.signal}]")
            lines.append(" ".join(parts))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Signal-detection patterns (locked v1 lists)
# ---------------------------------------------------------------------------


# Imperative verbs commonly seen in action notes. A sentence whose first
# significant word (after stripping bullet/list markers) is in this set
# fires the imperative signal. The set is deliberately narrow in v1 to
# avoid false positives; expand when dogfood surfaces misses.
_IMPERATIVE_VERBS: frozenset[str] = frozenset({
    # communication
    "call", "send", "email", "ping", "message", "dm", "text",
    "contact", "ask", "tell", "write", "forward",
    # creation and build
    "build", "ship", "deploy", "deliver", "fix", "draft",
    "post", "publish", "finalize", "schedule", "book", "set",
    # review and check
    "review", "check", "read", "watch", "test", "run",
    "validate", "verify", "confirm", "audit",
    # version control
    "merge", "commit", "push", "pull", "refactor", "rename",
    "delete", "remove", "add", "update",
    # work direction
    "decide", "prioritize", "complete", "finish", "start",
    "begin", "launch", "sort", "spike",
})


# Future-tense intent phrases, matched word-bounded and case-insensitive.
# The "going to" pattern is restricted to first-person and team-subject
# forms ("i'm going to", "i am going to", "we're going to", "we are going
# to"). Bare "going to" is excluded because it produces false positives
# on descriptive predictions like "The API is going to change."
_FUTURE_INTENT_RE = re.compile(
    r"\b("
    r"we'll need to|i'll need to|"
    r"we'll|we will|i'll|i will|"
    r"i should|we should|"
    r"i need to|we need to|"
    r"i have to|we have to|"
    r"i'm going to|we're going to|i am going to|we are going to|"
    r"i must|we must"
    r")\b",
    re.IGNORECASE,
)


# Date and deadline patterns. Two categories:
#
# 1. Deadline-bearing patterns (ISO date, "by Friday", "next week",
#    "in 3 days", "by EOW", etc.) imply explicit time-targeting and
#    fire the `date` signal on their own.
# 2. Deictic patterns ("today", "tonight", "tomorrow") describe a
#    moment without a deadline target. They populate the `when`
#    annotation but only count as a `date` signal when the same
#    sentence has another signal (imperative, future_intent,
#    decision_point, or named_followup). Spec line 173 calls for
#    skipping descriptive prose, so a bare deictic alone in a sentence
#    like "Today I learned about Python." does not fire the gate.
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_DEICTIC_DATE_RE = re.compile(
    r"\b(tomorrow|tonight|today)\b",
    re.IGNORECASE,
)
_DEADLINE_RELATIVE_RE = re.compile(
    r"\b("
    r"this\s+week|this\s+weekend|this\s+month|"
    r"next\s+week|next\s+weekend|next\s+month|"
    r"by\s+eow|by\s+eod|"
    r"by\s+end\s+of\s+(?:week|month|day)"
    r")\b",
    re.IGNORECASE,
)
_DAY_NAME_RE = re.compile(
    r"\b(?:by|on|next|this)\s+"
    r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
_BY_MONTH_RE = re.compile(
    r"\bby\s+("
    r"january|february|march|april|may|june|"
    r"july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
    r")\b",
    re.IGNORECASE,
)
_IN_N_PERIOD_RE = re.compile(
    r"\bin\s+\d+\s+(?:day|days|week|weeks|month|months)\b",
    re.IGNORECASE,
)

# Deadline-bearing patterns fire `date` signal alone.
_DEADLINE_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    _ISO_DATE_RE,
    _DEADLINE_RELATIVE_RE,
    _DAY_NAME_RE,
    _BY_MONTH_RE,
    _IN_N_PERIOD_RE,
)

# All date patterns in priority order for `when` annotation extraction.
# Deadline patterns are checked first so they win on sentences that
# contain both (e.g., "by Friday today" should annotate "by Friday").
_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    *_DEADLINE_DATE_PATTERNS,
    _DEICTIC_DATE_RE,
)


# Decision-point phrases. "TBD" is matched case-insensitive but with
# word boundaries so it does not match inside other words.
_DECISION_POINT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\btbd\b", re.IGNORECASE),
    re.compile(r"\b(?:we|i)\s+need\s+to\s+decide\b", re.IGNORECASE),
    re.compile(r"\bstill\s+figuring(?:\s+(?:it\s+)?out)?\b", re.IGNORECASE),
    re.compile(r"\bopen\s+question(?:s)?\b", re.IGNORECASE),
    re.compile(r"\bto\s+be\s+decided\b", re.IGNORECASE),
    re.compile(r"\bundecided\b", re.IGNORECASE),
    re.compile(r"\b(?:we|i)\s+haven'?t\s+decided\b", re.IGNORECASE),
    re.compile(r"\b(?:we|i)\s+haven'?t\s+figured\s+(?:it\s+)?out\b", re.IGNORECASE),
    re.compile(r"\bdecide\s+(?:on|whether)\b", re.IGNORECASE),
)


# Named-followup patterns. The capture group is restricted to a
# capitalized word (proper-noun heuristic) so generic following nouns
# like "the database" or "the form" do not fire. This is a deliberate
# v1 simplification; lowercase-named entities are missed.
_DIRECT_FOLLOWUP_RE = re.compile(
    r"(?i:\b(?:ping|ask|contact|email|dm|message|text|tell|"
    r"follow\s+up\s+with|reach\s+out\s+to)\s+)"
    r"([A-Z][\w-]+)"
)
_TOOL_FOLLOWUP_RE = re.compile(
    r"(?i:\b(?:test\s+in|spike\s+with|build\s+with|deploy\s+on|"
    r"implement\s+in)\s+)"
    r"([A-Z][\w-]+)"
)
_DELIVERY_TO_RE = re.compile(
    r"(?i:\b(?:send|deliver|hand|forward|post|ship|email)\b"
    r"[^.!?\n]*?\bto\s+)"
    r"([A-Z][\w-]+)"
)

_NAMED_FOLLOWUP_PATTERNS: tuple[re.Pattern[str], ...] = (
    _DIRECT_FOLLOWUP_RE,
    _TOOL_FOLLOWUP_RE,
    _DELIVERY_TO_RE,
)


# Bullet and ordered-list markers stripped from the start of each line.
# Also strips an optional task-list checkbox after the bullet so input
# like "- [ ] Send the deck" surfaces "Send" as the imperative.
_BULLET_PREFIX_RE = re.compile(
    r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?"
)

# Sentence walker: matches non-terminator runs followed by an optional
# terminator. Used inside each line so list items do not bleed across.
_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def extract_next_actions(
    text: str,
    config: Config,
    *,
    max_proposals: int = 10,
) -> NextActionsResult:
    """Extract candidate next-actions from `text` based on action signals.

    Returns an empty result (`gate_fired=False`, `proposals=()`,
    `signals_detected=()`) when input is empty/whitespace or when no
    signal fires anywhere in the input. Never raises on benign input.

    Mode-agnostic: identical behavior under fixed_domains and emergent.
    The `config` argument is accepted for parity with Steps 3-6 but is
    not consulted in v1.
    """
    del config  # accepted for orchestrator parity; unused in v1

    if not text or not text.strip() or max_proposals <= 0:
        return NextActionsResult(
            proposals=(),
            gate_fired=False,
            signals_detected=(),
        )

    proposals: list[NextAction] = []
    signals_seen: set[str] = set()

    for sentence in _iter_clauses(text):
        if len(proposals) >= max_proposals:
            break
        proposal = _analyze_sentence(sentence)
        if proposal is None:
            continue
        proposals.append(proposal)
        for signal in proposal.signal.split(" + "):
            signals_seen.add(signal)

    return NextActionsResult(
        proposals=tuple(proposals),
        gate_fired=bool(proposals),
        signals_detected=tuple(sorted(signals_seen)),
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _iter_clauses(text: str) -> Iterable[str]:
    """Yield trimmed clauses from `text`, honoring bullet and list markers.

    Each input line is stripped of any leading bullet prefix, then split
    into sentences on `.`, `!`, `?` boundaries. Empty clauses are
    skipped. The yielded string is the full clause including any
    terminating punctuation, used as both `what` and `source_excerpt`
    on the resulting NextAction.
    """
    for raw_line in text.splitlines():
        stripped_line = _BULLET_PREFIX_RE.sub("", raw_line).strip()
        if not stripped_line:
            continue
        for match in _SENTENCE_RE.finditer(stripped_line):
            sentence = match.group(0).strip()
            if sentence:
                yield sentence


def _analyze_sentence(sentence: str) -> NextAction | None:
    signals: list[str] = []

    if _is_imperative(sentence):
        signals.append("imperative")
    if _FUTURE_INTENT_RE.search(sentence):
        signals.append("future_intent")
    if _matches_decision_point(sentence):
        signals.append("decision_point")

    where = _extract_named_followup(sentence)
    if where:
        signals.append("named_followup")

    when = _extract_date(sentence)
    if when:
        # `date` only joins signals when this is a deadline-bearing
        # match (ISO date, "by Friday", "next week", etc.) OR when the
        # sentence already has another signal. A bare deictic alone
        # ("Today I learned about Python.") does not fire the gate.
        if _has_deadline_date(sentence) or signals:
            signals.append("date")
        else:
            when = None

    if not signals:
        return None

    return NextAction(
        what=sentence,
        when=when,
        where=where,
        effort=None,
        waiting_on=None,
        signal=" + ".join(sorted(set(signals))),
        source_excerpt=sentence,
    )


def _is_imperative(sentence: str) -> bool:
    stripped = sentence.lstrip()
    if not stripped:
        return False
    first_token = stripped.split(None, 1)[0]
    # Strip any non-word trailing punctuation (e.g., "Send," -> "Send").
    first_word = re.sub(r"[^\w'-].*$", "", first_token).lower()
    return first_word in _IMPERATIVE_VERBS


def _extract_date(sentence: str) -> str | None:
    earliest: tuple[int, str] | None = None
    for pattern in _DATE_PATTERNS:
        match = pattern.search(sentence)
        if match is None:
            continue
        if earliest is None or match.start() < earliest[0]:
            earliest = (match.start(), match.group(0))
    return earliest[1] if earliest is not None else None


def _has_deadline_date(sentence: str) -> bool:
    return any(p.search(sentence) is not None for p in _DEADLINE_DATE_PATTERNS)


def _matches_decision_point(sentence: str) -> bool:
    return any(p.search(sentence) for p in _DECISION_POINT_PATTERNS)


def _extract_named_followup(sentence: str) -> str | None:
    for pattern in _NAMED_FOLLOWUP_PATTERNS:
        match = pattern.search(sentence)
        if match is not None:
            return match.group(1)
    return None


__all__ = [
    "NextAction",
    "NextActionsResult",
    "extract_next_actions",
]
