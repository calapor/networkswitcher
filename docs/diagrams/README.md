# Diagrams

Architecture/topology diagrams are authored in **PlantUML**. Each diagram has a
`.puml` **source of truth** and a same-named `.png` rendered from it; the
markdown docs embed the `.png`.

| Source | Rendered | Used in |
|--------|----------|---------|
| `bridge-topology.puml` | `bridge-topology.png` | [`README.md`](../../README.md) |
| `architecture-topology.puml` | `architecture-topology.png` | [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) |

## Convention

- **Edit the `.puml` first**, then re-render the `.png`. Never hand-edit a PNG,
  and don't re-introduce ASCII flow blocks (`├──`, `└──`, `│`) in the markdown —
  those were intentionally replaced.
- **Commit the `.puml` and `.png` together.**

## Rendering

No local install needed — render via [Kroki](https://kroki.io):

```bash
curl -sS -X POST -H "Content-Type: text/plain" \
  --data-binary @bridge-topology.puml \
  https://kroki.io/plantuml/png -o bridge-topology.png
```

Or with a local PlantUML CLI (needs Graphviz): `plantuml -tpng bridge-topology.puml`.
</content>
