"""Tests for peer analysis debate loop module."""

import json
from unittest.mock import patch

import pytest

from jenkins_job_insight.models import AiConfigEntry, TestFailure


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_failure(
    test_name: str = "com.example.TestClass.testMethod",
    error_message: str = "AssertionError: expected true",
    stack_trace: str = "at com.example.TestClass.testMethod(TestClass.java:42)",
) -> TestFailure:
    return TestFailure(
        test_name=test_name,
        error_message=error_message,
        stack_trace=stack_trace,
    )


def _peer_configs() -> list[AiConfigEntry]:
    return [
        AiConfigEntry(ai_provider="gemini", ai_model="gemini-2.5-pro"),
        AiConfigEntry(ai_provider="claude", ai_model="claude-sonnet-4-20250514"),
    ]


def _make_ai_json_response(
    classification: str = "CODE ISSUE",
    details: str = "Test is broken",
) -> str:
    return json.dumps(
        {
            "classification": classification,
            "affected_tests": ["com.example.TestClass.testMethod"],
            "details": details,
            "code_fix": {
                "file": "src/TestClass.java",
                "line": "42",
                "change": "fix the assertion",
            },
        }
    )


def _make_peer_json_response(
    agrees: bool = True,
    classification: str = "CODE ISSUE",
    reasoning: str = "I agree with the analysis",
    suggested_changes: str = "",
) -> str:
    return json.dumps(
        {
            "agrees": agrees,
            "classification": classification,
            "reasoning": reasoning,
            "suggested_changes": suggested_changes,
        }
    )


# ===========================================================================
# _check_consensus tests
# ===========================================================================


class TestCheckConsensus:
    def test_check_consensus_all_agree(self) -> None:
        """All non-failed peers agree with orchestrator -> True."""
        from jenkins_job_insight.peer_analysis import _check_consensus
        from jenkins_job_insight.models import PeerRound

        rounds = [
            PeerRound(
                round=1,
                ai_provider="gemini",
                ai_model="gemini-2.5-pro",
                role="peer",
                classification="CODE ISSUE",
                details="agree",
                agrees_with_orchestrator=True,
            ),
            PeerRound(
                round=1,
                ai_provider="claude",
                ai_model="claude-sonnet-4-20250514",
                role="peer",
                classification="CODE ISSUE",
                details="also agree",
                agrees_with_orchestrator=True,
            ),
        ]
        assert _check_consensus("CODE ISSUE", rounds) is True

    def test_check_consensus_disagreement(self) -> None:
        """At least one peer disagrees -> False."""
        from jenkins_job_insight.peer_analysis import _check_consensus
        from jenkins_job_insight.models import PeerRound

        rounds = [
            PeerRound(
                round=1,
                ai_provider="gemini",
                ai_model="gemini-2.5-pro",
                role="peer",
                classification="CODE ISSUE",
                details="agree",
                agrees_with_orchestrator=True,
            ),
            PeerRound(
                round=1,
                ai_provider="claude",
                ai_model="claude-sonnet-4-20250514",
                role="peer",
                classification="PRODUCT BUG",
                details="disagree",
                agrees_with_orchestrator=False,
            ),
        ]
        assert _check_consensus("CODE ISSUE", rounds) is False

    def test_check_consensus_no_valid_peers(self) -> None:
        """All peers failed (agrees=None) -> False."""
        from jenkins_job_insight.peer_analysis import _check_consensus
        from jenkins_job_insight.models import PeerRound

        rounds = [
            PeerRound(
                round=1,
                ai_provider="gemini",
                ai_model="gemini-2.5-pro",
                role="peer",
                classification="",
                details="failed",
                agrees_with_orchestrator=None,
            ),
            PeerRound(
                round=1,
                ai_provider="claude",
                ai_model="claude-sonnet-4-20250514",
                role="peer",
                classification="",
                details="also failed",
                agrees_with_orchestrator=None,
            ),
        ]
        assert _check_consensus("CODE ISSUE", rounds) is False

    def test_check_consensus_failed_peer_excluded(self) -> None:
        """One peer failed (None), remaining peer agrees -> True."""
        from jenkins_job_insight.peer_analysis import _check_consensus
        from jenkins_job_insight.models import PeerRound

        rounds = [
            PeerRound(
                round=1,
                ai_provider="gemini",
                ai_model="gemini-2.5-pro",
                role="peer",
                classification="",
                details="failed",
                agrees_with_orchestrator=None,
            ),
            PeerRound(
                round=1,
                ai_provider="claude",
                ai_model="claude-sonnet-4-20250514",
                role="peer",
                classification="CODE ISSUE",
                details="agree",
                agrees_with_orchestrator=True,
            ),
        ]
        assert _check_consensus("CODE ISSUE", rounds) is True

    def test_check_consensus_derives_from_classification_match(self) -> None:
        """Consensus is derived from classification match, not self-reported agrees field.

        A peer that self-reports agrees=True but has a different classification
        should NOT count as consensus.
        """
        from jenkins_job_insight.peer_analysis import _check_consensus
        from jenkins_job_insight.models import PeerRound

        # Peer says agrees=True but classification differs from orchestrator
        rounds = [
            PeerRound(
                round=1,
                ai_provider="gemini",
                ai_model="gemini-2.5-pro",
                role="peer",
                classification="PRODUCT BUG",
                details="I agree",
                agrees_with_orchestrator=True,  # self-reported, should be ignored
            ),
        ]
        # Consensus should be False because classification != orchestrator's "CODE ISSUE"
        assert _check_consensus("CODE ISSUE", rounds) is False

    def test_check_consensus_classification_match_overrides_disagrees(self) -> None:
        """Peer that says disagrees but has matching classification -> consensus True."""
        from jenkins_job_insight.peer_analysis import _check_consensus
        from jenkins_job_insight.models import PeerRound

        rounds = [
            PeerRound(
                round=1,
                ai_provider="gemini",
                ai_model="gemini-2.5-pro",
                role="peer",
                classification="CODE ISSUE",
                details="I disagree but",
                agrees_with_orchestrator=False,  # self-reported, should be ignored
            ),
        ]
        assert _check_consensus("CODE ISSUE", rounds) is True

    def test_check_consensus_case_insensitive(self) -> None:
        """Classification comparison is case-insensitive."""
        from jenkins_job_insight.peer_analysis import _check_consensus
        from jenkins_job_insight.models import PeerRound

        rounds = [
            PeerRound(
                round=1,
                ai_provider="gemini",
                ai_model="gemini-2.5-pro",
                role="peer",
                classification="code issue",
                details="agree",
                agrees_with_orchestrator=True,
            ),
        ]
        assert _check_consensus("CODE ISSUE", rounds) is True

    def test_check_consensus_whitespace_tolerance(self) -> None:
        """Classification comparison tolerates leading/trailing whitespace."""
        from jenkins_job_insight.peer_analysis import _check_consensus
        from jenkins_job_insight.models import PeerRound

        rounds = [
            PeerRound(
                round=1,
                ai_provider="gemini",
                ai_model="gemini-2.5-pro",
                role="peer",
                classification="  CODE ISSUE  ",
                details="agree",
                agrees_with_orchestrator=True,
            ),
        ]
        assert _check_consensus("CODE ISSUE", rounds) is True


