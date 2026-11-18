<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`skill novelty ground --prefix mkl_` no longer vetoes library names in a link context** — `Link with
  mkl_intel_lp64, mkl_sequential and mkl_core.`, `Use the mkl_rt single dynamic library.` and
  `target_link_libraries(app MKL::mkl_core)` were refused as "not in source". A name after `-l` or a CMake `Pkg::`,
  or in a sentence about linking or a library, is not vetoed (`linked list`, `hyperlink` and `std::x` are no link
  context), and a library file's stem (`mkl_rt` for
  `libmkl_rt.so`) is a known name. A called name (`mkl_x(`) is still vetoed, and so is an undeclared name
  outside a link context (`Call mkl_x to set threads`). A key whose name only a link context exempts is
  `unverifiable`, never `verified`.
