import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# Opt-in CI diagnosis: publish code locations/types only, never log text or locals.
import json
import os

import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    target = os.environ.get("JIAHUI_CI_FAILURE_REPORT")
    if not target or not report.failed or call.excinfo is None:
        return
    root = Path(__file__).resolve().parents[2]
    locations = []
    for entry in call.excinfo.traceback:
        try:
            relative = Path(str(entry.path)).resolve().relative_to(root)
        except ValueError:
            continue
        locations.append({"file": relative.as_posix(), "line": entry.lineno + 1})
    record = {
        "test": item.name,
        "exception_type": call.excinfo.type.__name__,
        "winerror": getattr(call.excinfo.value, "winerror", None),
        "locations": locations,
    }
    with open(target, "a", encoding="utf-8") as output:
        output.write(json.dumps(record) + "\n")