# ===========================================================================
# _normalize_classification tests
# ===========================================================================


class TestNormalizeClassification:
    def test_normalizes_lowercase(self) -> None:
        from jenkins_job_insight.peer_analysis import _normalize_classification

        assert _normalize_classification("code issue") == "CODE ISSUE"

    def test_normalizes_mixed_case(self) -> None:
        from jenkins_job_insight.peer_analysis import _normalize_classification

        assert _normalize_classification("Product Bug") == "PRODUCT BUG"

    def test_strips_whitespace(self) -> None:
        from jenkins_job_insight.peer_analysis import _normalize_classification

        assert _normalize_classification("  CODE ISSUE  ") == "CODE ISSUE"

    def test_already_normalized(self) -> None:
        from jenkins_job_insight.peer_analysis import _normalize_classification

        assert _normalize_classification("PRODUCT BUG") == "PRODUCT BUG"

    def test_collapses_internal_whitespace(self) -> None:
        from jenkins_job_insight.peer_analysis import _normalize_classification

        assert _normalize_classification("CODE   ISSUE") == "CODE ISSUE"

    def test_handles_none_input(self) -> None:
        from jenkins_job_insight.peer_analysis import _normalize_classification

        assert _normalize_classification(None) == ""


# ===========================================================================
# _parse_peer_response tests
# ===========================================================================


class TestParsePeerResponse:
    def test_parse_peer_response_valid_json(self) -> None:
        """Valid JSON string is parsed correctly."""
        from jenkins_job_insight.peer_analysis import _parse_peer_response

        raw = _make_peer_json_response(agrees=True, classification="CODE ISSUE")
        result = _parse_peer_response(raw)
        assert result["agrees"] is True
        assert result["classification"] == "CODE ISSUE"
        assert "_failed" not in result

    def test_parse_peer_response_json_in_text(self) -> None:
        """JSON embedded in markdown code block is extracted."""
        from jenkins_job_insight.peer_analysis import _parse_peer_response

        raw = f"Here is my analysis:\n```json\n{_make_peer_json_response()}\n```\n"
        result = _parse_peer_response(raw)
        assert result["agrees"] is True
        assert "_failed" not in result

    def test_parse_peer_response_unparseable(self) -> None:
        """Completely unparseable text returns _failed marker."""
        from jenkins_job_insight.peer_analysis import _parse_peer_response

        result = _parse_peer_response("I cannot provide JSON output sorry")
        assert result["_failed"] is True

    @pytest.mark.parametrize(
        "raw_json,description",
        [
            ('"just a string"', "JSON string"),
            ("[1, 2, 3]", "JSON array"),
            ("42", "JSON number"),
            ("true", "JSON boolean"),
            ("null", "JSON null"),
        ],
    )
    def test_parse_peer_response_non_dict_json_returns_failed(
        self, raw_json: str, description: str
    ) -> None:
        """Non-dict JSON (string, array, number, etc.) returns _failed marker."""
        from jenkins_job_insight.peer_analysis import _parse_peer_response

        result = _parse_peer_response(raw_json)
        assert result.get("_failed") is True, (
            f"Expected _failed for {description}: {raw_json}"
        )

    def test_parse_peer_response_non_dict_in_code_block(self) -> None:
        """Non-dict JSON inside markdown code block returns _failed marker."""
        from jenkins_job_insight.peer_analysis import _parse_peer_response

        raw = '```json\n["not", "a", "dict"]\n```'
        result = _parse_peer_response(raw)
        assert result.get("_failed") is True

    def test_parse_peer_response_non_dict_brace_extraction(self) -> None:
        """When brace extraction yields a valid dict, it should succeed."""
        from jenkins_job_insight.peer_analysis import _parse_peer_response

        # This has leading text but valid JSON dict inside
        raw = f"Some preamble text {_make_peer_json_response()}"
        result = _parse_peer_response(raw)
        assert "_failed" not in result
        assert result["agrees"] is True

    def test_parse_peer_response_json_in_second_code_block(self) -> None:
        """JSON dict in a later fenced block is found when the first block is non-dict JSON."""
        from jenkins_job_insight.peer_analysis import _parse_peer_response

        valid_json = _make_peer_json_response(
            agrees=False, classification="PRODUCT BUG"
        )
        # First code block contains valid JSON but not a dict (an array).
        # Strategy 2 currently only tries the first block, so this fails
        # even though the second block has the valid dict.
        # Strategy 3 (brace matching) won't help because the greedy regex
        # matches from the first `{` in the array element to the last `}`
        # in the valid JSON, producing invalid JSON.
        raw = (
            "Here is my thinking:\n"
            '```json\n[{"step": "analysis"}, {"step": "review"}]\n```\n'
            "And here is my answer:\n"
            f"```json\n{valid_json}\n```\n"
        )
        result = _parse_peer_response(raw)
        assert "_failed" not in result
        assert result["agrees"] is False
        assert result["classification"] == "PRODUCT BUG"


