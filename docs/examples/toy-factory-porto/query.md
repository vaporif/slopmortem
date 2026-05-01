## Pitch

> We're building a factory that will sell toys in Porto.

## Command

```sh
just query "we're building a factory that will sell toys in porto"
```

## Run

- Synthesized: 0 candidates (4 filtered pre-synth)
- Cost: $0.0836
- Latency: 52s
- Query Trace: [Laminar](https://laminar.sh/shared/traces/2efd284d-2a9f-eea7-f51b-d171a542e63a)

A deliberately thin pitch — no sector specifics, no traction, no team. The retriever pulls candidates but the rerank/floor filter drops all of them, so synthesis runs on an empty set and the report short-circuits.

Full report: [`report.md`](report.md).
