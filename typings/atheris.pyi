from __future__ import annotations

from typing import Any, Callable, Sequence

def Setup(
    argv: Sequence[str],
    test_one_input: Callable[[bytes], None],
    enable_python_coverage: bool = True,
    enable_python_opcode_coverage: bool | None = None,
    custom_mutator: Callable[..., Any] | None = None,
    custom_crossover: Callable[..., Any] | None = None,
) -> None: ...
def Fuzz() -> None: ...
