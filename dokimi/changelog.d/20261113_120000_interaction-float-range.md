### Fixed

- **Interaction contrasts of finite scores beyond the float range refuse** — `interaction_test` and `interaction_significance` (the `interactions` of an arms summary) raise `OverflowError`, as `paired_test` does, when the four finite scores of a question give a contrast `ab - a - b + base` or an arm delta (`a - base`, `b - base`, `ab - base`) beyond the float maximum, such as `ab` 1.7e308 and `a` -1.7e308, where they returned an `inf` delta mean, a `nan` spread and no `p`. Results that stay finite are unchanged.
