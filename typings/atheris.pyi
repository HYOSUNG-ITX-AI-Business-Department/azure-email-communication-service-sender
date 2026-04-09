from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

def Setup(
    args: Sequence[str],
    test_one_input: Callable[[bytes], None],
    internal_libfuzzer: Optional[bool] = None,
    custom_mutator: Optional[Callable[..., Any]] = None,
    custom_crossover: Optional[Callable[..., Any]] = None,
    enable_python_coverage: bool = True,
    enable_python_opcode_coverage: bool = True,
) -> None: ...
def Fuzz() -> None: ...
