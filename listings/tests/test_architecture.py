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


def test_service_modules_have_russian_boundary_documentation():
    """Проверяет module docstring и публичные функции сервисного слоя."""
    service_dir = ROOT / "listings" / "services"
    undocumented = {}
    for path in sorted(service_dir.glob("*.py")):
        tree = ast.parse(path.read_text())
        module_doc = ast.get_docstring(tree) or ""
        missing = []
        if not any("а" <= char.lower() <= "я" for char in module_doc):
            missing.append("module docstring")
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                if not ast.get_docstring(node) or not any("а" <= char.lower() <= "я" for char in ast.get_docstring(node)):
                    missing.append(node.name)
        if missing:
            undocumented[str(path.relative_to(ROOT))] = missing
    assert not undocumented, f"service documentation gaps: {undocumented}"


def test_services_catalog_lists_every_service_module():
    """Проверяет, что каталог сервисов не отстал от структуры кода."""
    catalog = (ROOT / "docs" / "SERVICES.md").read_text()
    service_dir = ROOT / "listings" / "services"
    missing = [path.name for path in service_dir.glob("*.py") if f"`{path.name}`" not in catalog]
    assert not missing, f"services missing from docs/SERVICES.md: {missing}"


def test_review_models_have_a_dedicated_module_and_compatibility_exports():
    """Проверяет границу review-моделей и re-export из legacy-модуля."""
    review_source = (ROOT / "listings" / "models_reviews.py").read_text()
    models_source = (ROOT / "listings" / "models.py").read_text()
    for name in ("ReviewTag", "ListingReview", "ListingReviewRevision", "Consideration", "UserListingHide", "GlobalListingHide"):
        assert f"class {name}(models.Model)" in review_source
        assert f"class {name}(models.Model)" not in models_source
        assert name in models_source


def test_polling_models_have_a_dedicated_module_and_compatibility_exports():
    """Проверяет границу detail/polling-моделей и re-export из models."""
    polling_source = (ROOT / "listings" / "models_polling.py").read_text()
    models_source = (ROOT / "listings" / "models.py").read_text()
    for name in ("CianFullScanCheckpoint", "CianDetailPollState", "CianDetailPayload",
                 "CianDetailPollProgress", "AvitoDetailPollState", "AvitoDetailPayload",
                 "AvitoDetailPollProgress"):
        assert f"class {name}(models.Model)" in polling_source
        assert f"class {name}(models.Model)" not in models_source
        assert name in models_source
