from __future__ import annotations

import pytest

from eck.capabilities.safe_python import SafePythonExpressionCapability
from eck.domain.models import ActionProposal


@pytest.mark.asyncio
async def test_safe_expression_passes_cases() -> None:
    action = ActionProposal(
        capability="python.safe_expression",
        operation="evaluate",
        payload={
            "expression": "x * x + 1",
            "cases": [
                {"input": 2, "expected": 5},
                {"input": -2, "expected": 5},
            ],
        },
    )
    result = await SafePythonExpressionCapability().execute(action)
    assert result.success
    assert result.output["metrics"]["passed"] == 2


@pytest.mark.asyncio
async def test_safe_expression_rejects_function_calls() -> None:
    action = ActionProposal(
        capability="python.safe_expression",
        operation="evaluate",
        payload={
            "expression": "__import__('os').system('whoami')",
            "cases": [{"input": 1, "expected": 0}],
        },
    )
    result = await SafePythonExpressionCapability().execute(action)
    assert not result.success
    assert "Disallowed syntax" in result.output["error"]


@pytest.mark.asyncio
async def test_safe_expression_rejects_attribute_access() -> None:
    action = ActionProposal(
        capability="python.safe_expression",
        operation="evaluate",
        payload={
            "expression": "x.__class__",
            "cases": [{"input": 1, "expected": int}],
        },
    )
    result = await SafePythonExpressionCapability().execute(action)
    assert not result.success