# ===========================================================================
# _build_peer_review_prompt tests
# ===========================================================================


class TestBuildFailureSummary:
    def test_no_stack_trace_in_summary(self) -> None:
        """Failure summary should NOT contain stack trace -- peers have repo access."""
        from jenkins_job_insight.peer_analysis import _build_failure_summary

        failures = [_make_failure(stack_trace="at com.example.Foo.bar(Foo.java:10)")]
        summary = _build_failure_summary(failures, error_signature="abc123")
        assert "STACK TRACE" not in summary
        assert "Foo.java:10" not in summary

    def test_failure_summary_contains_essentials(self) -> None:
        """Failure summary includes error signature, test names, and error message."""
        from jenkins_job_insight.peer_analysis import _build_failure_summary

        failures = [
            _make_failure(
                test_name="test_alpha",
                error_message="NullPointerException",
                stack_trace="ignored",
            )
        ]
        summary = _build_failure_summary(failures, error_signature="sig456")
        assert "sig456" in summary
        assert "test_alpha" in summary
        assert "NullPointerException" in summary


class TestBuildPeerReviewPrompt:
    def test_build_peer_review_prompt_contains_framing(self) -> None:
        """Prompt contains AI-to-AI anti-sycophancy framing."""
        from jenkins_job_insight.peer_analysis import _build_peer_review_prompt

        prompt = _build_peer_review_prompt(
            failure_summary="Test failed with assertion error",
            orchestrator_analysis="classification: CODE ISSUE, details: broken test",
            custom_prompt="",
            resources_section="",
        )
        assert "AI-only conversation" in prompt
        assert "sycophantic" in prompt.lower()

    def test_build_peer_review_prompt_contains_analysis(self) -> None:
        """Prompt contains the orchestrator's analysis for review."""
        from jenkins_job_insight.peer_analysis import _build_peer_review_prompt

        prompt = _build_peer_review_prompt(
            failure_summary="Test failed with assertion error",
            orchestrator_analysis="classification: CODE ISSUE, details: broken test",
            custom_prompt="Focus on network issues",
            resources_section="- Git repo at /tmp/repo",
        )
        assert "CODE ISSUE" in prompt
        assert "broken test" in prompt
        assert "Focus on network issues" in prompt
        assert "Git repo at /tmp/repo" in prompt


# ===========================================================================
# Integration tests: analyze_failure_group_with_peers
# ===========================================================================


