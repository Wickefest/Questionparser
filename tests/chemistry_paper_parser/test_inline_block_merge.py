from __future__ import annotations

from questbank.normalize.inline_blocks import merge_inline_notation_blocks
from questbank.types.canonical import ChemistryBlock, MathBlock, TextBlock, TableBlock


def test_merges_inline_chem_and_math_into_one_text_block():
    blocks = [
        TextBlock(value="Two gases,"),
        ChemistryBlock(value="H2S"),
        TextBlock(value="("),
        MathBlock(latex="M_r = 34.1"),
        TextBlock(value=") and"),
        ChemistryBlock(value="SO2"),
        TextBlock(value="("),
        MathBlock(latex="M_r = 64.1"),
        TextBlock(value=") were released from one end of a laboratory."),
    ]
    merged = merge_inline_notation_blocks(blocks)
    assert len(merged) == 1
    assert merged[0].type == "text"
    value = merged[0].value
    assert "H₂S" in value or "H2S" in value
    assert "SO₂" in value or "SO2" in value
    assert "34.1" in value
    assert "were released" in value
    assert "Two gases" in value


def test_keeps_standalone_reaction_as_chemistry_block():
    blocks = [
        TextBlock(value="The Haber process is:"),
        ChemistryBlock(value="N2 + 3H2 -> 2NH3"),
        TextBlock(value="Which statement is correct?"),
    ]
    merged = merge_inline_notation_blocks(blocks)
    assert [b.type for b in merged] == ["text", "chemistry", "text"]
    assert merged[1].value == "N2 + 3H2 -> 2NH3"


def test_keeps_lone_chemistry_block():
    blocks = [ChemistryBlock(value="H2SO4")]
    merged = merge_inline_notation_blocks(blocks)
    assert len(merged) == 1
    assert merged[0].type == "chemistry"
    assert merged[0].value == "H2SO4"


def test_does_not_merge_across_table_or_image():
    blocks = [
        TextBlock(value="Refer to"),
        ChemistryBlock(value="H2O"),
        TableBlock(columns=["A"], rows=[["1"]]),
        TextBlock(value="and then"),
        ChemistryBlock(value="CO2"),
    ]
    merged = merge_inline_notation_blocks(blocks)
    assert merged[0].type == "text"
    assert "H₂O" in merged[0].value or "H2O" in merged[0].value
    assert merged[1].type == "table"
    assert merged[2].type == "text"
