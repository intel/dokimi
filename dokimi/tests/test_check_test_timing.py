# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""scripts/check_test_timing.py: a new sleep or timer in tests/ fails, the baseline only shrinks."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_test_timing as ctt  # noqa: E402

SLEEP = "import time\n\n\ndef test_a():\n    time.sleep(1)\n"


def run(tmp_path: Path, files: dict[str, str], baseline: dict[str, int] | None, *flags: str) -> int:
    root = tmp_path / "tests"
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    path = tmp_path / "baseline.json"
    if baseline is not None:
        path.write_text(json.dumps({"files": baseline}), encoding="utf-8")
    return ctt.main(["--tests-root", str(root), "--baseline", str(path), *flags])


def entries(tmp_path: Path) -> dict[str, int]:
    return json.loads((tmp_path / "baseline.json").read_text(encoding="utf-8"))["files"]


def test_a_sleep_in_a_file_with_no_entry_fails_naming_the_file(tmp_path, capsys):
    assert run(tmp_path, {"test_new.py": SLEEP}, {}) == 1
    out = capsys.readouterr().out
    assert "tests/test_new.py: 1 sleep/Timer call(s), above no baseline entry" in out


def test_a_count_at_its_entry_passes(tmp_path, capsys):
    assert run(tmp_path, {"test_old.py": SLEEP}, {"tests/test_old.py": 1}) == 0
    assert "1 sleep/Timer call(s) in 1 file(s): 1 time.sleep" in capsys.readouterr().out


def test_a_count_over_its_entry_fails(tmp_path, capsys):
    assert run(tmp_path, {"test_old.py": SLEEP + "    time.sleep(2)\n"}, {"tests/test_old.py": 1}) == 1
    assert "tests/test_old.py: 2 sleep/Timer call(s), above its baseline 1" in capsys.readouterr().out


def test_a_count_under_its_entry_fails_until_update_lowers_it(tmp_path, capsys):
    files = {"test_old.py": SLEEP, "test_gone.py": "def test_b():\n    pass\n"}
    baseline = {"tests/test_old.py": 2, "tests/test_gone.py": 1}
    assert run(tmp_path, files, baseline) == 1
    assert "tests/test_old.py: 1 sleep/Timer call(s), below its baseline 2" in capsys.readouterr().out
    assert run(tmp_path, files, baseline, "--update") == 0
    assert entries(tmp_path) == {"tests/test_old.py": 1}
    assert run(tmp_path, files, entries(tmp_path)) == 0


def test_update_refuses_while_a_file_is_over_its_entry_and_leaves_the_baseline_alone(tmp_path):
    files = {"test_old.py": SLEEP + "    time.sleep(2)\n", "test_new.py": SLEEP}
    baseline = {"tests/test_old.py": 1}
    assert run(tmp_path, files, baseline, "--update") == 1
    assert entries(tmp_path) == baseline


def test_update_writes_a_first_baseline_when_there_is_none(tmp_path):
    assert run(tmp_path, {"test_old.py": SLEEP, "sub/test_deep.py": SLEEP}, None, "--update") == 0
    assert entries(tmp_path) == {"tests/sub/test_deep.py": 1, "tests/test_old.py": 1}


@pytest.mark.parametrize("flags", [(), ("--update",)])
def test_a_tests_root_that_is_not_a_directory_exits_2_and_leaves_the_baseline_alone(tmp_path, capsys, flags):
    baseline = {"tests/test_old.py": 1}
    (tmp_path / "baseline.json").write_text(json.dumps({"files": baseline}), encoding="utf-8")
    (tmp_path / "a_file.py").write_text(SLEEP, encoding="utf-8")
    for root in (tmp_path / "missing", tmp_path / "a_file.py"):
        argv = ["--tests-root", str(root), "--baseline", str(tmp_path / "baseline.json"), *flags]
        assert ctt.main(argv) == 2
        assert "is not a directory" in capsys.readouterr().out
        assert entries(tmp_path) == baseline


@pytest.mark.parametrize("content", ['{"files": null}', '{"wrong_key": {}}', "{}", '[1]', '{"files": {"tests/a.py": null}}', "{not json",
                                     '{"files": {"tests/a.py": 1.9}}', '{"files": {"tests/a.py": "2"}}'])
