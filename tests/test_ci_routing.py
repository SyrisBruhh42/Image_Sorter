"""Static event contract; hosted GitHub execution is a separate acceptance gate."""
from pathlib import Path


def test_prs_are_unfiltered_and_cutover_push_refs_are_covered():
    workflow = (Path(__file__).parents[1] / ".github/workflows/ci.yml").read_text()
    event_block = workflow.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]
    # Deliberately exact: introducing paths, PR branch filters, or extra nesting
    # requires reviewing the cutover event contract, not silently losing checks.
    assert event_block.strip().splitlines() == [
        "push:",
        "    branches: [main, integration/unified-main, feature/image-sorter-app-4050765199291038722]",
        "  pull_request:",
        "  workflow_dispatch:",
    ]
    aggregate = workflow.split("\n  required:\n", 1)[1]
    assert "    name: Image Sorter required CI\n" in aggregate
    assert "    if: ${{ always() }}\n" in aggregate
    assert "    needs: [quality, x11-smoke, native-portability, linux-artifacts, optional-ai-integration]\n" in aggregate
