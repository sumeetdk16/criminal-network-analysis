"""
Export the case graph to Cypher / JSON / a live Neo4j instance.

    python3 scripts/export_graph.py                 # -> exports/graph.cypher
    python3 scripts/export_graph.py --json
    NEO4J_URI=bolt://localhost:7687 NEO4J_PASSWORD=... \
      python3 scripts/export_graph.py --neo4j
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.graph.build import build_graph                      # noqa: E402
from app.graph.store import (CypherExportStore, JsonExportStore,  # noqa: E402
                             Neo4jStore, export_graph, describe_backends)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--neo4j", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    out_dir = os.path.join(ROOT, "exports")
    os.makedirs(out_dir, exist_ok=True)
    g = build_graph()

    if args.neo4j:
        if not Neo4jStore.available():
            print("neo4j driver not installed:  pip install neo4j")
            print(describe_backends())
            return 1
        store = Neo4jStore()
    elif args.json:
        store = JsonExportStore(args.out or os.path.join(out_dir, "graph.json"))
    else:
        store = CypherExportStore(args.out or os.path.join(out_dir, "graph.cypher"))

    print(export_graph(g, store))
    return 0


if __name__ == "__main__":
    sys.exit(main())