def test_a_baseline_that_is_not_one_fails_naming_it(tmp_path, capsys, content):
    (tmp_path / "tests").mkdir()
    (tmp_path / "baseline.json").write_text(content, encoding="utf-8")
    assert ctt.main(["--tests-root", str(tmp_path / "tests"), "--baseline", str(tmp_path / "baseline.json")]) == 1
    assert "baseline.json is not a readable baseline" in capsys.readouterr().out


def test_a_test_file_that_cannot_be_read_fails_naming_it_instead_of_raising(tmp_path, capsys):
    root = tmp_path / "tests"
    root.mkdir()
    (root / "test_binary.py").write_bytes(b"\xff\xfe = 1\n")
    (root / "test_dangling.py").symlink_to(tmp_path / "nowhere.py")
    (root / "test_broken.py").write_text("def (:\n", encoding="utf-8")
    (tmp_path / "baseline.json").write_text('{"files": {}}', encoding="utf-8")
    assert ctt.main(["--tests-root", str(root), "--baseline", str(tmp_path / "baseline.json")]) == 1
    out = capsys.readouterr().out
    for name in ("test_binary.py", "test_dangling.py", "test_broken.py"):
        assert f"tests/{name}: cannot be scanned" in out


def test_a_missing_baseline_fails_without_update(tmp_path, capsys):
    assert run(tmp_path, {"test_old.py": SLEEP}, None) == 1
    assert "no baseline.json" in capsys.readouterr().out


@pytest.mark.parametrize("source", [
    "import time\ntime.sleep(1)\n",
    "import time as t\nt.sleep(1)\n",
    "from time import sleep\nsleep(1)\n",
    "from time import sleep as nap\nnap(1)\n",
    "import threading\nthreading.Timer(1, print)\n",
    "import threading as th\nth.Timer(1, print)\n",
    "from threading import Timer\nTimer(1, print)\n",
    "from threading import Timer as T\nT(1, print)\n",
    "from time import *\nsleep(1)\n",
    "from threading import *\nTimer(1, print)\n",
])
def test_each_way_to_import_a_sleep_or_timer_is_counted(source):
    counts, bare = ctt.scan(source, "x.py")
    assert sum(counts.values()) == 1
    assert bare == []


@pytest.mark.parametrize("source", [
    "import subprocess\nsubprocess.run(['sh', '-c', 'sleep 5'])\n",
    "CHILD = 'import time; time.sleep(1)'\n",
    "import asyncio\nasyncio.sleep(1)\n",
    "def sleep(s):\n    pass\n\n\nsleep(1)\n",
    "import time\nprint(time.monotonic())\n",
])
def test_a_sleep_in_a_string_or_another_name_is_not_a_call_to_count(source):
    assert ctt.scan(source, "x.py") == ({}, [])


