"""Tests for Step 7: extract candidate next-actions (gated by signals).

Per build spec lines 171-182: gate fires only when text contains action
signals (imperatives, future-tense intent, dates/deadlines, decision
points, named follow-ups). For descriptive content with no signals,
return an empty result so the skill orchestrator suppresses the
"Possíveis próximos passos" section.

Step 7 is content-driven, not vault-driven, so the function is mode-
agnostic: identical behavior under fixed_domains and emergent.

Rule-based v1 (Option A) per Steps 2-6 cadence; model-call v2 deferred.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from types import MappingProxyType

import pytest

from vault_intake.config import Config, Domain
from vault_intake.next_actions import (
    NextAction,
    NextActionsResult,
    extract_next_actions,
)


def _make_config(
    *,
    mode: str = "fixed_domains",
    vault_path: Path | None = None,
) -> Config:
    return Config(
        vault_path=vault_path or Path("/tmp/vault-stub"),
        mode=mode,  # type: ignore[arg-type]
        domains=(
            Domain(slug="ops", description="Operations and processes."),
            Domain(slug="branding", description="Brand identity and design."),
            Domain(slug="dev", description="Software development and engineering."),
        ) if mode == "fixed_domains" else (),
        notebook_map=MappingProxyType({}),
        language="en",
        skip_notebooklm=False,
        refinement_enabled=True,
        classification_confidence_threshold=0.6,
    )


# ---------------------------------------------------------------------------
# Round 1: gate-skip on signal-free content
# ---------------------------------------------------------------------------


def test_descriptive_content_with_no_signals_does_not_fire_gate():
    text = (
        "The branding system uses the OK/Hmm/Not pattern as its core "
        "evaluation framework. Decisions are documented in the council notes."
    )
    result = extract_next_actions(text, _make_config())

    assert result.gate_fired is False
    assert result.proposals == ()
    assert result.signals_detected == ()


def test_empty_text_returns_empty_result_without_crash():
    result = extract_next_actions("", _make_config())

    assert result.gate_fired is False
    assert result.proposals == ()
    assert result.signals_detected == ()


def test_whitespace_only_text_returns_empty_result_without_crash():
    result = extract_next_actions("   \n\t \n  ", _make_config())

    assert result.gate_fired is False
    assert result.proposals == ()


# ---------------------------------------------------------------------------
# Round 2: imperative detection
# ---------------------------------------------------------------------------


def test_imperative_verb_first_phrasing_fires_gate():
    result = extract_next_actions(
        "Send the launch deck to the partners.",
        _make_config(),
    )

    assert result.gate_fired is True
    assert "imperative" in result.signals_detected
    assert len(result.proposals) == 1
    assert "imperative" in result.proposals[0].signal


def test_multiple_imperative_verbs_each_become_proposals():
    text = (
        "Send the launch deck to Alice. "
        "Review the council document. "
        "Schedule the follow-up call."
    )
    result = extract_next_actions(text, _make_config())

    assert result.gate_fired is True
    assert len(result.proposals) == 3
    assert all("imperative" in p.signal for p in result.proposals)


def test_imperative_excerpt_carries_source_sentence():
    text = "Send the launch deck to Alice."
    result = extract_next_actions(text, _make_config())

    assert result.proposals[0].source_excerpt == "Send the launch deck to Alice."


# ---------------------------------------------------------------------------
# Round 3: future-tense intent detection
# ---------------------------------------------------------------------------


def test_future_intent_we_will_fires_gate():
    result = extract_next_actions(
        "We'll need to spike the new auth flow this week.",
        _make_config(),
    )

    assert result.gate_fired is True
    assert "future_intent" in result.signals_detected


def test_future_intent_i_should_fires_gate():
    result = extract_next_actions(
        "I should review the brand guidelines before the next session.",
        _make_config(),
    )

    assert result.gate_fired is True
    assert "future_intent" in result.signals_detected


def test_future_intent_going_to_fires_gate():
    result = extract_next_actions(
        "Tomorrow I am going to write up the post-mortem.",
        _make_config(),
    )

    assert result.gate_fired is True
    assert "future_intent" in result.signals_detected


# ---------------------------------------------------------------------------
# Round 4: date and deadline detection
# ---------------------------------------------------------------------------


def test_iso_date_fires_gate_and_populates_when():
    result = extract_next_actions(
        "Submit the report by 2026-05-15.",
        _make_config(),
    )

    assert result.gate_fired is True
    assert "date" in result.signals_detected
    assert result.proposals[0].when is not None
    assert "2026-05-15" in result.proposals[0].when


def test_relative_date_next_week_fires_gate():
    result = extract_next_actions(
        "Send the deck next week.",
        _make_config(),
    )

    assert result.gate_fired is True
    assert "date" in result.signals_detected
    assert result.proposals[0].when is not None


def test_relative_date_by_friday_fires_gate():
    result = extract_next_actions(
        "Finalize the deck by Friday.",
        _make_config(),
    )

    assert result.gate_fired is True
    assert "date" in result.signals_detected


def test_tomorrow_fires_gate():
    result = extract_next_actions(
        "Tomorrow I will ship the patch.",
        _make_config(),
    )

    assert result.gate_fired is True
    assert "date" in result.signals_detected


# ---------------------------------------------------------------------------
# Round 5: decision-point detection
# ---------------------------------------------------------------------------


def test_tbd_fires_decision_point_gate():
    result = extract_next_actions(
        "The colour palette is TBD until council reviews it.",
        _make_config(),
    )

    assert result.gate_fired is True
    assert "decision_point" in result.signals_detected


def test_we_need_to_decide_fires_decision_point_gate():
    result = extract_next_actions(
        "We need to decide on the navbar redesign approach.",
        _make_config(),
    )

    assert result.gate_fired is True
    assert "decision_point" in result.signals_detected


def test_open_question_fires_decision_point_gate():
    result = extract_next_actions(
        "Open question: should the launcher be modal or inline?",
        _make_config(),
    )

    assert result.gate_fired is True
    assert "decision_point" in result.signals_detected


def test_still_figuring_out_fires_decision_point_gate():
    result = extract_next_actions(
        "Still figuring out how to ship the partner onboarding flow.",
        _make_config(),
    )

    assert result.gate_fired is True
    assert "decision_point" in result.signals_detected


# ---------------------------------------------------------------------------
# Round 6: named-followup detection
# ---------------------------------------------------------------------------


def test_ping_named_person_fires_named_followup():
    result = extract_next_actions(
        "Ping Alice about the launch deck draft.",
        _make_config(),
    )

    assert result.gate_fired is True
    assert "named_followup" in result.signals_detected
    assert result.proposals[0].where == "Alice"


def test_ask_named_person_fires_named_followup():
    result = extract_next_actions(
        "Ask Bob whether he reviewed the council notes.",
        _make_config(),
    )

    assert result.gate_fired is True
    assert "named_followup" in result.signals_detected
    assert result.proposals[0].where == "Bob"


def test_test_in_named_tool_fires_named_followup():
    result = extract_next_actions(
        "We should test in Playwright before promoting the build.",
        _make_config(),
    )

    assert result.gate_fired is True
    assert "named_followup" in result.signals_detected
    assert result.proposals[0].where == "Playwright"


def test_spike_with_named_tool_fires_named_followup():
    result = extract_next_actions(
        "Spike with Convex once the schema is locked.",
        _make_config(),
    )

    assert result.gate_fired is True
    assert "named_followup" in result.signals_detected
    assert result.proposals[0].where == "Convex"


# ---------------------------------------------------------------------------
# Round 7: multi-signal candidates
# ---------------------------------------------------------------------------


def test_multi_signal_candidate_records_all_matching_signals():
    text = "Send the launch deck to Alice by Friday."
    result = extract_next_actions(text, _make_config())

    assert result.gate_fired is True
    assert len(result.proposals) == 1
    proposal = result.proposals[0]
    assert "imperative" in proposal.signal
    assert "named_followup" in proposal.signal
    assert "date" in proposal.signal
    assert proposal.where == "Alice"
    assert proposal.when is not None
    assert "Friday" in proposal.when


def test_signals_detected_is_deduplicated_across_proposals():
    text = (
        "Send the launch deck to the partners. "
        "Review the council notes."
    )
    result = extract_next_actions(text, _make_config())

    # Both sentences are imperatives; signals_detected lists imperative once.
    counts = {s: result.signals_detected.count(s) for s in result.signals_detected}
    assert counts.get("imperative") == 1


def test_signals_detected_ordering_is_stable():
    text = (
        "Send the launch deck to Alice. "
        "We need to decide on the navbar approach. "
        "Submit the report by 2026-05-15."
    )
    result = extract_next_actions(text, _make_config())

    # signals_detected is sorted alphabetically for determinism.
    assert list(result.signals_detected) == sorted(result.signals_detected)


# ---------------------------------------------------------------------------
# Round 8: max_proposals cap
# ---------------------------------------------------------------------------


def test_max_proposals_cap_truncates_long_lists():
    text = " ".join(
        [f"Send batch {i} to the council." for i in range(15)]
    )
    result = extract_next_actions(text, _make_config(), max_proposals=5)

    assert len(result.proposals) == 5
    assert result.gate_fired is True


def test_default_max_proposals_is_ten():
    text = " ".join(
        [f"Send batch {i} to the council." for i in range(20)]
    )
    result = extract_next_actions(text, _make_config())

    assert len(result.proposals) == 10


# ---------------------------------------------------------------------------
# Round 9: mode-agnostic invariance
# ---------------------------------------------------------------------------


def test_fixed_domains_and_emergent_produce_same_result():
    text = "Send the launch deck to Alice by Friday."

    fixed_result = extract_next_actions(text, _make_config(mode="fixed_domains"))
    emergent_result = extract_next_actions(text, _make_config(mode="emergent"))

    assert fixed_result.gate_fired == emergent_result.gate_fired
    assert len(fixed_result.proposals) == len(emergent_result.proposals)
    for f, e in zip(fixed_result.proposals, emergent_result.proposals):
        assert f.what == e.what
        assert f.signal == e.signal
        assert f.where == e.where
        assert f.when == e.when


def test_emergent_mode_does_not_raise_not_implemented():
    # Step 7 is the first mode-agnostic step. Unlike Steps 3-6, it must
    # NOT raise in emergent mode. Confirms the contract explicitly.
    extract_next_actions(
        "Send the deck to Alice.",
        _make_config(mode="emergent"),
    )


# ---------------------------------------------------------------------------
# Round 10: dataclass invariants
# ---------------------------------------------------------------------------


def test_next_action_is_frozen_dataclass():
    action = NextAction(
        what="Send the deck",
        when=None,
        where=None,
        effort=None,
        waiting_on=None,
        signal="imperative",
        source_excerpt="Send the deck.",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        action.what = "edited"  # type: ignore[misc]


def test_next_actions_result_is_frozen_dataclass():
    result = NextActionsResult(
        proposals=(),
        gate_fired=False,
        signals_detected=(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.gate_fired = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Round 11: to_markdown formatting
# ---------------------------------------------------------------------------


def test_to_markdown_produces_possiveis_proximos_passos_heading():
    result = extract_next_actions(
        "Send the launch deck to Alice by Friday.",
        _make_config(),
    )
    markdown = result.to_markdown()

    assert markdown.startswith("## Possíveis próximos passos")


def test_to_markdown_uses_plain_bullets_not_checkboxes():
    result = extract_next_actions(
        "Send the launch deck to Alice.",
        _make_config(),
    )
    markdown = result.to_markdown()

    # Plain bullets per spec line 175: "Format as plain bullets, NOT
    # task checkboxes." The orchestrator's downstream tooling materializes
    # tasks; intake never emits `- [ ]`.
    assert "- [ ]" not in markdown
    assert "- [x]" not in markdown


def test_to_markdown_returns_empty_string_when_gate_does_not_fire():
    result = extract_next_actions(
        "The branding system has three colours.",
        _make_config(),
    )

    assert result.to_markdown() == ""


def test_to_markdown_includes_what_and_signal_annotations():
    result = extract_next_actions(
        "Send the launch deck to Alice by Friday.",
        _make_config(),
    )
    markdown = result.to_markdown()

    assert "[What]" in markdown
    assert "[Signal:" in markdown
    assert "imperative" in markdown


def test_to_markdown_omits_optional_field_brackets_when_none():
    result = extract_next_actions(
        "Review the council notes.",
        _make_config(),
    )
    markdown = result.to_markdown()

    # `where`, `when`, `effort`, `waiting_on` are None for this input.
    assert "[Where:" not in markdown
    assert "[When:" not in markdown
    assert "[Effort:" not in markdown
    assert "[Waiting on:" not in markdown


def test_to_markdown_renders_optional_field_brackets_when_present():
    result = extract_next_actions(
        "Send the launch deck to Alice by Friday.",
        _make_config(),
    )
    markdown = result.to_markdown()

    assert "[Where: Alice]" in markdown
    assert "[When:" in markdown
    assert "Friday" in markdown


# ---------------------------------------------------------------------------
# Round 12: source_excerpt audit trail
# ---------------------------------------------------------------------------


def test_source_excerpt_preserves_exact_input_slice():
    text = (
        "Some preamble.\n\n"
        "Send the launch deck to Alice by Friday.\n\n"
        "Some trailing notes."
    )
    result = extract_next_actions(text, _make_config())

    assert any(
        "Send the launch deck to Alice by Friday" in p.source_excerpt
        for p in result.proposals
    )


# ---------------------------------------------------------------------------
# Round 13: weak-signal content still produces a result
# ---------------------------------------------------------------------------


def test_uncertain_content_with_weak_signal_still_produces_result():
    # Ambiguous prose with one weak signal should not false-skip. The
    # orchestrator and user prune at confirmation; the function over-
    # supplies because spec line 175 calls this a seed list.
    result = extract_next_actions(
        "There's a lot to think about. We should check on the partner brief.",
        _make_config(),
    )

    assert result.gate_fired is True
    assert len(result.proposals) >= 1


# ---------------------------------------------------------------------------
# Round 14: bullet-formatted input (oral-style notes)
# ---------------------------------------------------------------------------


def test_bullet_formatted_imperatives_each_become_proposals():
    text = (
        "Action items from today:\n"
        "- Send the deck to Alice\n"
        "- Review the council notes\n"
        "- Schedule the follow-up call\n"
    )
    result = extract_next_actions(text, _make_config())

    assert result.gate_fired is True
    assert len(result.proposals) >= 3
