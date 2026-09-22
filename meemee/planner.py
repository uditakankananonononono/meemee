from __future__ import annotations

import re

from .types import Plan, PlanStep


class TaskPlanner:
    """Creates deterministic, inspectable plans without requiring a second model call."""

    ACTIONS = re.compile(r"\b(?:then|after that|next|and then|finally)\b|[.;]\s+", re.IGNORECASE)

    def plan(self, goal: str, max_steps: int = 8) -> Plan:
        chunks = [chunk.strip(" -") for chunk in self.ACTIONS.split(goal) if chunk.strip(" -")]
        if not chunks:
            chunks = [goal.strip()]
        chunks = chunks[:max_steps]
        steps = []
        for index, description in enumerate(chunks, 1):
            steps.append(PlanStep(
                id=f"step-{index}",
                description=description,
                depends_on=[f"step-{index - 1}"] if index > 1 else [],
            ))
        return Plan(goal=goal, steps=steps)


    def revise(self, current: Plan, descriptions: list[str]) -> Plan:
        """Replace pending work with a bounded, validated sequential plan."""
        cleaned = [item.strip() for item in descriptions if item.strip()][:8]
        if not cleaned:
            raise ValueError("replan must contain at least one non-empty step")
        return Plan(goal=current.goal, steps=[
            PlanStep(id=f"revision-step-{index}", description=description,
                     depends_on=[f"revision-step-{index - 1}"] if index > 1 else [])
            for index, description in enumerate(cleaned, 1)
        ])
