# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""library subcommand group: list, show, intents.

`library` is the historical group name; `product` is accepted as an alias.
Product identity comes from products.yaml; the domain/intent space (which
products serve which user problems) comes from intents.yaml.
"""

from __future__ import annotations

import argparse
import sys


def _load_custom(cls, flag: str, value):
    """``cls()`` for the bundled file, else ``cls`` over the file named by ``flag``; exit 1 if it cannot be read."""
    import yaml
    from pathlib import Path as _Path

    if not value:
        return cls()
    path = _Path(value)
    if not path.is_file():
        print(f"Error: {flag} {value}: not a file", file=sys.stderr)
        sys.exit(1)
    try:
        return cls(path=path)
    except (OSError, ValueError, yaml.YAMLError) as exc:  # unreadable, not UTF-8, not YAML
        print(f"Error: cannot read {flag} {value}: {exc}", file=sys.stderr)
        sys.exit(1)


def _load_registry(args):
    from dokimi.registry import ProductRegistry
    from pathlib import Path as _Path
    reg_path = _Path(args.registry) if getattr(args, "registry", None) else None
    return ProductRegistry(path=reg_path)


def _load_registry_checked(args):
    """`_load_registry` for the `library` commands: a bad --registry file exits 1 (benchmark keeps its own)."""
    from dokimi.registry import ProductRegistry
    return _load_custom(ProductRegistry, "--registry", getattr(args, "registry", None))


def _load_intents(args=None):
    from dokimi.intents import IntentRegistry
    return _load_custom(IntentRegistry, "--intents", getattr(args, "intents", None))


def cmd_library_list(args: argparse.Namespace) -> None:
    """List all products in the registry, optionally filtered by intent domain."""
    registry = _load_registry_checked(args)
    entries = registry.list()
    domain = getattr(args, "domain", None)
    if domain:
        intents = _load_intents(args)
        try:
            keys = set(intents.products_for(domain))
        except KeyError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        entries = [e for e in entries if e.key in keys]
    if not entries:
        print("Registry is empty.")
        return
    print(f"{'Key':<12} {'Name':<16} {'Repo':<35} {'Doc sources'}")
    print("─" * 85)
    for e in entries:
        repo = e.repo or "—"
        sources = ", ".join(e.doc_sources)
        print(f"{e.key:<12} {e.name:<16} {repo:<35} {sources}")
    suffix = f" in domain '{domain}'" if domain else " registered"
    print(f"\n{len(entries)} products{suffix}.")


def cmd_library_show(args: argparse.Namespace) -> None:
    """Show full details for a single product."""
    registry = _load_registry_checked(args)
    try:
        entry = registry.get(args.name)
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    domains = _load_intents(args).domains_for(entry.key)
    print(f"Key          : {entry.key}")
    print(f"Name         : {entry.name}")
    print(f"Repo         : {entry.repo or '—'}")
    print(f"Context7 ID  : {entry.context7_id or '—'}")
    print(f"Doc sources  : {', '.join(entry.doc_sources)}")
    print(f"Domains      : {', '.join(domains) if domains else '—'}")
    print(f"Description  :\n  {entry.description}")


def cmd_library_intents(args: argparse.Namespace) -> None:
    """List intent domains and the products that serve them."""
    intents = _load_intents(args)
    domains = intents.list()
    if not domains:
        print("Intent registry is empty.")
        return
    for d in domains:
        print(f"{d.key} — {d.name}")
        print(f"  products: {', '.join(d.products) if d.products else '—'}")
        for example in d.example_intents:
            print(f"  e.g. \"{example}\"")
        print()
    print(f"{len(domains)} intent domains registered.")


def register(sub, positive_int) -> None:
    """Add the `library` subcommand group (alias: `product`)."""
    library_p = sub.add_parser(
        "library", aliases=["product"], help="Product registry management"
    )
    library_sub = library_p.add_subparsers(dest="library_cmd", required=True)

    intents_help = "Path to custom intents.yaml"

    lib_list_p = library_sub.add_parser("list", help="List products in the registry")
    lib_list_p.add_argument("--registry", default=None, help="Path to custom products.yaml")
    lib_list_p.add_argument("--intents", default=None, help=intents_help)
    lib_list_p.add_argument("--domain", default=None, help="Filter by intent domain (see `library intents`)")
    lib_list_p.set_defaults(func=cmd_library_list)

    lib_show_p = library_sub.add_parser("show", help="Show details for a product")
    lib_show_p.add_argument("name", help="Product key (e.g., onetbb)")
    lib_show_p.add_argument("--registry", default=None, help="Path to custom products.yaml")
    lib_show_p.add_argument("--intents", default=None, help=intents_help)
    lib_show_p.set_defaults(func=cmd_library_show)

    lib_intents_p = library_sub.add_parser(
        "intents", help="List intent domains (problem space) and their products"
    )
    lib_intents_p.add_argument("--intents", default=None, help=intents_help)
    lib_intents_p.set_defaults(func=cmd_library_intents)
