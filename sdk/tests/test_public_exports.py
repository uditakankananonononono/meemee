import meemee_client
from meemee_client import errors, models


def test_all_public_models_and_errors_are_exported_from_package_root():
    names = []
    for module in (models, errors):
        for name, value in vars(module).items():
            if name.startswith("_") or getattr(value, "__module__", None) != module.__name__:
                continue
            if isinstance(value, type): names.append(name)
    missing = sorted(name for name in names if not hasattr(meemee_client, name))
    assert missing == []
    assert set(names) <= set(meemee_client.__all__)
