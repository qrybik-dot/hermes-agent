"""Ordered deterministic stages that may finish a turn before model execution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class PreModelStage:
    name: str
    run: Callable[[], Any | None]
    enabled: bool = True


@dataclass(frozen=True)
class PreModelOutcome:
    stage: str
    response: Any


def run_pre_model_pipeline(stages: Iterable[PreModelStage]) -> PreModelOutcome | None:
    """Run enabled stages in order and stop at the first concrete response."""
    for stage in stages:
        if not stage.enabled:
            continue
        response = stage.run()
        if response is not None:
            return PreModelOutcome(stage=stage.name, response=response)
    return None
