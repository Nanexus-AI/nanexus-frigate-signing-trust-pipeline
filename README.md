# Nanexus trust contracts

Offline Python contracts and portable recorded Historical evidence for a Frigate-first trust companion. This foundation does not provide media verification, a running service, Live transport, Frigate integration or installation packages.

Python 3.11+; standard library only. Run the targeted tests from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The domain contracts keep Historical verification, metadata coverage and requested-range coverage separate. A verification context can be wider than the requested range and contain gaps. Neither a storage file nor a context envelope is a coverage assertion.

Evidence bundles contain a versioned `receipt.json` and bundle-relative artifacts. Newly issued supported bundles can be relocated and recognized without media processing, external tools, database or network access. Recognition establishes supported structure and hash consistency of a **recorded result**, not a new cryptographic verdict or issuer authenticity.

See the [evidence contract](docs/evidence-format.md) for schema, limits, provenance, API usage and failure semantics. All verdicts, including legitimate `PARTIAL` and `UNKNOWN`, are preserved. Frigate version support, production deployment, licensing for distribution and release qualification remain future decisions; no released integration is claimed.
