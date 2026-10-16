# Nanexus trust contracts

Python contracts, bounded Historical verification, Live Signing Status and a companion integration foundation for Frigate v0.18.0. A read-only SQLite adapter stages retained media for the existing finite Toolkit backend. Explicit playback/rendition mapping, nonblocking observation sources, durable jobs and a local programmatic service boundary prepare later Frigate-native desktop integration. A private GET-only Live transport supports a separate version-bound Frigate authentication relay. No browser UI or installation package is provided.

Python 3.11+; standard library only. Run the targeted tests from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The domain contracts keep Historical verification, metadata coverage and requested-range coverage separate. A verification context can be wider than the requested range and contain gaps. Neither a storage file nor a context envelope is a coverage assertion.

Evidence bundles contain a versioned `receipt.json` and bundle-relative artifacts. Newly issued supported bundles can be relocated and recognized without media processing, external tools, database or network access. Recognition establishes supported structure and hash consistency of a **recorded result**, not a new cryptographic verdict or issuer authenticity.

See the [integration foundation](docs/integration.md), [Live Signing Status](docs/live.md), the [Historical API](docs/historical.md), and the [evidence contract](docs/evidence-format.md). Live observation is separate from Historical cryptographic verification and coverage. All Historical verdicts, including legitimate `PARTIAL` and `UNKNOWN`, are preserved. Only the documented Frigate v0.18.0 profile is targeted. Real deployment acceptance, licensing for distribution and release qualification remain future work; no released integration is claimed.