@pytest.mark.parametrize(("source", "expected"), [
    ("def test_a():\n    import time as t\n    t.sleep(1)\n\n\ndef test_b():\n    import threading as t\n",
     {"time.sleep": 1}),
    ("def test_a():\n    import threading as t\n\n\ndef test_b():\n    import time as t\n    t.sleep(1)\n",
     {"time.sleep": 1}),
    ("def test_a():\n    from time import sleep as nap\n    nap(1)\n\n\ndef test_b():\n    nap(1)\n",
     {"time.sleep": 1}),
    ("import time as t\n\n\ndef test_a(t):\n    t.sleep(1)\n\n\ndef test_b():\n    t.sleep(1)\n",
     {"time.sleep": 1}),
    ("import time\n\n\ndef test_a():\n    time = FakeClock()\n    time.sleep(1)\n", {}),
    ("from time import sleep\n\n\ndef test_a():\n    def sleep(s):\n        pass\n    sleep(1)\n", {}),
    ("import time\n\n\ndef test_a():\n    def inner():\n        time.sleep(1)\n    inner()\n", {"time.sleep": 1}),
    ("import time\n\n\nclass T:\n    time = None\n\n    def test_a(self):\n        time.sleep(1)\n",
     {"time.sleep": 1}),
    ("import time\ntime.sleep(1)\ntime = None\n", {"time.sleep": 1}),
    ("def test_a():\n    import time\n    time.sleep(1)\n    time = None\n", {"time.sleep": 1}),
    ("import time\ntime = None\ntime.sleep(1)\n", {}),
    ("import time\ntime = time.sleep(1)\n", {"time.sleep": 1}),
    ("import time\n[time for time in ()]\ntime.sleep(1)\n", {"time.sleep": 1}),
    ("import time\n[time.sleep(1) for _ in range(2)]\ntime = None\n", {"time.sleep": 1}),
    ("import time\n[0 for time in time.sleep(1)]\n", {"time.sleep": 1}),
    ("import time\n[0 for time in ()  if time.sleep(1)]\n", {}),
    ("import time\n[(time := None) for _ in ()]\ntime.sleep(1)\n", {}),
    ("import time\n[(lambda: (time := None)) for _ in ()]\ntime.sleep(1)\n", {"time.sleep": 1}),
    ("import time\n[(time := None) for _ in (time.sleep(1),)]\n", {"time.sleep": 1}),
    ("import time\n\n\ndef test_a():\n    global time\n    time.sleep(1)\n    time = None\n", {"time.sleep": 1}),
    ("import time\n\n\ndef test_a():\n    time = 1\n    def inner():\n        nonlocal time\n"
     "        time.sleep(1)\n        time = None\n", {}),
    ("import time\n\n\ndef test_a():\n    def inner():\n        global time\n        time.sleep(1)\n"
     "        time = None\n    inner()\n", {"time.sleep": 1}),
    ("def test_a():\n    global time\n    import time\n    time.sleep(1)\n", {"time.sleep": 1}),
    ("import time\ntime: object\ntime.sleep(1)\n", {"time.sleep": 1}),
    ("import time\n\n\nclass T:\n    time: object\n    time.sleep(1)\n", {"time.sleep": 1}),
    ("import time\n\n\ndef test_a():\n    time: object\n    time.sleep(1)\n", {}),
    ("def test_a():\n    import time\n    for time in (time.sleep(1),):\n        pass\n", {"time.sleep": 1}),
    ("import time\nfor time in ():\n    pass\ntime.sleep(1)\n", {}),
    ("import time\nclass T:\n    time.sleep(1)\ntime = None\n", {"time.sleep": 1}),
    ("import time\nclass T:\n    time = None\n    time.sleep(1)\n", {}),
    ("import time\n[(lambda x=(time := None): x) for _ in (0,)]\ntime.sleep(1)\n", {}),
    ("import time\n[(lambda x=time.sleep(1): (time := None)) for _ in (0,)]\ntime.sleep(1)\n",
     {"time.sleep": 2}),
    ("import time\nfrom types import SimpleNamespace\n"
     "[(time := SimpleNamespace(sleep=lambda _: None), time.sleep(1)) for _ in (0,)]\n", {}),
    ("import time\n[0 for _ in (0,) if [(time := None) for x in (time.sleep(1),)]]\n", {"time.sleep": 1}),
    ("import time\n[0 for _ in (0,) if [(time := None) for x in (0,)]]\ntime.sleep(1)\n", {}),
    ("import time\n[[(time := None) for x in (0,)] for _ in (time.sleep(1),)]\n", {"time.sleep": 1}),
    ("import time\nf = lambda x=(time := None): x\ntime.sleep(1)\n", {}),
    ("import time\n\n\ndef f(x=(time := None)):\n    pass\n\n\ntime.sleep(1)\n", {}),
    ("import time\n\n\n@(lambda g: g)(time := None)\ndef f():\n    pass\n\n\ntime.sleep(1)\n", {}),
    ("import time\n\n\nclass C((time := object)):\n    pass\n\n\ntime.sleep(1)\n", {}),
    ("import time\n\n\nclass C((t := object)):\n    pass\n\n\ntime.sleep(1)\n", {"time.sleep": 1}),
    ("import time\nf = lambda: (time := None)\ntime.sleep(1)\n", {"time.sleep": 1}),
    ("import time\ng = ((time := None) for _ in (0,))\ntime.sleep(1)\n", {"time.sleep": 1}),
    ("import time\ng = (0 for _ in ((time := None),))\ntime.sleep(1)\n", {}),
    ("import time\n[(time := None) for _ in (0,) if time.sleep(1) is None]\n", {"time.sleep": 1}),
    ("import time\n[(time := None) for _ in (0,) for y in (time.sleep(1),)]\n", {"time.sleep": 1}),
    ("import time\n{(time := None): 0 for _ in (0,) if time.sleep(1) is None}\n", {"time.sleep": 1}),
    ("import time\n[0 for _ in (0,) if (time := None)]\ntime.sleep(1)\n", {}),
    ("import time\n[0 for _ in ((time := None), time.sleep(1))]\n", {}),
    ("import time\n\n\ndef test_a(x: time.sleep(1)):\n    pass\n", {"time.sleep": 1}),
    ("import time\n\n\ndef test_a() -> time.sleep(1):\n    pass\n", {"time.sleep": 1}),
    ("import time\n\n\nasync def test_a(*a: time.sleep(1), **k: time.sleep(1)):\n    pass\n", {"time.sleep": 2}),
    ("from __future__ import annotations\nimport time\n\n\ndef test_a(x: time.sleep(1)) -> time.sleep(1):\n"
     "    pass\n", {}),
    ("from time import sleep\n\n\ndef test_a(x: (sleep := lambda _: None)):\n    pass\n\n\nsleep(1)\n", {}),
    ("from time import sleep\n\n\ndef test_a() -> (sleep := lambda _: None):\n    pass\n\n\nsleep(1)\n", {}),
    ("from __future__ import annotations\nfrom time import sleep\n\n\n"
     "def test_a(x: (sleep := lambda _: None)):\n    pass\n\n\nsleep(1)\n", {"time.sleep": 1}),
    ("from __future__ import annotations\nimport time\nx: time.sleep(1)\n", {}),
    ("import time\n\n\ndef test_a():\n    x: time.sleep(1)\n", {}),
    ("import time\n\n\ndef test_a():\n    x: int = time.sleep(1)\n", {"time.sleep": 1}),
    ("import time\nx: time.sleep(1)\n", {"time.sleep": 1}),
    ("import time\n\n\nclass T:\n    x: time.sleep(1)\n", {"time.sleep": 1}),
    ("def test_a():\n    import time\n    x: (time := None)\n    time.sleep(1)\n", {"time.sleep": 1}),
    ("def test_a():\n    import time\n    x: (time := None) = 0\n    time.sleep(1)\n", {"time.sleep": 1}),
    ("from __future__ import annotations\nimport time\nx: (time := None) = 0\ntime.sleep(1)\n",
     {"time.sleep": 1}),
    ("import time\nx: (time := None) = 0\ntime.sleep(1)\n", {}),
    ("from time import sleep\n[sleep(1) for _ in (0,) if (sleep := lambda _: True)]\n", {}),
    ("from time import sleep\n{sleep(1) for _ in (0,) if (sleep := lambda _: True)}\n", {}),
    ("from time import sleep\n{sleep(1): 0 for _ in (0,) if (sleep := lambda _: True)}\n", {}),
    ("from time import sleep\n[sleep(1) for _ in (sleep := lambda _: [0],)]\n", {}),
    ("from time import sleep\n[sleep(1) for _ in (0,) for y in (0,) if (sleep := lambda _: True)]\n", {}),
    ("from time import sleep\n[0 for _ in (0,) if [sleep(1) for y in (0,) if (sleep := None)]]\n", {}),
    ("from time import sleep\n[sleep(1) for _ in (0,) if sleep(2) if (sleep := None)]\n",
     {"time.sleep": 1}),
    ("from time import sleep\n[sleep(1) for _ in (0,) if (sleep := sleep(2))]\n", {"time.sleep": 1}),
    ("import time\nmatch 1:\n    case time:\n        time.sleep(1)\n", {}),
    ("import time\nmatch [1, 2]:\n    case [_, *time]:\n        time.sleep(1)\n", {}),
    ("import time\nmatch {1: 2}:\n    case {1: _, **time}:\n        time.sleep(1)\n", {}),
    ("import time\nmatch 1:\n    case 2:\n        pass\n    case _:\n        time.sleep(1)\n", {"time.sleep": 1}),
    ("import time\nmatch 1:\n    case time:\n        pass\ntime.sleep(1)\n", {"time.sleep": 1}),
    ("import time\nif 1:\n    time = 1\n    time.sleep(1)\n", {}),
])
def test_a_name_means_what_the_innermost_scope_that_binds_it_says(source, expected):
    assert ctt.scan(source, "x.py") == (expected, [])


