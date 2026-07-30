"""Prompt-engineering tests.

The brief requires improving a prompt from trace insights, which only works if
every prompt is versioned and the version reaches LangSmith. These tests keep
that machinery honest.

Owner: Team Member 1 (prompt engineering).
"""

from __future__ import annotations

import pytest

from supplychain import prompts
from supplychain.observability import run_config


def test_every_agent_has_a_prompt():
    for name in (
        "intake",
        "incident_analysis",
        "shipment",
        "inventory",
        "supplier",
        "recovery",
        "supervisor",
        "responder",
    ):
        assert name in prompts.PROMPTS
        assert len(prompts.PROMPTS[name]) > 200, f"{name} prompt looks like a stub"


def test_every_prompt_is_versioned():
    for name in prompts.PROMPTS:
        assert name in prompts.PROMPT_VERSIONS, f"{name} has no version"
        assert prompts.prompt_version(name) != "unversioned"


def test_shared_context_is_versioned_too():
    assert "shared_context" in prompts.PROMPT_VERSIONS


def test_unknown_prompt_reports_unversioned():
    assert prompts.prompt_version("nope") == "unversioned"


def test_every_prompt_carries_the_shared_client_context():
    # Agents must all know the identifier conventions and the scenario date.
    for name, prompt in prompts.PROMPTS.items():
        assert "NovaRetail" in prompt, f"{name} lost the shared context"
        assert prompts.SCENARIO_DATE in prompt, f"{name} lost the scenario date"


def test_changelog_covers_the_current_version_of_each_changed_prompt():
    for name, entries in prompts.CHANGELOG.items():
        versions = [version for version, _ in entries]
        current = prompts.PROMPT_VERSIONS[name]
        assert current in versions, (
            f"{name} is at {current} but the changelog stops at {versions[-1]}"
        )
        for _, description in entries:
            assert description.strip(), f"{name} has an empty changelog entry"


@pytest.mark.parametrize("name", ["intake", "incident_analysis"])
def test_improved_prompts_are_past_v1(name):
    """These two were revised from trace insights; the version must reflect it."""
    assert prompts.PROMPT_VERSIONS[name] != "v1"


def test_intake_prompt_includes_worked_examples():
    prompt = prompts.INTAKE_PROMPT
    assert "Worked examples" in prompt
    # Few-shot coverage for the cases traces showed going wrong.
    assert "shp 2026 2" in prompt              # sloppy identifier
    assert "that one" in prompt                # referring expression
    assert "is_supported: false" in prompt     # unsupported request


def test_incident_prompt_forbids_claiming_writes():
    prompt = prompts.INCIDENT_ANALYSIS_PROMPT
    assert "find_related_incidents" in prompt
    assert "cannot create or escalate" in prompt


def test_recovery_prompt_states_the_approval_boundary():
    assert "approval" in prompts.RECOVERY_PROMPT.lower()
    assert "Never state that an incident has been created" in prompts.RECOVERY_PROMPT


def test_prompt_versions_reach_langsmith_metadata():
    metadata = run_config("thread-test")["metadata"]
    for name, version in prompts.prompt_versions().items():
        assert metadata[f"prompt_{name}"] == version


def test_run_config_metadata_identifies_the_conversation_and_model():
    config = run_config("thread-abc", tags=["eval"], metadata={"case": "delay"})
    assert config["configurable"]["thread_id"] == "thread-abc"
    assert "eval" in config["tags"]
    assert config["metadata"]["case"] == "delay"
    assert config["metadata"]["provider"] == "google_genai"
