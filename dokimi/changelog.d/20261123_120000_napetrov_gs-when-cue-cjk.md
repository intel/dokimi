### Fixed

- **`description-no-when` reads two more Chinese "when" clauses** — `适用于` (or `適用於`) followed by an activity verb (`编写`, `记录`, `准备`, `评审`, ...) now counts as a when clause, as does `当 … 前后自动触发` (`当 … 前后 … 触发|调用|执行|运行|启动`, "when … before or after …, auto-triggers"). `适用于编写 PRD、记录产品需求，或评审现有 PRD。` no longer gets the `description-no-when` warning. A bare scope such as `适用于 Python 项目的工具` still does, and `当前` ("current") does not start a `当` clause. Authors of a flagged description add the request or situation the skill is for.
