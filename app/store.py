"""In-memory step store for a single CadQuery-chat session.

MVP: one global session with linear-plus-revert history. Each step captures
the code + STL + prompts + execution outcome. Filesystem persistence and true
branching are deferred (Phase 2 in SPEC11); `parent_id` is recorded now so the
timeline can render branch points later.
"""

import time
from dataclasses import asdict, dataclass
from itertools import count


@dataclass
class Step:
    id: int
    kind: str  # "initial" | "chat" | "manual"
    original_prompt: str | None
    refined_prompt: str | None
    code: str
    stl_base64: str | None
    geometry_info: str | None
    success: bool
    error: str | None
    parent_id: int | None
    created_at: float

    def to_public(self, include_stl: bool = True) -> dict:
        data = asdict(self)
        if not include_stl:
            data.pop("stl_base64", None)
        return data


class SessionStore:
    """Holds the ordered steps of the current session and the current pointer."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._steps: dict[int, Step] = {}
        self._order: list[int] = []
        self._ids = count()
        self.current_id: int | None = None

    def add(
        self,
        *,
        kind: str,
        code: str,
        success: bool,
        original_prompt: str | None = None,
        refined_prompt: str | None = None,
        stl_base64: str | None = None,
        geometry_info: str | None = None,
        error: str | None = None,
        make_current: bool = True,
    ) -> Step:
        step = Step(
            id=next(self._ids),
            kind=kind,
            original_prompt=original_prompt,
            refined_prompt=refined_prompt,
            code=code,
            stl_base64=stl_base64,
            geometry_info=geometry_info,
            success=success,
            error=error,
            parent_id=self.current_id,
            created_at=time.time(),
        )
        self._steps[step.id] = step
        self._order.append(step.id)
        if make_current:
            self.current_id = step.id
        return step

    def get(self, step_id: int) -> Step | None:
        return self._steps.get(step_id)

    def current(self) -> Step | None:
        if self.current_id is None:
            return None
        return self._steps.get(self.current_id)

    def all(self) -> list[Step]:
        return [self._steps[i] for i in self._order]

    def revert(self, step_id: int) -> Step | None:
        if step_id not in self._steps:
            return None
        self.current_id = step_id
        return self._steps[step_id]
