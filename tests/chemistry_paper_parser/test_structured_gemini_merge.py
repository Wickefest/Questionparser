from questbank.parsers.chemistry.parse_structured_questions_gemini import (
    flatten_structured_parts,
    sanitize_package_parts,
    _merge_parts,
)
from questbank.types.structured import StructuredPart


def _part(label, prompt="", marks=None, children=None, visuals=None):
    p = StructuredPart(
        label=label,
        prompt=prompt,
        marks=marks,
        children=children or [],
        visuals=visuals or [],
    )
    return p


def test_continuation_romans_attach_under_last_letter():
    existing = [
        _part("a", children=[_part("i", "What type of polymerisation?", 1)]),
        _part(
            "b",
            "Polyesters are polymers used in the manufacture of numerous products.",
            2,
            visuals=[{"asset": "compound-p.png", "role": "structure"}],
        ),
    ]
    incoming = [
        _part("i", "State the observation when compound P reacts with aqueous bromine.", 2),
        _part("ii", "Draw the full structural formula when compound P reacts with excess ethanol.", 2),
        _part("iii", "State the condition for the reaction in (ii) above.", 1),
    ]
    merged = sanitize_package_parts("Viton elastomer is a copolymer.", _merge_parts(existing, incoming))
    nav = [x["label"] for x in flatten_structured_parts(merged)]
    assert nav == ["a(i)", "b", "b(i)", "b(ii)", "b(iii)"]
    assert merged[1].visuals[0]["asset"] == "compound-p.png"


def test_empty_a_shell_is_skipped_in_nav():
    parts = sanitize_package_parts(
        "Viton",
        [
            _part(
                "a",
                "",
                children=[
                    _part("i", "What type of polymerisation does it undergo?", 1),
                    _part("ii", "Draw the structures of the monomers.", 2),
                ],
            )
        ],
    )
    assert [x["label"] for x in flatten_structured_parts(parts)] == ["a(i)", "a(ii)"]
