from tools.text_range import select_folded_range_anchors


def test_folded_range_anchors_can_shrink_from_96_to_12():
    start = "START-UNIQUE"
    end = "END-UNIQUE12"
    source = f"{start}{'真' * 100}\n真实内容\n{'实' * 100}{end}"
    submitted = f"{start}{'错' * 100}\n{'模型错误内容' * 20}\n{'误' * 100}{end}"

    result = select_folded_range_anchors(
        source,
        submitted,
        min_text_chars=240,
    )

    assert result["ok"] is True
    assert result["start_text"] == start
    assert result["end_text"] == end
    assert result["details"]["anchor_chars"] == 12
    assert result["details"]["attempted_anchor_chars"] == [96, 48, 24, 12]


def test_folded_range_anchors_stop_when_either_anchor_becomes_ambiguous():
    repeated_start = "S" * 48
    unique_end = "E" * 48
    source = (
        f"{repeated_start}{'甲' * 60}\n"
        f"{repeated_start}{'乙' * 60}\n"
        f"{'丙' * 60}{unique_end}"
    )
    submitted = (
        f"{repeated_start}{'错' * 60}\n"
        f"{'模型错误内容' * 20}\n"
        f"{'丙' * 60}{unique_end}"
    )

    result = select_folded_range_anchors(
        source,
        submitted,
        min_text_chars=240,
    )

    assert result["ok"] is False
    assert result["error"] == "ambiguous_text_range"
    assert result["details"]["anchor_chars"] == 48
    assert result["details"]["start_occurrences"] == 2
    assert result["details"]["attempted_anchor_chars"] == [96, 48]