@pytest.mark.parametrize(("source", "expected"), [
    ("import sys\nif sys.platform == 'win32':\n    from time import sleep as wait\nelse:\n"
     "    from my_helpers import wait\nwait(1)\n", {"time.sleep": 1}),
    ("import sys\nif sys.platform != 'win32':\n    from my_helpers import wait\nelse:\n"
     "    from time import sleep as wait\nwait(1)\n", {"time.sleep": 1}),
    ("try:\n    from time import sleep as wait\nexcept ImportError:\n    from my_helpers import wait\nwait(1)\n",
     {"time.sleep": 1}),
    ("try:\n    from my_helpers import wait\nexcept ImportError:\n    from time import sleep as wait\nwait(1)\n",
     {"time.sleep": 1}),
    ("import sys\nmatch sys.platform:\n    case 'win32':\n        import time as t\n    case _:\n"
     "        import json as t\nt.sleep(1)\n", {"time.sleep": 1}),
    ("from my_helpers import wait\nimport sys\nif sys.platform == 'win32':\n    from time import sleep as wait\n"
     "wait(1)\n", {"time.sleep": 1}),
    ("import sys\nfrom time import sleep as wait\nif sys.platform == 'win32':\n    from my_helpers import wait\n"
     "wait(1)\n", {"time.sleep": 1}),
    ("import sys\nfrom time import sleep as wait\nif sys.platform == 'win32':\n    from my_helpers import wait\n"
     "else:\n    wait = print\nwait(1)\n", {"time.sleep": 1}),
    ("import sys\nfrom time import sleep as wait\nwait = print\nif sys.platform == 'win32':\n"
     "    from my_helpers import wait\nwait(1)\n", {}),
    ("import sys\nif sys.platform == 'win32':\n    from time import sleep as wait\nelse:\n"
     "    from my_helpers import wait\n    wait(1)\n", {}),
    ("import sys\nif sys.platform == 'win32':\n    from time import sleep as wait\n    wait(1)\nelse:\n"
     "    from my_helpers import wait\n", {"time.sleep": 1}),
    ("import sys\nif sys.platform == 'win32':\n    from time import sleep as wait\nelse:\n"
     "    from my_helpers import wait\nwait = print\nwait(1)\n", {}),
    ("from time import sleep as wait\nfor _ in range(2):\n    wait = print\nwait(1)\n", {"time.sleep": 1}),
    ("import time\ntry:\n    raise ValueError\nexcept (time.sleep(0) or ValueError) as time:\n    pass\n",
     {"time.sleep": 1}),
    ("import time\ntry:\n    pass\nexcept ValueError as time:\n    time.sleep(1)\n", {}),
    ("import time\nFalse and (time := None)\ntime.sleep(1)\n", {"time.sleep": 1}),
    ("import time\n(time := None) if False else None\ntime.sleep(1)\n", {"time.sleep": 1}),
    ("import time\nNone if False else (time := None)\ntime.sleep(1)\n", {"time.sleep": 1}),
    ("import time\n(time := None) and 1\ntime.sleep(1)\n", {}),
    ("import time\n1 if (time := None) else 2\ntime.sleep(1)\n", {}),
    ("from time import sleep\nTrue and (sleep := lambda _: None) and sleep(1)\n", {}),
    ("from time import sleep\nFalse or (sleep := lambda _: None) or sleep(1)\n", {}),
    ("from time import sleep\nTrue and (sleep := lambda _: None) and sleep(1)\nsleep(2)\n", {"time.sleep": 1}),
])
def test_a_name_bound_differently_in_each_branch_counts_when_any_branch_makes_it_a_sleep(source, expected):
    assert ctt.scan(source, "x.py") == (expected, [])


