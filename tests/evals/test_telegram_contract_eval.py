from dataclasses import replace

from evals.telegram_contract.corpus import CASES
from evals.telegram_contract.runner import (
    grade_observation,
    reference_observations,
    run_suite,
)


def test_corpus_has_thirty_sanitized_cases():
    assert len(CASES) == 30
    assert len({case.case_id for case in CASES}) == 30


def test_reference_set_proves_grader_happy_path_only():
    results = run_suite(reference_observations())
    assert len(results) == 30
    assert all(item.passed for item in results)


def test_grader_catches_incomplete_and_tool_markup():
    case = next(case for case in CASES if case.public_verdict_required)
    observation = next(item for item in reference_observations() if item.case_id == case.case_id)
    bad = replace(observation, final_text="INCOMPLETE\n<tool_call>terminal</tool_call>")
    result = grade_observation(case, bad)
    assert not result.passed
    assert "public_incomplete" in result.failures
    assert "technical_leak" in result.failures


def test_grader_catches_false_ready_and_extra_calls():
    case = next(case for case in CASES if case.expected_verdict == "BLOCKED")
    observation = next(item for item in reference_observations() if item.case_id == case.case_id)
    bad = replace(
        observation,
        verdict="READY",
        final_text="READY\nГотово.",
        tool_calls=case.max_tool_calls + 1,
    )
    result = grade_observation(case, bad)
    assert not result.passed
    assert any(item.startswith("verdict:") for item in result.failures)
    assert "tool_calls_budget" in result.failures


def test_grader_catches_duplicate_delivery():
    case = CASES[0]
    observation = reference_observations()[0]
    result = grade_observation(case, replace(observation, duplicate_deliveries=1))
    assert result.failures == ("duplicate_delivery",)
