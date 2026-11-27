
"""AutoGen dispute-review adapter.

AutoGen reviews the deterministic invoice result but never changes it.
"""

import asyncio
import json
import logging
import os
from typing import Any

from app.errors import AdapterBadOutput, AdapterError, AdapterTimeout

logger = logging.getLogger(__name__)


def _mock_review(
    case_facts: dict[str, Any],
    rule_result: dict[str, Any],
) -> dict[str, Any]:
    decision = rule_result.get("decision", "UNKNOWN")
    flags = rule_result.get("flags", [])
    concerns = [str(flag) for flag in flags] if isinstance(flags, list) else []

    if decision == "AUTO_CLEAR" and not concerns:
        verdict = "AGREE"
        summary = "The verifier agrees with the rule-based result."
    else:
        verdict = "CHALLENGE"
        summary = "The review recommends additional human attention."

    return {
        "verdict": verdict,
        "summary": summary,
        "concerns": concerns,
        "transcript": [
            "Verifier reviewed invoice facts and rule result.",
            "Skeptical Reviewer checked for overlooked risks.",
            f"Mock conclusion: {verdict}.",
        ],
        "model_calls": 0,
    }


async def _real_review(
    case_facts: dict[str, Any],
    rule_result: dict[str, Any],
) -> dict[str, Any]:
    from autogen_agentchat.agents import AssistantAgent
    from autogen_agentchat.conditions import MaxMessageTermination
    from autogen_agentchat.teams import RoundRobinGroupChat
    from autogen_ext.models.openai import OpenAIChatCompletionClient

    model_client = OpenAIChatCompletionClient(
        model=os.getenv("MODEL_NAME", "gpt-4o-mini"),
        api_key=os.getenv("OPENAI_API_KEY"),
    )

    verifier = AssistantAgent(
        name="Verifier",
        model_client=model_client,
        system_message=(
            "Review the invoice facts and deterministic rule result. "
            "Identify possible inconsistencies or overlooked risks. "
            "Do not change the rule-based decision."
        ),
    )

    reviewer = AssistantAgent(
        name="SkepticalReviewer",
        model_client=model_client,
        system_message=(
            "Critically review the Verifier's findings. "
            "Look for overlooked risks. The deterministic rules "
            "are final and must never be changed."
        ),
    )

    team = RoundRobinGroupChat(
        [verifier, reviewer],
        termination_condition=MaxMessageTermination(4),
    )

    task = f"""
Review this invoice case.

Invoice facts:
{json.dumps(case_facts, indent=2, default=str)}

Deterministic rule result:
{json.dumps(rule_result, indent=2, default=str)}

After your discussion, the final reviewer must provide JSON only:
{{
  "verdict": "AGREE or CHALLENGE",
  "summary": "short explanation",
  "concerns": ["concern 1", "concern 2"]
}}

AGREE means no additional concern was found.
CHALLENGE means a possible concern needs human attention.
Never change the deterministic invoice decision.
"""

    timeout_seconds = int(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
    result = await asyncio.wait_for(team.run(task=task), timeout=timeout_seconds)

    transcript = [
        f"{message.source}: {message.content}"
        for message in result.messages
        if hasattr(message, "content") and isinstance(message.content, str)
    ]

    final_text = result.messages[-1].content

    if not isinstance(final_text, str):
        raise AdapterBadOutput("AutoGen returned non-text output.")

    # Remove optional Markdown code fences
    final_text = final_text.strip()
    if final_text.startswith("```"):
        final_text = final_text.replace("```json", "", 1)
        final_text = final_text.replace("```", "").strip()

    try:
        parsed = json.loads(final_text)
    except json.JSONDecodeError as exc:
        raise AdapterBadOutput("AutoGen returned invalid JSON.") from exc

    if parsed.get("verdict") not in {"AGREE", "CHALLENGE"}:
        raise AdapterBadOutput("AutoGen returned an invalid verdict.")

    if not isinstance(parsed.get("summary"), str):
        raise AdapterBadOutput("AutoGen summary is invalid.")

    if not isinstance(parsed.get("concerns"), list):
        raise AdapterBadOutput("AutoGen concerns are invalid.")

    return {
        "verdict": parsed["verdict"],
        "summary": parsed["summary"],
        "concerns": parsed["concerns"],
        "transcript": transcript,
        "model_calls": len(transcript),
    }


def run_dispute_review(
    case_facts: dict[str, Any],
    rule_result: dict[str, Any],
    correlation_id: str = "",
    mock: bool | None = None,
) -> dict[str, Any]:
    force_failure = os.getenv("FORCE_ADAPTER_FAILURE", "").strip().lower()

    if force_failure == "timeout":
        raise AdapterTimeout("Forced AutoGen adapter timeout.")

    if force_failure == "bad_json":
        raise AdapterBadOutput("Forced invalid AutoGen output.")

    if mock is None:
        mock = os.getenv("USE_MOCK_LLM", "1").strip() == "1"

    logger.info(
        "Starting AutoGen review",
        extra={"correlation_id": correlation_id},
    )

    if mock:
        return _mock_review(case_facts, rule_result)

    if not os.getenv("OPENAI_API_KEY"):
        raise AdapterError("OPENAI_API_KEY is missing.")

    try:
        return asyncio.run(_real_review(case_facts, rule_result))
    except asyncio.TimeoutError as exc:
        raise AdapterTimeout("AutoGen review timed out.") from exc
    except (AdapterBadOutput, AdapterTimeout):
        raise
    except Exception as exc:
        logger.exception("AutoGen review failed")
        raise AdapterError(f"AutoGen review failed: {exc}") from exc