@pytest.mark.parametrize(("source", "expected"), [
    ('__import__("time").sleep(1)\n', {"time.sleep": 1}),
    ('import importlib\nimportlib.import_module("time").sleep(1)\n', {"time.sleep": 1}),
    ('import importlib as il\nil.import_module("time").sleep(1)\n', {"time.sleep": 1}),
    ('from importlib import import_module\nimport_module("time").sleep(1)\n', {"time.sleep": 1}),
    ('from importlib import import_module as load\nload("time").sleep(1)\n', {"time.sleep": 1}),
    ('__import__("threading").Timer(1, print)\n', {"threading.Timer": 1}),
    ('import importlib\nimportlib.import_module("threading").Timer(1, print)\n', {"threading.Timer": 1}),
    ('__import__("time").sleep(1)  # timing-ok: the child must outlive the parent\n', {}),
    ('__import__(name).sleep(1)\n', {}),
    ('__import__("ti" + "me").sleep(1)\n', {}),
    ('__import__("time", fromlist=["sleep"]).sleep(1)\n', {}),
    ('__import__("os").getcwd()\n', {}),
    ('__import__("time").monotonic()\n', {}),
    ('__import__("time").Timer(1, print)\n', {}),
    ('def test_a(__import__):\n    __import__("time").sleep(1)\n', {}),
    ('def test_a():\n    def __import__(name):\n        pass\n    __import__("time").sleep(1)\n', {}),
    ('__import__ = print\n__import__("time").sleep(1)\n', {}),
    ('import importlib\n\n\ndef test_a(importlib):\n    importlib.import_module("time").sleep(1)\n', {}),
    ('from importlib import import_module\n\n\ndef test_a():\n    import_module = print\n'
     '    import_module("time").sleep(1)\n', {}),
    ('import importlib\nimportlib.import_module(name).sleep(1)\n', {}),
    ('import_module("time").sleep(1)\n', {}),
    ('import mylib\nmylib.import_module("time").sleep(1)\n', {}),
    ('from importlib import import_module\nimport_module("time")\n', {}),
    ('from importlib import *\nimport_module("time").sleep(1)\n', {"time.sleep": 1}),
    ('__import__("time", globals()).sleep(1)\n', {}),
    ('import importlib\nimportlib.import_module(name="time").sleep(1)\n', {}),
    ('__import__(name="time").sleep(1)\n', {}),
    ('mod = __import__("time")\nmod.sleep(1)\n', {}),
    ('import importlib\nt = importlib.import_module("time")\nt.sleep(1)\n', {}),
])
def test_a_module_loaded_by_a_literal_name_counts_like_an_imported_one(source, expected):
    assert ctt.scan(source, "x.py") == (expected, [])


