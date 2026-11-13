import ast
import sys
from pathlib import Path

import griffe

ROOT = Path(__file__).parent.parent
OUTPUT = ROOT / "docs" / "user" / "sdk-reference.md"

INTRO = (
    "The Python SDK runs experiments from your own code. Install `evalkit-py` at your "
    "server's version (see [Install](install.md)) and import from `evalkit.sdk`. "
    "[SDK runs](sdk-runs.md) walks through it."
)

SECTIONS = [
    (
        "Running experiments",
        ["run_experiment", "arun_experiment", "evaluate_experiment", "aevaluate_experiment"],
    ),
    ("Tracing", ["tracing.register"]),
    ("Evaluators", ["evaluator"]),
    (
        "Types",
        [
            "EvalCase",
            "EvalRun",
            "ScoreItem",
            "RunResult",
            "Fixture",
            "Judge",
            "ExperimentResult",
            "RunSummary",
        ],
    ),
]

HIDDEN_ATTRIBUTES = {"model_config"}

Item = tuple[str, str | griffe.Expr | None, str]


def load_sdk() -> griffe.Object | griffe.Alias:
    return griffe.load(
        "evalkit.sdk",
        search_paths=[str(ROOT / "src")],
        docstring_parser="google",
        resolve_aliases=True,
        resolve_external=False,
    )


def format_parameter(parameter: griffe.Parameter) -> str:
    text = parameter.name
    if parameter.kind is griffe.ParameterKind.var_positional:
        text = f"*{text}"
    elif parameter.kind is griffe.ParameterKind.var_keyword:
        text = f"**{text}"
    if parameter.annotation is not None:
        text = f"{text}: {parameter.annotation}"
    if parameter.default is not None:
        separator = " = " if parameter.annotation is not None else "="
        text = f"{text}{separator}{parameter.default}"
    return text


def render_parameters(parameters: list[griffe.Parameter]) -> list[str]:
    rendered = []
    keyword_marker_needed = True
    for parameter in parameters:
        if parameter.kind is griffe.ParameterKind.var_positional:
            keyword_marker_needed = False
        if parameter.kind is griffe.ParameterKind.keyword_only and keyword_marker_needed:
            rendered.append("*")
            keyword_marker_needed = False
        rendered.append(format_parameter(parameter))
        if parameter.kind is griffe.ParameterKind.positional_only and (
            parameter is parameters[-1]
            or parameters[parameters.index(parameter) + 1].kind
            is not griffe.ParameterKind.positional_only
        ):
            rendered.append("/")
    return rendered


def render_function_signature(name: str, function: griffe.Function) -> str:
    prefix = "async def" if "async" in function.labels else "def"
    parameters = render_parameters(list(function.parameters))
    returns = f" -> {function.returns}" if function.returns is not None else ""
    if not parameters:
        return f"{prefix} {name}(){returns}:"
    body = "".join(f"    {parameter},\n" for parameter in parameters)
    return f"{prefix} {name}(\n{body}){returns}:"


def list_public_attributes(cls: griffe.Class) -> list[griffe.Attribute]:
    return [
        member
        for member in cls.members.values()
        if isinstance(member, griffe.Attribute)
        and not member.name.startswith("_")
        and member.name not in HIDDEN_ATTRIBUTES
    ]


def split_field(value: object) -> tuple[str | None, str]:
    if not (isinstance(value, griffe.ExprCall) and str(value.function) == "Field"):
        return (None if value is None else str(value)), ""
    default = None
    description = ""
    for argument in value.arguments:
        if isinstance(argument, griffe.ExprKeyword):
            if argument.name == "default":
                default = str(argument.value)
            elif argument.name == "description":
                description = ast.literal_eval(str(argument.value))
        elif default is None:
            default = str(argument)
    return default, description


def render_class_signature(name: str, cls: griffe.Class) -> str:
    lines = [f"class {name}:"]
    for attribute in list_public_attributes(cls):
        line = f"    {attribute.name}"
        if attribute.annotation is not None:
            line = f"{line}: {attribute.annotation}"
        default, _ = split_field(attribute.value)
        if default is not None:
            line = f"{line} = {default}"
        lines.append(line)
    if len(lines) == 1:
        lines.append("    ...")
    return "\n".join(lines)


def flatten(text: str) -> str:
    return " ".join(text.split())


def render_named_list(title: str, items: list[Item]) -> list[str]:
    lines = [f"**{title}**", ""]
    for name, annotation, description in items:
        kind = f" (`{annotation}`)" if annotation not in (None, "") else ""
        lines.append(f"- `{name}`{kind}: {flatten(description)}")
    return [*lines, ""]


def render_unnamed_list(title: str, items: list) -> list[str]:
    lines = [f"**{title}**", ""]
    for item in items:
        lines.append(f"- `{item.annotation}`: {flatten(item.description)}")
    return [*lines, ""]


def list_field_descriptions(obj: griffe.Object) -> list[Item]:
    if not isinstance(obj, griffe.Class):
        return []
    items: list[Item] = []
    for attribute in list_public_attributes(obj):
        _, description = split_field(attribute.value)
        if description:
            items.append((attribute.name, attribute.annotation, description))
    return items


def render_docstring(obj: griffe.Object) -> list[str]:
    if obj.docstring is None:
        return []
    lines: list[str] = []
    sections = obj.docstring.parse("google")
    for section in sections:
        kind = section.kind
        if kind is griffe.DocstringSectionKind.text:
            lines.extend([section.value.strip(), ""])
        elif kind is griffe.DocstringSectionKind.parameters:
            lines.extend(render_named_list("Args", describe_items(section.value)))
        elif kind is griffe.DocstringSectionKind.attributes:
            lines.extend(render_named_list("Attributes", describe_items(section.value)))
        elif kind is griffe.DocstringSectionKind.returns:
            lines.extend(render_unnamed_list("Returns", section.value))
        elif kind is griffe.DocstringSectionKind.raises:
            lines.extend(render_unnamed_list("Raises", section.value))
        else:
            raise ValueError(f"{obj.path}: unsupported docstring section {kind.value}")
    has_attributes = any(
        section.kind is griffe.DocstringSectionKind.attributes for section in sections
    )
    fields = list_field_descriptions(obj)
    if fields and not has_attributes:
        lines.extend(render_named_list("Attributes", fields))
    return lines


def describe_items(items: list) -> list[Item]:
    return [(item.name, item.annotation, item.description) for item in items]


def render_object(path: str, obj: griffe.Object) -> list[str]:
    name = path.rsplit(".", 1)[-1]
    if isinstance(obj, griffe.Function):
        signature = render_function_signature(name, obj)
    elif isinstance(obj, griffe.Class):
        signature = render_class_signature(name, obj)
    else:
        raise ValueError(f"{path} is neither a function nor a class")
    return [f"### {path}", "", "```python", signature, "```", "", *render_docstring(obj)]


def render_reference() -> str:
    sdk = load_sdk()
    lines = ["# SDK reference", "", INTRO, ""]
    for title, paths in SECTIONS:
        lines.extend([f"## {title}", ""])
        for path in paths:
            obj = sdk[path]
            target = obj.final_target if isinstance(obj, griffe.Alias) else obj
            lines.extend(render_object(path, target))
    return "\n".join(lines).rstrip() + "\n"


def generate(output: Path = OUTPUT) -> None:
    output.write_text(render_reference())


if __name__ == "__main__":
    generate(Path(sys.argv[1]) if len(sys.argv) > 1 else OUTPUT)