class TestAnalyzeWithPeers:
    @pytest.mark.asyncio
    async def test_analyze_with_peers_consensus_round_1(self) -> None:
        """All peers agree in round 1 -> consensus reached, 1 round used."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        peer_response = _make_peer_json_response(
            agrees=True, classification="CODE ISSUE"
        )

        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(classification="CODE ISSUE", details="Test is broken"),
                "sig123",
            )
        )

        async def mock_peer_call(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            return (True, peer_response)

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_peer_call,
            ),
        ):
            results = await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="some console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=3,
            )

        assert len(results) == 1
        assert results[0].analysis.classification == "CODE ISSUE"
        assert results[0].peer_debate is not None
        assert results[0].peer_debate.consensus_reached is True
        assert results[0].peer_debate.rounds_used == 1

    @pytest.mark.asyncio
    async def test_analyze_with_peers_consensus_after_revision(self) -> None:
        """Peers disagree round 1, main AI revises, consensus round 2."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        main_response_r2 = _make_ai_json_response(
            classification="PRODUCT BUG",
            details="Revised: this is a product bug",
        )
        # Round 1 peers disagree
        peer_disagree = _make_peer_json_response(
            agrees=False,
            classification="PRODUCT BUG",
            reasoning="This is clearly a product bug",
        )
        # Round 2 peers agree
        peer_agree = _make_peer_json_response(
            agrees=True,
            classification="PRODUCT BUG",
        )

        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(classification="CODE ISSUE", details="Test is broken"),
                "sig123",
            )
        )

        call_count = 0

        async def mock_peer_and_revision_call(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            nonlocal call_count
            call_count += 1
            # Calls 1-2: round 1 peers (disagree)
            if call_count <= 2:
                return (True, peer_disagree)
            # Call 3: main AI revision
            if call_count == 3:
                return (True, main_response_r2)
            # Calls 4-5: round 2 peers (agree)
            return (True, peer_agree)

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_peer_and_revision_call,
            ),
        ):
            results = await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="some console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=3,
            )

        assert len(results) == 1
        debate = results[0].peer_debate
        assert debate is not None
        assert debate.consensus_reached is True
        assert debate.rounds_used == 2

    @pytest.mark.asyncio
    async def test_failed_revision_preserves_previous_analysis(self) -> None:
        """When revision call fails, previous valid analysis is kept."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        # Orchestrator initial: CODE ISSUE
        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(classification="CODE ISSUE", details="initial"),
                "sig123",
            )
        )

        peer_disagree = _make_peer_json_response(
            agrees=False,
            classification="PRODUCT BUG",
            reasoning="disagree",
        )
        peer_agree = _make_peer_json_response(
            agrees=True,
            classification="CODE ISSUE",
            reasoning="ok",
        )

        call_count = 0

        async def mock_peer_and_revision_call(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            nonlocal call_count
            call_count += 1
            # Calls 1-2: round 1 peers disagree
            if call_count <= 2:
                return (True, peer_disagree)
            # Call 3: orchestrator revision FAILS
            if call_count == 3:
                return (False, "CLI error: connection refused")
            # Calls 4-5: round 2 peers agree (should see CODE ISSUE, preserved)
            return (True, peer_agree)

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_peer_and_revision_call,
            ),
        ):
            results = await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="some console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=2,
            )

        assert len(results) == 1
        # Classification should be CODE ISSUE (preserved from initial, not overwritten)
        assert results[0].analysis.classification == "CODE ISSUE"
        assert results[0].analysis.details == "initial"
        debate = results[0].peer_debate
        assert debate is not None
        assert debate.consensus_reached is True
        assert debate.rounds_used == 2

    @pytest.mark.asyncio
    async def test_revision_exception_preserves_previous_analysis(self) -> None:
        """When revision call raises an exception, prior analysis is preserved."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(classification="CODE ISSUE", details="initial"),
                "sig123",
            )
        )

        peer_disagree = _make_peer_json_response(
            agrees=False,
            classification="PRODUCT BUG",
            reasoning="disagree",
        )
        peer_agree = _make_peer_json_response(
            agrees=True,
            classification="CODE ISSUE",
            reasoning="ok",
        )

        call_count = 0

        async def mock_peer_and_revision_call(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            nonlocal call_count
            call_count += 1
            # Calls 1-2: round 1 peers disagree
            if call_count <= 2:
                return (True, peer_disagree)
            # Call 3: orchestrator revision RAISES an exception
            if call_count == 3:
                raise RuntimeError("CLI process crashed")
            # Calls 4-5: round 2 peers agree with preserved CODE ISSUE
            return (True, peer_agree)

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_peer_and_revision_call,
            ),
        ):
            results = await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="some console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=2,
            )

        assert len(results) == 1
        # Classification preserved from initial orchestrator (not lost to exception)
        assert results[0].analysis.classification == "CODE ISSUE"
        assert results[0].analysis.details == "initial"
        debate = results[0].peer_debate
        assert debate is not None
        assert debate.consensus_reached is True
        assert debate.rounds_used == 2

    @pytest.mark.asyncio
    async def test_revision_preserves_structured_fields_on_same_classification(
        self,
    ) -> None:
        """When revision keeps the same classification but drops structured fields,
        non-empty fields from the prior analysis are preserved (merged forward)."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        # Revision returns same classification but drops code_fix and artifacts_evidence
        revision_response = json.dumps(
            {
                "classification": "CODE ISSUE",
                "affected_tests": ["com.example.TestClass.testMethod"],
                "details": "Revised analysis with peer feedback incorporated",
            }
        )

        # Peers disagree in round 1 (different classification triggers revision)
        peer_disagree = _make_peer_json_response(
            agrees=False,
            classification="PRODUCT BUG",
            reasoning="This looks like a product bug not a code issue",
        )
        # After revision, peers agree with the (unchanged) CODE ISSUE classification
        peer_agree = _make_peer_json_response(
            agrees=True,
            classification="CODE ISSUE",
        )

        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(
                    classification="CODE ISSUE",
                    details="Initial detailed analysis",
                    artifacts_evidence="ERROR: assertion failed at line 42",
                    code_fix={
                        "file": "src/TestClass.java",
                        "line": "42",
                        "change": "fix the assertion",
                    },
                ),
                "sig123",
            )
        )

        call_count = 0

        async def mock_peer_and_revision_call(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            nonlocal call_count
            call_count += 1
            # Calls 1-2: round 1 peers disagree
            if call_count <= 2:
                return (True, peer_disagree)
            # Call 3: revision returns same classification but drops structured fields
            if call_count == 3:
                return (True, revision_response)
            # Calls 4-5: round 2 peers agree
            return (True, peer_agree)

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_peer_and_revision_call,
            ),
        ):
            results = await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=3,
            )

        assert len(results) == 1
        analysis = results[0].analysis
        assert analysis.classification == "CODE ISSUE"
        # Revised details should be used (newer)
        assert analysis.details == "Revised analysis with peer feedback incorporated"
        # Structured fields from initial analysis should be preserved
        assert analysis.artifacts_evidence == "ERROR: assertion failed at line 42"
        # Structured fields from initial analysis should be preserved exactly
        assert analysis.code_fix is not None and analysis.code_fix is not False
        assert analysis.code_fix.file == "src/TestClass.java"
        assert analysis.code_fix.line == "42"
        assert analysis.code_fix.change == "fix the assertion"

    @pytest.mark.asyncio
    async def test_revision_preserves_details_when_revision_omits_them(
        self,
    ) -> None:
        """When revision keeps the same classification but omits details,
        the prior analysis details are preserved (not replaced with empty string)."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        # Revision returns same classification but drops ALL fields including details
        revision_response = json.dumps(
            {
                "classification": "CODE ISSUE",
            }
        )

        # Peers disagree in round 1
        peer_disagree = _make_peer_json_response(
            agrees=False,
            classification="PRODUCT BUG",
            reasoning="This looks like a product bug",
        )
        # After revision, peers agree
        peer_agree = _make_peer_json_response(
            agrees=True,
            classification="CODE ISSUE",
        )

        initial_details = "Very thorough initial analysis with important context"
        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(
                    classification="CODE ISSUE",
                    details=initial_details,
                    artifacts_evidence="ERROR: assertion failed at line 42",
                    code_fix={
                        "file": "src/TestClass.java",
                        "line": "42",
                        "change": "fix the assertion",
                    },
                ),
                "sig123",
            )
        )

        call_count = 0

        async def mock_peer_and_revision_call(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return (True, peer_disagree)
            if call_count == 3:
                return (True, revision_response)
            return (True, peer_agree)

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_peer_and_revision_call,
            ),
        ):
            results = await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=3,
            )

        assert len(results) == 1
        analysis = results[0].analysis
        assert analysis.classification == "CODE ISSUE"
        # Details should be preserved from initial analysis since revision omitted them
        assert analysis.details == initial_details
        # Other structured fields should also be preserved
        assert analysis.artifacts_evidence == "ERROR: assertion failed at line 42"
        assert analysis.code_fix is not None and analysis.code_fix is not False
        assert analysis.code_fix.file == "src/TestClass.java"
        assert analysis.code_fix.line == "42"
        assert analysis.code_fix.change == "fix the assertion"

    @pytest.mark.asyncio
    async def test_revision_merge_uses_normalized_classification(self) -> None:
        """Revision returning a differently-cased classification still triggers
        the merge-forward path (preserving structured fields from prior analysis)."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        # Revision returns same classification but with different casing
        # and drops code_fix and artifacts_evidence
        revision_response = json.dumps(
            {
                "classification": "code issue",  # lowercase vs "CODE ISSUE"
                "affected_tests": ["com.example.TestClass.testMethod"],
                "details": "Revised analysis after peer review",
            }
        )

        peer_disagree = _make_peer_json_response(
            agrees=False,
            classification="PRODUCT BUG",
            reasoning="This looks like a product bug",
        )
        peer_agree = _make_peer_json_response(
            agrees=True,
            classification="CODE ISSUE",
        )

        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(
                    classification="CODE ISSUE",
                    details="Initial detailed analysis",
                    artifacts_evidence="ERROR: assertion failed at line 42",
                    code_fix={
                        "file": "src/TestClass.java",
                        "line": "42",
                        "change": "fix the assertion",
                    },
                ),
                "sig123",
            )
        )

        call_count = 0

        async def mock_peer_and_revision_call(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return (True, peer_disagree)
            if call_count == 3:
                return (True, revision_response)
            return (True, peer_agree)

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_peer_and_revision_call,
            ),
        ):
            results = await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=3,
            )

        assert len(results) == 1
        analysis = results[0].analysis
        # Classification is normalized to canonical uppercase form
        assert analysis.classification == "CODE ISSUE"
        # Revised details should be used (newer)
        assert analysis.details == "Revised analysis after peer review"
        # Structured fields from initial analysis should be preserved
        # because normalized classifications match ("code issue" == "CODE ISSUE")
        assert analysis.artifacts_evidence == "ERROR: assertion failed at line 42"
        assert analysis.code_fix is not None and analysis.code_fix is not False
        assert analysis.code_fix.file == "src/TestClass.java"
        assert analysis.code_fix.line == "42"
        assert analysis.code_fix.change == "fix the assertion"

    @pytest.mark.asyncio
    async def test_analyze_with_peers_max_rounds_no_consensus(self) -> None:
        """Peers never agree; exhausts max_rounds with consensus=False."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        main_response = _make_ai_json_response(classification="CODE ISSUE")
        peer_disagree = _make_peer_json_response(
            agrees=False,
            classification="PRODUCT BUG",
        )

        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(classification="CODE ISSUE", details="Test is broken"),
                "sig123",
            )
        )

        async def mock_peer_and_revision_call(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            # Revision calls: main AI always returns same classification
            if "revision" in prompt.lower() or "revise" in prompt.lower():
                return (True, main_response)
            # Peer calls: always disagree
            return (True, peer_disagree)

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_peer_and_revision_call,
            ),
        ):
            results = await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=2,
            )

        debate = results[0].peer_debate
        assert debate is not None
        assert debate.consensus_reached is False
        assert debate.rounds_used == 2
        assert debate.max_rounds == 2

    @pytest.mark.asyncio
    async def test_analyze_with_peers_all_peers_fail(self) -> None:
        """All peers return unparseable responses -> falls back to main AI, consensus=False."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(classification="CODE ISSUE", details="Test is broken"),
                "sig123",
            )
        )

        async def mock_peer_call(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            return (False, "CLI error: connection refused")

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_peer_call,
            ),
        ):
            results = await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=3,
            )

        assert len(results) == 1
        assert results[0].analysis.classification == "CODE ISSUE"
        debate = results[0].peer_debate
        assert debate is not None
        assert debate.consensus_reached is False

    @pytest.mark.asyncio
    async def test_peer_agrees_derived_from_classification_not_self_reported(
        self,
    ) -> None:
        """agrees_with_orchestrator is derived from classification match, not the peer's self-reported agrees field."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(classification="CODE ISSUE", details="Test is broken"),
                "sig123",
            )
        )
        # Peer self-reports agrees=True but classification is PRODUCT BUG
        peer_response = _make_peer_json_response(
            agrees=True,
            classification="PRODUCT BUG",
            reasoning="I say I agree but my classification differs",
        )

        async def mock_peer_call(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            return (True, peer_response)

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_peer_call,
            ),
        ):
            results = await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=1,
            )

        debate = results[0].peer_debate
        assert debate is not None
        # Classification mismatch means no consensus, despite self-reported agrees=True
        assert debate.consensus_reached is False
        # Check that agrees_with_orchestrator was derived from classification match
        peer_rounds = [r for r in debate.rounds if r.role == "peer"]
        for pr in peer_rounds:
            if pr.agrees_with_orchestrator is not None:
                # Classification is "PRODUCT BUG" vs orchestrator "CODE ISSUE" -> False
                assert pr.agrees_with_orchestrator is False

    @pytest.mark.asyncio
    async def test_analyze_with_peers_one_peer_fails(self) -> None:
        """One peer fails, remaining peer agrees -> consensus=True."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(classification="CODE ISSUE", details="Test is broken"),
                "sig123",
            )
        )
        peer_agree = _make_peer_json_response(agrees=True, classification="CODE ISSUE")

        call_count = 0

        async def mock_peer_call(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            nonlocal call_count
            call_count += 1
            # First peer fails, second agrees
            if call_count == 1:
                return (False, "CLI crashed")
            return (True, peer_agree)

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_peer_call,
            ),
        ):
            results = await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=3,
            )

        debate = results[0].peer_debate
        assert debate is not None
        assert debate.consensus_reached is True
        # Should have entries for both peers (one failed, one succeeded)
        peer_rounds = [r for r in debate.rounds if r.role == "peer"]
        assert len(peer_rounds) == 2
        failed_peers = [r for r in peer_rounds if r.agrees_with_orchestrator is None]
        assert len(failed_peers) == 1

    @pytest.mark.asyncio
    async def test_progress_phase_updates_during_peer_debate(self) -> None:
        """Progress phase is updated before each peer round and orchestrator revision."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        main_response = _make_ai_json_response(classification="CODE ISSUE")
        peer_disagree = _make_peer_json_response(
            agrees=False,
            classification="PRODUCT BUG",
        )
        peer_agree = _make_peer_json_response(
            agrees=True,
            classification="CODE ISSUE",
        )

        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(classification="CODE ISSUE", details="Test is broken"),
                "sig123",
            )
        )

        call_count = 0

        async def mock_peer_and_revision_call(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            nonlocal call_count
            call_count += 1
            # Calls 1-2: round 1 peers (disagree)
            if call_count <= 2:
                return (True, peer_disagree)
            # Call 3: main AI revision
            if call_count == 3:
                return (True, main_response)
            # Calls 4-5: round 2 peers (agree)
            return (True, peer_agree)

        phases: list[str] = []

        async def capture_phase(job_id, phase):
            phases.append(phase)

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_peer_and_revision_call,
            ),
            patch(
                "jenkins_job_insight.peer_analysis.update_progress_phase",
                side_effect=capture_phase,
            ),
        ):
            await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=3,
                job_id="test-job-id",
            )

        assert "peer_review_round_1" in phases
        assert "orchestrator_revising_round_1" in phases
        assert "peer_review_round_2" in phases

    @pytest.mark.asyncio
    async def test_progress_phase_includes_group_label(self) -> None:
        """When group_label is provided, progress phases include the group info."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        main_response = _make_ai_json_response(classification="CODE ISSUE")
        peer_disagree = _make_peer_json_response(
            agrees=False,
            classification="PRODUCT BUG",
        )
        peer_agree = _make_peer_json_response(
            agrees=True,
            classification="CODE ISSUE",
        )

        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(classification="CODE ISSUE", details="Test is broken"),
                "sig123",
            )
        )

        call_count = 0

        async def mock_peer_and_revision_call(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return (True, peer_disagree)
            if call_count == 3:
                return (True, main_response)
            return (True, peer_agree)

        phases: list[str] = []

        async def capture_phase(job_id, phase):
            phases.append(phase)

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_peer_and_revision_call,
            ),
            patch(
                "jenkins_job_insight.peer_analysis.update_progress_phase",
                side_effect=capture_phase,
            ),
        ):
            await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=3,
                job_id="test-job-id",
                group_label="2/3",
            )

        assert "peer_review_round_1 (group 2/3)" in phases
        assert "orchestrator_revising_round_1 (group 2/3)" in phases
        assert "peer_review_round_2 (group 2/3)" in phases

    @pytest.mark.asyncio
    async def test_progress_phase_no_group_suffix_when_label_empty(self) -> None:
        """When group_label is empty, progress phases have no group suffix."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        peer_agree = _make_peer_json_response(agrees=True, classification="CODE ISSUE")

        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(classification="CODE ISSUE", details="Test is broken"),
                "sig123",
            )
        )

        async def mock_peer_call(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            return (True, peer_agree)

        phases: list[str] = []

        async def capture_phase(job_id, phase):
            phases.append(phase)

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_peer_call,
            ),
            patch(
                "jenkins_job_insight.peer_analysis.update_progress_phase",
                side_effect=capture_phase,
            ),
        ):
            await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=3,
                job_id="test-job-id",
                group_label="",
            )

        # Should be exactly "peer_review_round_1" with no group suffix
        assert "peer_review_round_1" in phases
        assert not any("group" in p for p in phases)

    @pytest.mark.asyncio
    async def test_peer_invalid_classification_excluded_from_consensus(self) -> None:
        """Peer returning an invalid classification (not CODE ISSUE or PRODUCT BUG) is excluded."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(classification="CODE ISSUE", details="Test is broken"),
                "sig123",
            )
        )
        # One peer returns invalid classification, other agrees
        call_count = 0

        async def mock_peer_call(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Invalid classification
                return (
                    True,
                    _make_peer_json_response(
                        agrees=True,
                        classification="UNKNOWN TYPE",
                        reasoning="not a valid classification",
                    ),
                )
            # Valid and agrees
            return (
                True,
                _make_peer_json_response(
                    agrees=True,
                    classification="CODE ISSUE",
                ),
            )

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_peer_call,
            ),
        ):
            results = await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=1,
            )

        debate = results[0].peer_debate
        assert debate is not None
        # Consensus should be True because the invalid peer is excluded
        # and the valid peer agrees
        assert debate.consensus_reached is True
        # The invalid peer should have agrees_with_orchestrator=None
        peer_rounds = [r for r in debate.rounds if r.role == "peer"]
        invalid_peers = [r for r in peer_rounds if r.agrees_with_orchestrator is None]
        assert len(invalid_peers) == 1

    @pytest.mark.asyncio
    async def test_peer_null_classification_coerced_to_empty_string(self) -> None:
        """Peer returning null classification stores empty string, not None."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(classification="CODE ISSUE", details="Test is broken"),
                "sig123",
            )
        )
        # Craft raw JSON with null classification
        null_classification_response = json.dumps(
            {
                "agrees": True,
                "classification": None,
                "reasoning": "some reasoning",
                "suggested_changes": "",
            }
        )

        async def mock_peer_call(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            return (True, null_classification_response)

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_peer_call,
            ),
        ):
            results = await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=1,
            )

        debate = results[0].peer_debate
        assert debate is not None
        # Null classification should be excluded from consensus
        peer_rounds = [r for r in debate.rounds if r.role == "peer"]
        for pr in peer_rounds:
            # Classification should be coerced to empty string, not None
            assert pr.classification == ""
            assert pr.agrees_with_orchestrator is None

    @pytest.mark.asyncio
    async def test_peer_case_insensitive_classification_agreement(self) -> None:
        """Peer returning lowercase classification still reaches consensus."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(classification="CODE ISSUE", details="Test is broken"),
                "sig123",
            )
        )
        # Peer returns lowercase classification
        peer_response = _make_peer_json_response(
            agrees=True,
            classification="code issue",  # lowercase
        )

        async def mock_peer_call(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            return (True, peer_response)

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_peer_call,
            ),
        ):
            results = await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=1,
            )

        debate = results[0].peer_debate
        assert debate is not None
        assert debate.consensus_reached is True
        # Check that normalized classification is stored
        peer_rounds = [r for r in debate.rounds if r.role == "peer"]
        for pr in peer_rounds:
            if pr.agrees_with_orchestrator is not None:
                assert pr.classification == "CODE ISSUE"

    @pytest.mark.asyncio
    async def test_no_progress_phase_when_job_id_empty(self) -> None:
        """When job_id is empty, update_progress_phase should not be called."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        peer_response = _make_peer_json_response(
            agrees=True, classification="CODE ISSUE"
        )

        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(classification="CODE ISSUE", details="Test is broken"),
                "sig123",
            )
        )

        async def mock_peer_call(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            return (True, peer_response)

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_peer_call,
            ),
            patch(
                "jenkins_job_insight.peer_analysis.update_progress_phase",
                new_callable=AsyncMock,
            ) as mock_update,
        ):
            await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=3,
                job_id="",
            )

        mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_suggested_changes_preserved_in_peer_round_details(self) -> None:
        """Peer's suggested_changes are preserved in the PeerRound details field."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        peer_response = _make_peer_json_response(
            agrees=True,
            classification="CODE ISSUE",
            reasoning="The test assertion is wrong",
            suggested_changes="Fix the assertion on line 42",
        )

        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(classification="CODE ISSUE", details="Test is broken"),
                "sig123",
            )
        )

        async def mock_peer_call(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            return (True, peer_response)

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_peer_call,
            ),
        ):
            results = await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=1,
            )

        debate = results[0].peer_debate
        assert debate is not None
        peer_rounds = [r for r in debate.rounds if r.role == "peer"]
        for pr in peer_rounds:
            assert "The test assertion is wrong" in pr.details
            assert "Fix the assertion on line 42" in pr.details

    @pytest.mark.asyncio
    async def test_empty_suggested_changes_not_appended(self) -> None:
        """When suggested_changes is empty, details contains only reasoning."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        peer_response = _make_peer_json_response(
            agrees=True,
            classification="CODE ISSUE",
            reasoning="The test assertion is wrong",
            suggested_changes="",
        )

        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(classification="CODE ISSUE", details="Test is broken"),
                "sig123",
            )
        )

        async def mock_peer_call(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            return (True, peer_response)

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_peer_call,
            ),
        ):
            results = await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=1,
            )

        debate = results[0].peer_debate
        assert debate is not None
        peer_rounds = [r for r in debate.rounds if r.role == "peer"]
        for pr in peer_rounds:
            assert pr.details == "The test assertion is wrong"
            assert "Suggested changes" not in pr.details

    @pytest.mark.asyncio
    async def test_invalid_orchestrator_classification_normalized(self) -> None:
        """Orchestrator returning an invalid classification gets normalized to empty string."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        # Main AI returns a malformed classification
        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(
                    classification="CODEISSUE",
                    details="Malformed classification",
                ),
                "sig123",
            )
        )

        peer_response = _make_peer_json_response(
            agrees=True, classification="CODE ISSUE"
        )

        async def mock_peer_call(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            return (True, peer_response)

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_peer_call,
            ),
        ):
            results = await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=1,
            )

        analysis = results[0].analysis
        # Invalid "CODEISSUE" should NOT leak through as the final classification
        assert analysis.classification != "CODEISSUE"

    @pytest.mark.asyncio
    async def test_invalid_revision_classification_keeps_prior(self) -> None:
        """Revision returning an invalid classification preserves the prior valid analysis."""
        from unittest.mock import AsyncMock

        from jenkins_job_insight.models import AnalysisDetail
        from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

        # Main AI returns valid classification
        mock_orchestrator = AsyncMock(
            return_value=(
                AnalysisDetail(
                    classification="CODE ISSUE",
                    details="Valid initial analysis",
                ),
                "sig123",
            )
        )

        # Peers disagree in round 1
        peer_disagree = _make_peer_json_response(
            agrees=False,
            classification="PRODUCT BUG",
            reasoning="This is a product bug",
        )
        # Revision returns malformed classification
        revision_response = json.dumps(
            {
                "classification": "maybe product bug",
                "details": "Revised with bad classification",
            }
        )
        # Round 2: peers agree with orchestrator (which should still be
        # "CODE ISSUE" since the invalid revision was rejected)
        peer_agree = _make_peer_json_response(agrees=True, classification="CODE ISSUE")

        call_count = 0

        async def mock_calls(
            prompt,
            *,
            cwd=None,
            ai_provider="",
            ai_model="",
            ai_cli_timeout=None,
            cli_flags=None,
            max_retries=3,
        ):
            nonlocal call_count
            call_count += 1
            # Round 1 peer calls (2 peers)
            if call_count <= 2:
                return (True, peer_disagree)
            # Revision call after round 1
            if call_count == 3:
                return (True, revision_response)
            # Round 2 peer calls
            return (True, peer_agree)

        with (
            patch(
                "jenkins_job_insight.peer_analysis._run_single_ai_analysis",
                mock_orchestrator,
            ),
            patch(
                "jenkins_job_insight.peer_analysis._call_ai_cli_with_retry",
                side_effect=mock_calls,
            ),
        ):
            results = await analyze_failure_group_with_peers(
                failures=[_make_failure()],
                console_context="console output",
                repo_path=None,
                main_ai_provider="claude",
                main_ai_model="claude-sonnet-4-20250514",
                peer_ai_configs=_peer_configs(),
                max_rounds=2,
            )

        analysis = results[0].analysis
        # "maybe product bug" should NOT leak through as the final classification
        assert analysis.classification != "maybe product bug"
        # The prior valid classification should have been preserved
        assert analysis.classification == "CODE ISSUE"
