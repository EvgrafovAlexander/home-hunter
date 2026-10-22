import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _imports(path):
    tree = ast.parse(path.read_text())
    result = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.append(("." * node.level) + (node.module or ""))
    return result


def test_services_do_not_depend_on_http_views():
    service_dir = ROOT / "listings" / "services"
    forbidden = {"listings.views", ".views", "..views"}
    violations = {
        str(path.relative_to(ROOT)): sorted(set(_imports(path)) & forbidden)
        for path in service_dir.glob("*.py")
        if set(_imports(path)) & forbidden
    }
    assert not violations, f"service -> view dependency: {violations}"


def test_collectors_do_not_depend_on_http_views():
    collector_dir = ROOT / "collectors"
    forbidden = {"listings.views", ".views", "..views"}
    violations = {
        str(path.relative_to(ROOT)): sorted(set(_imports(path)) & forbidden)
        for path in collector_dir.rglob("*.py")
        if "tests" not in path.parts and set(_imports(path)) & forbidden
    }
    assert not violations, f"collector -> view dependency: {violations}"
