"""Text generation uses the font installed in the worker image."""

from pathlib import Path

from app.llm import SYSTEM_PROMPT


def test_system_prompt_requires_the_worker_font():
    assert 'font="DejaVu Sans"' in SYSTEM_PROMPT


def test_worker_image_installs_the_prompt_font():
    dockerfile = (Path(__file__).parents[1] / "worker" / "Dockerfile").read_text()

    assert "fontconfig" in dockerfile
    assert "fonts-dejavu-core" in dockerfile
