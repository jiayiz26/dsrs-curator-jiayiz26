"""Run the documented example questions and write output/agent_usage.json."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.answer import main
from agents.llm import usage


QUESTIONS = [
    "Which manager held the largest Apple position in 2026 Q2?",
    "Which manager added the most Nvidia shares between 2026 Q1 and 2026 Q2?",
    "How many distinct issuers did Renaissance Technologies LLC report in 2026 Q2?",
    "What was the total reported value of Citadel Advisors LLC's holdings in 2026 Q1?",
    "Which managers in the roster filed a 13F-NT instead of a 13F-HR for 2026 Q2?",
    "Did Pershing Square Capital Management report any Microsoft holdings directly in 2026 Q2?",
    "Which manager reported the most call options in 2026 Q2?",
    "What was Third Point LLC's largest position by value in 2026 Q1, and what was it?",
    "Which manager held Tesla in both 2026 Q1 and 2026 Q2, and did the position grow or shrink?",
    "What was the average portfolio value across all managers in 2026 Q3?",
]


def main_script() -> None:
    before = usage()
    questions: list[dict[str, object]] = []
    for question in QUESTIONS:
        started = time.monotonic()
        main(question)
        after = usage()
        questions.append({
            "question": question,
            "calls": after["calls"] - before["calls"],
            "prompt_tokens": after["prompt_tokens"] - before["prompt_tokens"],
            "completion_tokens": after["completion_tokens"] - before["completion_tokens"],
            "elapsed_seconds": round(time.monotonic() - started, 3),
        })
        before = after
    totals = usage()
    payload = {
        "questions": questions,
        "totals": {
            "calls": totals["calls"],
            "prompt_tokens": totals["prompt_tokens"],
            "completion_tokens": totals["completion_tokens"],
        },
    }
    path = Path(__file__).resolve().parents[1] / "output" / "agent_usage.json"
    path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    main_script()
