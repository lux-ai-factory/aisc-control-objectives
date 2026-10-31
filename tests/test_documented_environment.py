"""Every environment variable the service reads is documented: in the README's configuration table or in
.env.example. The names are read from the source with the ast module, so a new read cannot slip in
undocumented. baf_llm.py is exempt: it stays byte-identical to qualification's copy, and its keys
(`<PROVIDER>_API_KEY`) are documented as a pattern."""
from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SRC = APP / "src" / "aisc_control_objectives"
EXEMPT = {"baf_llm.py"}

#: Read through a helper that takes the variable's name (upstream._base), which the scan cannot follow.
READ_BY_NAME = {"upstream.py": {"PLATFORM_URL", "QUALIFICATION_URL"}}

#: What an environment is called where it is read: os.environ itself, or a mapping handed the environment.
ENVIRONMENTS = {"environ", "env"}


def _is_environment(node: ast.expr) -> bool:
    """os.environ, an `env` mapping, or `(env or os.environ)`."""
    if isinstance(node, ast.BoolOp):
        return any(_is_environment(value) for value in node.values)
    return (isinstance(node, ast.Attribute) and node.attr == "environ") or (
        isinstance(node, ast.Name) and node.id in ENVIRONMENTS)


def _literal(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def names_read(source: str) -> set[str]:
    """Every literal name read through os.environ[...], os.environ.get(...), os.getenv(...) or an `env`
    mapping."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        name = None
        if isinstance(node, ast.Subscript) and _is_environment(node.value):
            name = _literal(node.slice)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
            receiver, method = node.func.value, node.func.attr
            if (method in ("get", "setdefault", "pop") and _is_environment(receiver)) or (
                    method == "getenv" and isinstance(receiver, ast.Name) and receiver.id == "os"):
                name = _literal(node.args[0])
        if name is not None:
            found.add(name)
    return found


def documented() -> set[str]:
    readme = (APP / "README.md").read_text()
    section = readme.split("## Configuration", 1)[1].split("\n## ", 1)[0]
    table = set(re.findall(r"^\| `([A-Z][A-Z0-9_]*)`", section, re.M))
    example = set(re.findall(r"^[#\s]*([A-Z][A-Z0-9_]*)=", (APP / ".env.example").read_text(), re.M))
    return table | example


def read_by_the_service() -> dict[str, set[str]]:
    read = {}
    for path in sorted(SRC.rglob("*.py")):
        if path.name in EXEMPT:
            continue
        names = names_read(path.read_text()) | READ_BY_NAME.get(path.name, set())
        if names:
            read[str(path.relative_to(SRC))] = names
    return read


def test_the_scan_finds_each_way_of_reading_a_variable():
    source = ('import os\nos.environ["A"]\nos.environ.get("B", "")\nos.getenv("C")\n'
              'def f(env):\n    env.get("D")\n    env["E"]\n    (env or os.environ).get("F")\n'
              'os.environ.setdefault(key, value)\n')
    assert names_read(source) == {"A", "B", "C", "D", "E", "F"}


def test_every_variable_the_service_reads_is_documented():
    known = documented()
    missing = {f"{where}: {name}" for where, names in read_by_the_service().items() for name in names
               if name not in known}
    assert missing == set()
