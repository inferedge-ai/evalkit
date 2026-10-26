from evalkit.store.base import CaseView, Selection, select_cases


def case(case_id: str, split_tags: list[str]) -> CaseView:
    return CaseView(case_ref=1, case_id=case_id, content_hash="h", input={}, split_tags=split_tags)


def test_tag_selection_sees_overlay_tags():
    tagged = case("case-1", ["finance", "client-priority"])
    assert select_cases(
        [tagged, case("case-2", ["finance"])], Selection(tag="client-priority")
    ) == [tagged]


def test_all_and_case_ids():
    cases = [case("a", []), case("b", [])]
    assert select_cases(cases, Selection(all=True)) == cases
    assert select_cases(cases, Selection(case_ids=["b"])) == [cases[1]]