@pytest.mark.parametrize(("source", "expected"), [
    ("def test_a():\n    assert 1 + 1 == 2\n", ({}, [])),
    ("import time\nprint(time.monotonic())\n", ({}, [])),
    ("import time\ntime.sleep(1)\n", ({"time.sleep": 1}, [])),
    ("x = 1  # timing-ok\n", ({}, [1])),
    ("import time\ntime.ｓleep(1)\n", ({"time.sleep": 1}, [])),
    ("from threading import Ｔimer\nＴimer(1, print)\n", ({"threading.Timer": 1}, [])),
])
def test_a_file_that_cannot_hold_a_call_is_not_walked_and_scans_as_if_it_were(source, expected, monkeypatch):
    assert ctt.count(ast.parse(source), source) == expected
    assert ctt.scan(source, "x.py") == expected
    if source.isascii() and not any(word in source for word in ("sleep", "Timer", "timing-ok")):
        monkeypatch.setattr(ctt, "_calls", lambda tree: pytest.fail("walked a file with no marker"))
        assert ctt.scan(source, "x.py") == expected


def test_a_file_with_no_marker_is_still_parsed_for_its_syntax():
    with pytest.raises(SyntaxError):
        ctt.scan("def (:\n", "x.py")


def test_a_waiver_with_a_reason_is_not_counted():
    source = "import time\ntime.sleep(60)  # timing-ok: child must outlive the parent\n"
    assert ctt.scan(source, "x.py") == ({}, [])


def test_a_waiver_on_the_last_line_of_a_multiline_call_is_not_counted():
    source = "import time\ntime.sleep(\n    60\n)  # timing-ok: child must outlive the parent\n"
    assert ctt.scan(source, "x.py") == ({}, [])


@pytest.mark.parametrize("comment", ["# timing-ok", "# timing-ok:   ", "# timing-ok: --"])
def test_a_waiver_without_a_reason_fails_naming_its_line(tmp_path, capsys, comment):
    text = f"import time\n\n\ndef test_a():\n    time.sleep(1)  {comment}\n"
    assert run(tmp_path, {"test_w.py": text}, {"tests/test_w.py": 1}) == 1
    out = capsys.readouterr().out
    assert "tests/test_w.py:5: `# timing-ok` needs a reason" in out


def test_waiting_py_is_not_counted(tmp_path):
    assert run(tmp_path, {"waiting.py": SLEEP, "test_a.py": "def test_a():\n    pass\n"}, {}) == 0


def test_the_repo_tree_holds_its_own_baseline(capsys):
    assert ctt.main([]) == 0, capsys.readouterr().out
