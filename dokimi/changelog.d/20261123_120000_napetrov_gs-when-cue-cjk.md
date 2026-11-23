### Fixed

- **`description-no-when` reads two more Chinese "when" clauses** — `适用于` (or `適用於`) followed by a list of activities (`适用于编写 PRD、记录产品需求，…`, a verb then `、`) now counts as a when clause, as does `当 … 前后 … 自动触发` (`当 … 前后 … 触发|调用|执行|运行|启动`, "when … before or after …, auto-triggers"). A bare scope, `适用于配置管理的工具` or `适用于 Python 项目的工具`, still gets the warning, and so do `相当于` ("equivalent to") and `当前` ("current"). Authors of a flagged description add the request or situation the skill is for.
