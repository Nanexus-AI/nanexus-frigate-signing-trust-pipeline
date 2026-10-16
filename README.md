# Nanexus trust contracts

Offline Python contracts, bounded Historical verification and Live Signing Status for a Frigate-first trust companion. A configurable Toolkit adapter verifies finite H.264 candidates from a caller-owned frozen metadata snapshot. Live observes supported compressed bytes through a configuration-driven source interface. No real transport, current Frigate discovery, running daemon or installation package is provided.

Python 3.11+; standard library only. Run the targeted tests from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The domain contracts keep Historical verification, metadata coverage and requested-range coverage separate. A verification context can be wider than the requested range and contain gaps. Neither a storage file nor a context envelope is a coverage assertion.

Evidence bundles contain a versioned `receipt.json` and bundle-relative artifacts. Newly issued supported bundles can be relocated and recognized without media processing, external tools, database or network access. Recognition establishes supported structure and hash consistency of a **recorded result**, not a new cryptographic verdict or issuer authenticity.

See [Live Signing Status](docs/live.md), the [Historical API](docs/historical.md), and the [evidence contract](docs/evidence-format.md). Live observation is separate from Historical cryptographic verification and coverage. All Historical verdicts, including legitimate `PARTIAL` and `UNKNOWN`, are preserved. Frigate version support, production deployment, licensing for distribution and release qualification remain future decisions; no released integration is claimed.
