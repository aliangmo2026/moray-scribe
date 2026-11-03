"""Multi-language feasibility probe: tree-sitter -> moray-scribe SymbolGraph.

Usage::

    .venv/Scripts/python.exe benchmarks/test_tree_sitter.py

Parses a TypeScript and a Go sample with ``tree-sitter`` (via the
``tree-sitter-language-pack``), walks the AST for definitions, maps them onto
the existing :mod:`moray_scribe.schema` contracts (SymbolNode / SymbolEdge /
EdgeKind) and prints the verdict. This is a feasibility experiment, not part
of the pytest suite (pytest only collects ``tests/``).

Findings feed docs/multi-lang-future.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from moray_scribe.schema import (  # noqa: E402
    EdgeKind,
    SymbolEdge,
    SymbolGraph,
    SymbolKind,
    SymbolNode,
)

TS_SAMPLE = r'''
export class UserService {
  private repo: Repository;
  constructor(repo: Repository) { this.repo = repo; }
  async login(name: string): Promise<User> {
    const user = await this.repo.find(name);
    return this.audit(user);
  }
  audit(user: User): User { return user; }
}

export function createService(repo: Repository): UserService {
  return new UserService(repo);
}
'''

GO_SAMPLE = r'''
package service

type Store struct{ Name string }

func (s *Store) Load(id string) string {
	return s.Name + id
}

func NewStore(name string) *Store {
	return &Store{Name: name}
}

func Run(name string) string {
	s := NewStore(name)
	return s.Load("1")
}
'''

# tree-sitter node types per language that carry a definition, mapped to the
# SymbolKind our schema understands.
NODE_KIND_MAP: dict[str, dict[str, SymbolKind]] = {
    "typescript": {
        "class_declaration": SymbolKind.CLASS,
        "function_declaration": SymbolKind.FUNCTION,
        "method_definition": SymbolKind.METHOD,
    },
    "go": {
        # Go nests `type_spec` (the real named definition) inside
        # `type_declaration`; the spec is what carries the struct/interface.
        "type_spec": SymbolKind.CLASS,
        "function_declaration": SymbolKind.FUNCTION,
        "method_declaration": SymbolKind.METHOD,
    },
}


def _name_of(node) -> str | None:
    named = ("identifier", "type_identifier", "field_identifier", "property_identifier")
    for child in node.children:
        if child.type in named:
            return child.text.decode("utf-8")
    return None


def extract_symbols(source: bytes, lang: str) -> list[tuple[str, SymbolKind, int, int]]:
    """Walk the tree-sitter AST and return (name, kind, lineno, end_lineno)."""
    from tree_sitter_language_pack import get_parser

    parser = get_parser(lang)
    tree = parser.parse(source)
    kind_map = NODE_KIND_MAP[lang]
    found: list[tuple[str, SymbolKind, int, int]] = []

    def visit(node, class_stack: list[str]) -> None:
        if node.type in kind_map:
            name = _name_of(node)
            if name:
                kind = kind_map[node.type]
                found.append((name, kind, node.start_point[0] + 1, node.end_point[0] + 1))
                if kind is SymbolKind.CLASS:
                    class_stack.append(name)
        for child in node.children:
            visit(child, class_stack)
        is_class_def = node.type in kind_map and kind_map[node.type] is SymbolKind.CLASS
        if is_class_def and class_stack:
            class_stack.pop()

    visit(tree.root_node, [])
    return found


def build_symbol_graph(symbols: list[tuple[str, SymbolKind, int, int]], module: str) -> SymbolGraph:
    """Map extracted symbols onto the existing schema contracts."""
    graph = SymbolGraph()
    graph.add_node(
        SymbolNode(
            id=module,
            name=module,
            kind=SymbolKind.MODULE,
            module=module,
            file=f"{module}.{'ts' if 'user' in module else 'go'}",
            lineno=1,
            end_lineno=1,
        )
    )
    method_parents: dict[str, str] = {}
    for name, kind, lineno, end_lineno in symbols:
        node_id = f"{module}.{name}"
        if node_id in graph.nodes:
            continue
        graph.add_node(
            SymbolNode(
                id=node_id,
                name=name,
                qualname=name,
                kind=kind,
                module=module,
                file=f"{module}.dummy",
                lineno=lineno,
                end_lineno=end_lineno,
            )
        )
        graph.add_edge(
            SymbolEdge(
                source=module,
                target=node_id,
                kind=EdgeKind.CONTAINS,
            )
        )
        if kind is SymbolKind.METHOD:
            method_parents[node_id] = name
    return graph


def main() -> int:
    print("=" * 72)
    print("moray-scribe 多语言可行性验证（tree-sitter -> SymbolGraph schema）")
    print("=" * 72)
    ok = True
    for lang, sample, module in (
        ("typescript", TS_SAMPLE, "user_service"),
        ("go", GO_SAMPLE, "service"),
    ):
        print(f"\n--- {lang} ---")
        try:
            symbols = extract_symbols(sample.encode("utf-8"), lang)
        except Exception as exc:
            print(f"  解析失败: {exc!r}")
            ok = False
            continue
        for name, kind, lineno, end_lineno in symbols:
            print(f"  {kind.value:<9} {name:<16} lines {lineno}-{end_lineno}")

        graph = build_symbol_graph(symbols, module)
        print(f"  -> SymbolGraph: {graph.node_count} nodes, {graph.edge_count} edges")
        problems = graph.validate_edges()
        if problems:
            print(f"  !! 悬挂边: {problems}")
            ok = False
        else:
            print("  -> validate_edges(): 0 dangling edges — schema 对齐通过")
    print()
    print("结论:", "两种语言均可映射到现有 SymbolGraph 契约 ✓" if ok else "存在失败项 ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
