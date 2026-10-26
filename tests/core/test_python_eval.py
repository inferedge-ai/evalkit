from evalkit.contracts.custom_evaluator import EvalCase, ScoreItem
from evalkit.core.custom_eval import EvalError
from evalkit.core.python_eval import PythonEvaluator, score_with_python_evaluator

CASE = EvalCase(id="c", input={"question": "q"}, expected=None, config={"k": 1})


async def test_float_result_becomes_named_score():
    ev = PythonEvaluator(name="cite", kind="evaluator", fn=lambda case, run: 0.5)
    rows = await score_with_python_evaluator(ev, case=CASE, answer="a", output=None, trajectory={})
    assert rows == [ScoreItem(name="cite", score=0.5)]


async def test_async_and_list_results():
    async def fn(case, run):
        assert run.trajectory == {"x": 1} and case.config == {"k": 1}
        return [ScoreItem(name="a", score=1.0), ScoreItem(name="b", score=0.0, label="fail")]

    ev = PythonEvaluator(name="multi", kind="gauge", fn=fn)
    rows = await score_with_python_evaluator(
        ev, case=CASE, answer="a", output={"o": 1}, trajectory={"x": 1}
    )
    assert [r.name for r in rows] == ["a", "b"]


async def test_exception_becomes_eval_error():
    def fn(case, run):
        raise RuntimeError("boom")

    ev = PythonEvaluator(name="bad", kind="evaluator", fn=fn)
    result = await score_with_python_evaluator(ev, case=CASE, answer="", output=None, trajectory={})
    assert isinstance(result, EvalError) and "boom" in result.message
