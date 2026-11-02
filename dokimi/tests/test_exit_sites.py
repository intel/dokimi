# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Which lines of a module end the process, as the architecture gate counts them (``exit_sites``).

Kept apart from test_import_architecture.py, which the growth ratchet caps at 1000 lines.
"""

from tests.exit_site_scan import exit_sites
from tests.test_import_architecture import _package, exit_problems


def test_a_process_exit_outside_the_frontend_fails(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/cli.py": "import sys\nsys.exit(2)\n",
        "dokimi/commands/x.py": "import sys\nsys.exit(1)\n",
        "dokimi/core.py": "import sys\n\ndef f():\n    sys.exit(3)\n",
        "dokimi/b.py": "def g():\n    raise SystemExit('bad')\n",
        "dokimi/c.py": "import os\nos._exit(0)\nexit(1)\n",
        "dokimi/d.py": "import sys\n\nif __name__ == '__main__':\n    sys.exit(0)\n",
        "dokimi/e.py": "import sys\n\nif __name__ == '__main__':\n    pass\nelse:\n    sys.exit(1)\n",
    })
    problems = exit_problems(imports, {"dokimi.c": (1, "one exit once")})
    assert [p.split()[0] for p in problems] == ["dokimi.b", "dokimi.c", "dokimi.core", "dokimi.e"]
    assert "lower EXIT_BASELINE" not in "".join(problems)


def test_an_aliased_process_exit_outside_the_frontend_is_counted(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "import sys as s\ns.exit(1)\n",
        "dokimi/b.py": "from sys import exit as bye\nbye(1)\n",
        "dokimi/c.py": "import os as o\no._exit(0)\n",
    })
    assert exit_sites(imports.trees["dokimi.a"]) == [2]
    assert exit_sites(imports.trees["dokimi.b"]) == [2]
    assert exit_sites(imports.trees["dokimi.c"]) == [2]


def test_an_exit_through_the_helper_or_a_bound_raise_outside_the_frontend_fails(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/exit_codes.py": "def usage_exit(m):\n    error = SystemExit(m)\n    raise error\n",
        "dokimi/commands/x.py": "from dokimi.exit_codes import usage_exit\nusage_exit('x')\n",
        "dokimi/a.py": "from dokimi.exit_codes import usage_exit as ue\nue('x')\n",
        "dokimi/b.py": "import dokimi.exit_codes\ndokimi.exit_codes.usage_exit('x')\n",
        "dokimi/c.py": "from dokimi import exit_codes as ec\nec.usage_exit('x')\n",
        "dokimi/d.py": "def f():\n    e = SystemExit(2)\n    raise e\n",
        "dokimi/e.py": "import builtins\nbuiltins.exit(1)\n",
        "dokimi/f.py": "import builtins\nraise builtins.SystemExit(2)\n",
        "dokimi/g.py": "from builtins import SystemExit as Stop\nraise Stop(2)\n",
        "dokimi/h.py": "from builtins import SystemExit as Stop\nerror = Stop(2)\nraise error\n",
        "dokimi/i.py": "import builtins\nerror = builtins.SystemExit(2)\nraise error\n",
    })
    problems = exit_problems(imports, {})
    assert [p.split()[0] for p in problems] == ["dokimi.a", "dokimi.b", "dokimi.c", "dokimi.d", "dokimi.e",
                                                "dokimi.f", "dokimi.g", "dokimi.h", "dokimi.i"]
    assert [p.split(" at lines ")[1].split(";")[0] for p in problems] == ["[2]", "[2]", "[2]", "[3]", "[2]",
                                                                         "[2]", "[2]", "[3]", "[3]"]


def test_relative_helper_imports_and_a_reused_module_alias_are_counted(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "from .exit_codes import usage_exit\nusage_exit('x')\n",
        "dokimi/b.py": "from . import exit_codes as ec\nec.usage_exit('x')\n",
        "dokimi/c.py": "def f():\n    import sys as s\n    s.exit(2)\n\ndef g():\n    import os as s\n",
    })
    assert exit_sites(imports.trees["dokimi.a"]) == [2]
    assert exit_sites(imports.trees["dokimi.b"]) == [2]
    assert exit_sites(imports.trees["dokimi.c"]) == [3]


def test_annotated_and_scoped_bindings_of_a_raised_exit_are_counted_per_scope(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "error: SystemExit = SystemExit(2)\nraise error\n",
        "dokimi/b.py": ("def f():\n    error = SystemExit(2)\n    return error\n\n"
                        "def g():\n    error = ValueError('bad')\n    raise error\n"),
        "dokimi/c.py": "error = SystemExit(2)\nerror = ValueError('bad')\nraise error\n",
        "dokimi/d.py": "def f():\n    error = SystemExit(3)\n    raise error\n",
    })
    assert exit_sites(imports.trees["dokimi.a"]) == [2]
    assert exit_sites(imports.trees["dokimi.b"]) == []
    assert exit_sites(imports.trees["dokimi.c"]) == []
    assert exit_sites(imports.trees["dokimi.d"]) == [3]


def test_a_name_in_one_scope_does_not_change_what_another_scope_means(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "def f():\n    import sys as s\n    s.exit(2)\n\ndef g(s):\n    s.exit()\n",
        "dokimi/b.py": ("def f():\n    from builtins import SystemExit as Stop\n    raise Stop(2)\n\n"
                        "def g():\n    raise Stop(3)\n"),
        "dokimi/c.py": "error = SystemExit(2)\n\ndef f(error):\n    raise error\n\ndef g():\n    raise error\n",
        "dokimi/d.py": ("error = SystemExit(2)\n\nclass C:\n    error = ValueError('bad')\n\n"
                        "    def f(self):\n        raise error\n"),
    })
    assert exit_sites(imports.trees["dokimi.a"]) == [3]
    assert exit_sites(imports.trees["dokimi.b"]) == [3]
    assert exit_sites(imports.trees["dokimi.c"]) == [7]
    assert exit_sites(imports.trees["dokimi.d"]) == [7]


def test_an_annotation_alone_makes_a_name_local_to_its_function(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "error = SystemExit(2)\n\ndef f():\n    error: int\n    raise error\n",
    })
    assert exit_sites(imports.trees["dokimi.a"]) == []


def test_comprehension_targets_and_headers_are_evaluated_where_python_evaluates_them(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "import sys\n[x for sys in []]\nsys.exit(2)\n",
        "dokimi/b.py": "import sys\n[x for sys in sys.exit(2)]\n",
        "dokimi/c.py": "import sys\ndef f(sys=sys.exit(2)):\n    pass\n",
        "dokimi/d.py": "import sys\ng = lambda sys=sys.exit(2): 0\n",
        "dokimi/e.py": "import sys\n@sys.exit(2)\ndef f():\n    pass\n",
        "dokimi/f.py": "import sys\nclass C(sys.exit(2)):\n    pass\n",
        "dokimi/g.py": "import sys\ndef f():\n    [sys for sys in []]\n    sys.exit(2)\n",
    })
    assert exit_sites(imports.trees["dokimi.a"]) == [3]
    assert exit_sites(imports.trees["dokimi.b"]) == [2]
    assert exit_sites(imports.trees["dokimi.c"]) == [2]
    assert exit_sites(imports.trees["dokimi.d"]) == [2]
    assert exit_sites(imports.trees["dokimi.e"]) == [2]
    assert exit_sites(imports.trees["dokimi.f"]) == [2]
    assert exit_sites(imports.trees["dokimi.g"]) == [4]


def test_global_and_nonlocal_declarations_bind_where_they_point(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "import sys\ndef f():\n    global sys\n    sys.exit(2)\n    sys = None\n",
        "dokimi/b.py": ("def g():\n    e = SystemExit\n    def f():\n        nonlocal e\n"
                        "        raise e(2)\n        e = None\n    return f\n"),
    })
    assert exit_sites(imports.trees["dokimi.a"]) == [4]
    assert exit_sites(imports.trees["dokimi.b"]) == [5]


def test_a_class_body_reads_enclosing_names_until_it_assigns_them(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "import sys\nclass C:\n    sys.exit(2)\n    sys = None\n",
        "dokimi/b.py": "import sys\nsys: object\nsys.exit(2)\n",
        "dokimi/c.py": "import sys\nclass C:\n    sys: object\n    sys.exit(2)\n",
    })
    assert exit_sites(imports.trees["dokimi.a"]) == [3]
    assert exit_sites(imports.trees["dokimi.b"]) == [3]
    assert exit_sites(imports.trees["dokimi.c"]) == [4]


def test_an_assigned_exit_callable_or_module_keeps_its_meaning(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "import sys\nstop = sys.exit\nstop(2)\n",
        "dokimi/b.py": "import sys\ns = sys\ns.exit(2)\n",
        "dokimi/c.py": "import sys\nstop: object = sys.exit\nstop(2)\n",
        "dokimi/d.py": "def f():\n    import sys\n    stop = sys.exit\n    stop(2)\n",
        "dokimi/e.py": "import builtins\nstop = builtins.SystemExit\nraise stop(2)\n",
        "dokimi/f.py": "import sys\nstop = None\nstop = sys.exit\n\ndef f():\n    stop(1)\n",
    })
    assert exit_sites(imports.trees["dokimi.a"]) == [3]
    assert exit_sites(imports.trees["dokimi.b"]) == [3]
    assert exit_sites(imports.trees["dokimi.c"]) == [3]
    assert exit_sites(imports.trees["dokimi.d"]) == [4]
    assert exit_sites(imports.trees["dokimi.e"]) == [3]
    assert exit_sites(imports.trees["dokimi.f"]) == [6]


def test_a_dotted_import_resolves_through_the_binding_of_its_root(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "import dokimi.exit_codes\ndef f(dokimi):\n    dokimi.exit_codes.usage_exit('x')\n",
        "dokimi/b.py": "import dokimi.exit_codes\ndokimi = None\ndokimi.exit_codes.usage_exit('x')\n",
        "dokimi/c.py": "import dokimi.exit_codes\ndokimi.exit_codes.usage_exit('x')\n",
        "dokimi/d.py": "import os.path\nos._exit(1)\n",
    })
    assert exit_sites(imports.trees["dokimi.a"]) == []
    assert exit_sites(imports.trees["dokimi.b"]) == []
    assert exit_sites(imports.trees["dokimi.c"]) == [2]
    assert exit_sites(imports.trees["dokimi.d"]) == [2]


def test_a_deleted_binding_or_a_cleared_except_target_leaves_the_builtin(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "SystemExit = ValueError\ndel SystemExit\nraise SystemExit(2)\n",
        "dokimi/b.py": "try:\n    1 / 0\nexcept Exception as SystemExit:\n    pass\nraise SystemExit(2)\n",
        "dokimi/c.py": "SystemExit = ValueError\nraise SystemExit(2)\n",
        "dokimi/d.py": "try:\n    1 / 0\nexcept Exception as SystemExit:\n    raise SystemExit(2)\n",
    })
    assert exit_sites(imports.trees["dokimi.a"]) == [3]
    assert exit_sites(imports.trees["dokimi.b"]) == [5]
    assert exit_sites(imports.trees["dokimi.c"]) == []
    assert exit_sites(imports.trees["dokimi.d"]) == []


def test_a_nonlocal_assignment_reaches_the_aliases_read_in_a_sibling(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": ("import sys\ndef outer():\n    x = None\n    def set_exit():\n        nonlocal x\n"
                        "        x = sys.exit\n    def run():\n        stop = x\n        stop(2)\n"
                        "    set_exit()\n    run()\nouter()\n"),
        "dokimi/b.py": ("def outer():\n    x = None\n    def set_exit():\n        nonlocal x\n"
                        "        x = SystemExit\n    def run():\n        stop = x\n        raise stop(2)\n"
                        "    set_exit()\n    run()\nouter()\n"),
    })
    assert exit_sites(imports.trees["dokimi.a"]) == [9]
    assert exit_sites(imports.trees["dokimi.b"]) == [8]


def test_eager_bodies_read_enclosing_names_by_position(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "E = SystemExit\nE = ValueError\nclass C:\n    raise E(2)\n",
        "dokimi/b.py": "E = ValueError\nE = SystemExit\nclass C:\n    raise E(2)\n",
        "dokimi/c.py": "import sys\ns = sys.exit\ns = print\nxs = [s(2) for _ in 'a']\n",
        "dokimi/d.py": "import sys\ns = print\ns = sys.exit\nxs = [s(2) for _ in 'a']\n",
        "dokimi/e.py": "E = SystemExit\nE = ValueError\ndef f():\n    class C:\n        raise E(2)\n",
        "dokimi/f.py": "xs = [SystemExit(2) for _ in 'a']\nSystemExit = ValueError\n",
    })
    assert exit_sites(imports.trees["dokimi.a"]) == []
    assert exit_sites(imports.trees["dokimi.b"]) == [4]
    assert exit_sites(imports.trees["dokimi.c"]) == []
    assert exit_sites(imports.trees["dokimi.d"]) == [4]
    assert exit_sites(imports.trees["dokimi.e"]) == [5]
    assert exit_sites(imports.trees["dokimi.f"]) == [1]
