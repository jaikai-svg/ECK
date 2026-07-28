from __future__ import annotations

import json
import sys

import httpx


def main() -> int:
    try:
        with httpx.Client(base_url="http://127.0.0.1:8420", timeout=180) as client:
            health = client.get("/health")
            health.raise_for_status()
            response = client.post("/v1/demos/all")
            response.raise_for_status()
            result = response.json()
        checks = {
            "health": health.json()["status"] == "ok",
            "persistence": result["persistence"]["acceptance"],
            "safe_code": result["safe_code"]["status"] == "verified_success",
            "gridworld_learning": result["gridworld"]["learning_measure"][
                "fewer_steps_after_experience"
            ],
        }
        print(json.dumps({"checks": checks, "passed": all(checks.values())}, indent=2))
        return 0 if all(checks.values()) else 1
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
