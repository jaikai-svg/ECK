from __future__ import annotations

import ast
from typing import Any

from eck.capabilities.base import Capability, CapabilityDefinition
from eck.core.time import utc_now
from eck.domain.enums import EvidenceSource, RiskLevel
from eck.domain.models import ActionProposal, CapabilityResult, Evidence


class UnsafeExpressionError(ValueError):
    pass


class _ExpressionValidator(ast.NodeVisitor):
    allowed = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.BoolOp,
        ast.Compare,
        ast.IfExp,
        ast.Name,
        ast.Load,
        ast.Constant,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.USub,
        ast.UAdd,
        ast.Not,
        ast.And,
        ast.Or,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
    )

    def generic_visit(self, node: ast.AST) -> None:
        if not isinstance(node, self.allowed):
            raise UnsafeExpressionError(f"Disallowed syntax: {type(node).__name__}")
        super().generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id != "x":
            raise UnsafeExpressionError(f"Only the variable 'x' is allowed, got {node.id!r}.")

    def visit_Constant(self, node: ast.Constant) -> None:
        if not isinstance(node.value, (int, float, bool)):
            raise UnsafeExpressionError("Only numeric and Boolean constants are allowed.")


class SafePythonExpressionCapability(Capability):
    definition = CapabilityDefinition(
        name="python.safe_expression",
        description="Evaluate one arithmetic expression against deterministic test cases.",
        default_risk=RiskLevel.LOW,
        deterministic=True,
    )

    async def execute(self, action: ActionProposal) -> CapabilityResult:
        started = utc_now()
        expression = str(action.payload.get("expression", ""))
        cases = action.payload.get("cases", [])
        evidence: tuple[Evidence, ...]
        try:
            tree = ast.parse(expression, mode="eval")
            _ExpressionValidator().visit(tree)
            code = compile(tree, "<eck-safe-expression>", "eval")
            results: list[dict[str, Any]] = []
            for index, case in enumerate(cases):
                x = case["input"]
                expected = case["expected"]
                actual = eval(code, {"__builtins__": {}}, {"x": x})  # noqa: S307
                results.append(
                    {
                        "index": index,
                        "input": x,
                        "expected": expected,
                        "actual": actual,
                        "passed": actual == expected,
                    }
                )
            passed = sum(int(item["passed"]) for item in results)
            failed = len(results) - passed
            success = bool(results) and failed == 0
            output = {
                "expression": expression,
                "metrics": {
                    "passed": passed,
                    "failed": failed,
                    "total": len(results),
                    "all_passed": success,
                },
                "cases": results,
                "skill_fingerprint": f"python.safe_expression:{expression}",
                "skill_name": f"Safe expression: {expression}",
                "skill_procedure": {"expression": expression},
            }
            evidence = (
                Evidence(
                    source=EvidenceSource.UNIT_TEST,
                    claim=f"{passed}/{len(results)} deterministic test cases passed.",
                    payload={"cases": results},
                ),
                Evidence(
                    source=EvidenceSource.FORMAL_CHECK,
                    claim="The expression passed the v0.1 AST allowlist.",
                    payload={"allowed_variable": "x"},
                ),
            )
        except (SyntaxError, UnsafeExpressionError, KeyError, TypeError, ArithmeticError) as exc:
            success = False
            output = {
                "expression": expression,
                "metrics": {
                    "passed": 0,
                    "failed": len(cases),
                    "total": len(cases),
                    "all_passed": False,
                },
                "error": str(exc),
            }
            evidence = (
                Evidence(
                    source=EvidenceSource.FORMAL_CHECK,
                    claim="The expression was rejected or failed during deterministic evaluation.",
                    payload={"error": str(exc)},
                ),
            )
        finished = utc_now()
        return CapabilityResult(
            action_id=action.action_id,
            capability=self.definition.name,
            success=success,
            output=output,
            evidence=evidence,
            reversible=True,
            cost_units=max(1, len(cases)),
            started_at=started,
            finished_at=finished,
        )
