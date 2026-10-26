from evalkit.core.python_eval import PythonEvaluator
from evalkit.sdk.evaluators import evaluator


def test_decorator_builds_python_evaluator():
    @evaluator(name="cite", kind="gauge", requires_trajectory=True)
    def cite(case, run):
        return 1.0

    assert isinstance(cite, PythonEvaluator) and cite.name == "cite" and cite.kind == "gauge"
    assert cite.requires_trajectory
    assert cite(None, None) == 1.0


def test_bare_decorator_uses_function_name():
    @evaluator
    def answer_length(case, run):
        return 0.5

    assert answer_length.name == "answer_length" and answer_length.kind == "evaluator"
