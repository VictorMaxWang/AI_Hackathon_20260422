from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm.prompts import INTENT_CANDIDATE_SYSTEM_PROMPT


REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_PROMPT_DOC = REPO_ROOT / "docs" / "core_prompt.md"
FENCE_MARKER = "```"
PROMPT_FIRST_LINE = "You are GuardedOps intent-candidate assistant."


def test_core_prompt_doc_quotes_the_shipped_prompt_verbatim() -> None:
    """The doc must carry the prompt byte for byte, not a paraphrase of it.

    Without this, `docs/core_prompt.md` section 2 is correct only for as long
    as a human remembers to re-copy it after editing `app/llm/prompts.py`.
    """

    blocks = _fenced_blocks(_doc_text())
    expected = INTENT_CANDIDATE_SYSTEM_PROMPT.strip()

    assert expected in blocks, (
        "docs/core_prompt.md no longer quotes "
        "app.llm.prompts.INTENT_CANDIDATE_SYSTEM_PROMPT verbatim; "
        "re-copy the prompt into the fenced block in section 2"
    )


def test_core_prompt_doc_holds_exactly_one_copy_of_the_prompt() -> None:
    blocks = _fenced_blocks(_doc_text())
    prompt_blocks = [block for block in blocks if block.startswith(PROMPT_FIRST_LINE)]

    assert len(prompt_blocks) == 1, (
        f"expected exactly one quoted system prompt in {CORE_PROMPT_DOC.name}, "
        f"found {len(prompt_blocks)}; a second copy can drift on its own"
    )


def test_core_prompt_doc_names_the_constant_it_quotes() -> None:
    doc_text = _doc_text()

    assert "app/llm/prompts.py::INTENT_CANDIDATE_SYSTEM_PROMPT" in doc_text


def _doc_text() -> str:
    assert CORE_PROMPT_DOC.is_file(), f"missing core prompt doc: {CORE_PROMPT_DOC}"
    return CORE_PROMPT_DOC.read_text(encoding="utf-8").replace("\r\n", "\n")


def _fenced_blocks(markdown: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] | None = None

    for line in markdown.split("\n"):
        if line.startswith(FENCE_MARKER):
            if current is None:
                current = []
            else:
                blocks.append("\n".join(current).strip())
                current = None
            continue
        if current is not None:
            current.append(line)

    return blocks
