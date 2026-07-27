"""EasyCAD quality harness (bench-SPEC).

Answers four questions the product launch depends on: what fraction of prompts
produce a correct model, what systematically breaks, whether a change helped,
and whether the repair loop pays for itself.

Run with `python -m bench <command>` (add `bench/src` to PYTHONPATH), or via the
`make bench ARGS="..."` target.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
