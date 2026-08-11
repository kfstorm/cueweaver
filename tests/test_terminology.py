import pytest

from cueweaver.terminology import (
    TerminologyConflictError,
    filter_terminology_for_text,
)


def test_exact_phrase_is_selected():
    result = filter_terminology_for_text(
        {"Police Bureau": "捕盗厅"},
        "He went to the Police Bureau.",
    )

    assert result.terminology == {"Police Bureau": "捕盗厅"}
    assert result.occurrences == {"Police Bureau": 1}


def test_matching_is_case_insensitive():
    result = filter_terminology_for_text({"Dong Yi": "同伊"}, "Where is DONG YI?")

    assert result.terminology == {"Dong Yi": "同伊"}


def test_matching_respects_token_boundaries():
    result = filter_terminology_for_text({"King": "王"}, "The kingdom is unstable.")

    assert result.terminology == {}


def test_hyphen_normalization_matches_spaces():
    result = filter_terminology_for_text(
        {"Jang Hee-jae": "张希载"},
        "Jang Hee Jae returned.",
    )

    assert result.terminology == {"Jang Hee-jae": "张希载"}


@pytest.mark.parametrize("dash", ["‐", "‑", "‒", "–", "—", "―"])
def test_unicode_dash_normalization_matches(dash):
    result = filter_terminology_for_text(
        {"Jang Hee-jae": "张希载"},
        f"Jang{dash}Hee{dash}jae returned.",
    )

    assert result.terminology == {"Jang Hee-jae": "张希载"}


def test_longest_phrase_wins_per_occurrence():
    result = filter_terminology_for_text(
        {
            "Queen": "王妃",
            "Queen Dowager": "大妃",
            "Queen Dowager Hyeonryeol": "明圣王后",
        },
        "Queen Dowager Hyeonryeol entered.",
    )

    assert result.terminology == {"Queen Dowager Hyeonryeol": "明圣王后"}
    assert result.occurrences == {"Queen Dowager Hyeonryeol": 1}


def test_longest_phrase_does_not_suppress_shorter_phrase_elsewhere():
    result = filter_terminology_for_text(
        {
            "Queen": "王妃",
            "Queen Dowager": "大妃",
            "Queen Dowager Hyeonryeol": "明圣王后",
        },
        "Queen Dowager Hyeonryeol entered. Later, the Queen left.",
    )

    assert result.terminology == {
        "Queen Dowager Hyeonryeol": "明圣王后",
        "Queen": "王妃",
    }
    assert result.occurrences == {
        "Queen Dowager Hyeonryeol": 1,
        "Queen": 1,
    }


def test_multiple_independent_terms_are_selected():
    result = filter_terminology_for_text(
        {"Dong Yi": "同伊", "Police Bureau": "捕盗厅"},
        "Dong Yi went to the Police Bureau.",
    )

    assert result.terminology == {"Dong Yi": "同伊", "Police Bureau": "捕盗厅"}


def test_semantic_similarity_does_not_match():
    result = filter_terminology_for_text(
        {"Censor": "持平", "Censorate": "司宪府"},
        "The Justice Officer went to the Prefecture Office.",
    )

    assert result.terminology == {}


def test_irrelevant_series_terminology_does_not_leak():
    result = filter_terminology_for_text(
        {"Gwi-in": "贵人", "Mama": "娘娘", "Dong Yi": "同伊"},
        "Dong Yi spoke with Lady Jang.",
    )

    assert result.terminology == {"Dong Yi": "同伊"}


def test_repeated_occurrences_are_counted():
    result = filter_terminology_for_text(
        {"Dong Yi": "同伊"},
        "Dong Yi saw Dong Yi, and Dong Yi waved.",
    )

    assert result.terminology == {"Dong Yi": "同伊"}
    assert result.occurrences == {"Dong Yi": 3}


def test_same_normalized_source_and_target_is_allowed():
    result = filter_terminology_for_text(
        {"Jang Hee-jae": "张希载", "Jang Hee Jae": "张希载"},
        "Jang Hee Jae returned.",
    )

    assert result.terminology == {
        "Jang Hee-jae": "张希载",
        "Jang Hee Jae": "张希载",
    }
    assert result.occurrences == {"Jang Hee-jae": 1, "Jang Hee Jae": 1}


def test_same_normalized_source_and_conflicting_target_is_rejected():
    with pytest.raises(
        TerminologyConflictError,
        match=r"Jang Hee-jae.*张希载.*Jang Hee Jae.*张熙载.*\('jang', 'hee', 'jae'\)",
    ):
        filter_terminology_for_text(
            {"Jang Hee-jae": "张希载", "Jang Hee Jae": "张熙载"},
            "Jang Hee Jae returned.",
        )


def test_subtitle_markup_is_ignored():
    result = filter_terminology_for_text(
        {"Dong Yi": "同伊"},
        r"<i>Dong Yi</i> {\an8} returned.",
    )

    assert result.terminology == {"Dong Yi": "同伊"}


def test_empty_source_returns_empty_result():
    result = filter_terminology_for_text({"Dong Yi": "同伊"}, "")

    assert result.terminology == {}
    assert result.occurrences == {}


def test_empty_glossary_returns_empty_result():
    result = filter_terminology_for_text({}, "Dong Yi")

    assert result.terminology == {}
    assert result.occurrences == {}


def test_empty_normalized_sources_are_ignored():
    result = filter_terminology_for_text({"<i>": "ignored", "  ": "ignored"}, "text")

    assert result.terminology == {}
