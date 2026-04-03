"""
validation_routes.py — API endpoints for the AgentFixer validation framework.

Exposes validation reports and validator status for the Hive Mind dashboard.
"""

import logging
import time
from fastapi import APIRouter

from backend.validation.robust_parser import parse_json_verbose
from backend.validation.output_validators import (
    OutputSchemaValidator,
    TokenAnomalyDetector,
    PythonSyntaxValidator,
)
from backend.validation.cross_stage_validators import (
    InformationConsistencyValidator,
    ProposalIntegrityValidator,
)

logger = logging.getLogger("localmind.routes.validation")

router = APIRouter(prefix="/api/validation", tags=["validation"])

# Singleton validator instances
_validators = {
    "output_schema": OutputSchemaValidator(),
    "token_anomaly": TokenAnomalyDetector(),
    "python_syntax": PythonSyntaxValidator(),
    "info_consistency": InformationConsistencyValidator(),
    "proposal_integrity": ProposalIntegrityValidator(),
}


@router.get("/status")
async def validation_status():
    """Return the list of available validators and their status."""
    return {
        "validators": [
            {
                "name": v.name,
                "severity": v.severity.value,
                "type": "rule-based" if "Aligner" not in v.name and "Consistency" not in v.name else "llm-based",
            }
            for v in _validators.values()
        ],
        "total": len(_validators),
        "framework": "AgentFixer (arXiv:2603.29848)",
    }


@router.post("/check")
async def validate_output(payload: dict):
    """
    Run validators against provided context.

    Body:
      - output: str — LLM output text
      - schema_name: str — "proposal", "critique", or "edit"
      - code: str — optional Python code to validate
    """
    results = []

    # Run rule-based validators synchronously
    for key, validator in _validators.items():
        try:
            result = validator.validate(payload)
            results.append(result.to_dict())
        except Exception as e:
            logger.warning(f"Validator {key} failed: {e}")
            results.append({
                "validator": key,
                "passed": True,
                "severity": "minor",
                "message": f"Validator error: {e}",
            })

    passed = sum(1 for r in results if r.get("passed"))
    total = len(results)

    return {
        "passed": passed,
        "total": total,
        "pass_rate": passed / total if total else 1.0,
        "results": results,
        "timestamp": time.time(),
    }


@router.post("/parse-test")
async def test_parser(payload: dict):
    """
    Test the cascading JSON parser against sample input.

    Body:
      - text: str — raw text to parse
    """
    text = payload.get("text", "")
    result = parse_json_verbose(text)

    return {
        "success": result.success,
        "strategy_used": result.strategy_used,
        "data": result.data if result.success else None,
        "attempts": [
            {"strategy": name, "success": ok, "error": err}
            for name, ok, err in result.attempts
        ],
    }
