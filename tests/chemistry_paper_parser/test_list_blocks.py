from questbank.factory.mapping import ensure_list_blocks
from questbank.normalize.list_blocks import split_numbered_statement_lists
from questbank.types.canonical import ListBlock, TextBlock


def test_split_methylamine_style_numbered_list() -> None:
    text = (
        "Which of the following is/are true about aqueous methylamine?\n"
        "1. It turns litmus paper blue.\n"
        "2. It consists of CH3NH3+ and OH- ions only.\n"
        "3. It forms a blue precipitate with copper(II) sulfate."
    )
    out = split_numbered_statement_lists([TextBlock(type="text", value=text)])
    assert len(out) == 2
    assert isinstance(out[0], TextBlock)
    assert "methylamine" in out[0].value
    assert isinstance(out[1], ListBlock)
    assert out[1].ordered is True
    assert out[1].items == [
        "It turns litmus paper blue.",
        "It consists of CH3NH3+ and OH- ions only.",
        "It forms a blue precipitate with copper(II) sulfate.",
    ]


def test_split_inline_numbered_statements_with_trailing_question() -> None:
    text = (
        "The following statements about dilute sulfuric acid are all correct. "
        "1 The solution reacts with copper(II) oxide, forming a blue solution. "
        "2 The solution turns anhydrous copper(II) sulfate from white to blue. "
        "3 A white precipitate is formed when aqueous barium chloride is added. "
        "4 Addition of Universal Indicator shows that the solution has a pH value of less than 7.0. "
        "Which two statements confirm the acidic nature of the solution?"
    )
    out = split_numbered_statement_lists([TextBlock(type="text", value=text)])
    assert len(out) == 3
    assert isinstance(out[0], TextBlock)
    assert out[0].value.startswith("The following statements")
    assert isinstance(out[1], ListBlock)
    assert out[1].ordered is True
    assert len(out[1].items) == 4
    assert out[1].items[0].startswith("The solution reacts")
    assert isinstance(out[2], TextBlock)
    assert out[2].value.startswith("Which two statements")


def test_split_inline_acids_example() -> None:
    text = (
        "Which of the following must be true for all acids? "
        "1 Their pH value is less than 7. "
        "2 They are completely ionised when dissolved in water. "
        "3 They react with all metals to give hydrogen gas. "
        "4 Dibasic acids are stronger than monobasic acids."
    )
    out = split_numbered_statement_lists([TextBlock(type="text", value=text)])
    assert len(out) == 2
    assert out[0].value == "Which of the following must be true for all acids?"
    assert len(out[1].items) == 4


def test_split_inline_oxidation_states_skips_zero_noise() -> None:
    text = (
        "How does the oxidation state of chlorine change during this reaction? "
        "1 from 0 to -1 2 from 0 to +5 3 from 0 to +7"
    )
    out = split_numbered_statement_lists([TextBlock(type="text", value=text)])
    assert len(out) == 2
    assert out[1].items == ["from 0 to -1", "from 0 to +5", "from 0 to +7"]


def test_split_inline_without_periods_between_items() -> None:
    text = (
        "Three separations are listed. "
        "1 obtaining water from sodium chloride solution "
        "2 obtaining iodine from a mixture of iodine and sodium chloride "
        "3 obtaining solid sodium chloride from aqueous sodium chloride "
        "Which techniques would be involved in these separations?"
    )
    out = split_numbered_statement_lists([TextBlock(type="text", value=text)])
    assert len(out) == 3
    assert len(out[1].items) == 3
    assert out[2].value.startswith("Which techniques")


def test_split_ignores_experiments_1_and_2() -> None:
    text = "Experiments 1 and 2 are set up to demonstrate the diffusion of gases."
    out = split_numbered_statement_lists([TextBlock(type="text", value=text)])
    assert len(out) == 1
    assert isinstance(out[0], TextBlock)


def test_split_ignores_ph_decimal_false_positive() -> None:
    text = (
        "A farmer wishes to grow some ceanothus flowers. "
        "The pH of his soil is 5.1. Ceanothus flowers are known to grow best at around pH 7.2. "
        "Which substance should he add to his soil for the ceanothus to grow well?"
    )
    out = split_numbered_statement_lists([TextBlock(type="text", value=text)])
    assert len(out) == 1
    assert isinstance(out[0], TextBlock)


def test_repair_decimal_false_split_and_glued_trail() -> None:
    from questbank.normalize.list_blocks import repair_list_content

    bad = [
        {"type": "text", "value": "The pH of his soil is 5."},
        {
            "type": "list",
            "ordered": True,
            "items": [
                "Ceanothus flowers are known to grow best at around pH 7.",
                "Which substance should he add to his soil for the ceanothus to grow well?",
            ],
        },
    ]
    out = ensure_list_blocks(bad)
    assert len(out) == 1
    assert out[0]["type"] == "text"
    assert "5.1" in out[0]["value"]
    assert "7.2" in out[0]["value"]

    glued = [
        {"type": "text", "value": "Three separations are listed."},
        {
            "type": "list",
            "ordered": True,
            "items": [
                "obtaining water from sodium chloride solution",
                "obtaining iodine from a mixture of iodine and sodium chloride",
                "obtaining solid sodium chloride from aqueous sodium chloride Which techniques would be involved in these separations?",
            ],
        },
    ]
    fixed = ensure_list_blocks(glued)
    assert fixed[1]["type"] == "list"
    assert "Which" not in fixed[1]["items"][-1]
    assert fixed[2]["value"].startswith("Which techniques")


def test_split_leaves_plain_text_alone() -> None:
    block = TextBlock(type="text", value="Nitrogen fertiliser is used to increase crop yield.")
    out = split_numbered_statement_lists([block])
    assert out == [block]


def test_ensure_list_blocks_upgrades_dict_content() -> None:
    content = [
        {
            "type": "text",
            "value": (
                "Solid X has the following properties. "
                "1 It is soluble in water. "
                "2 It gives off a gas when added to dilute nitric acid. "
                "3 When warmed with sodium hydroxide solution, a gas is evolved. "
                "Which of the following is X?"
            ),
        }
    ]
    out = ensure_list_blocks(content)
    assert out[0]["type"] == "text"
    assert out[1]["type"] == "list"
    assert out[1]["ordered"] is True
    assert len(out[1]["items"]) == 3
    assert out[2]["type"] == "text"
    assert out[2]["value"].startswith("Which of the following is X")
