# Evidence contract

Formal evidence is stored at:

```text
evidence/<ENV-ID>/<Page-ID>/<State-ID>/<Evidence-ID>/
```

Each sealed package contains:

```text
screenshot.png
layout.json
layout-diff.json    # required only for a recorded transition
steps.md
metadata.json
manifest.sha256
COMMITTED
```

MP4 files and MP4 references are prohibited.

`metadata.json` binds one inventory, feature, page, state, environment, and evidence ID. It records issuer, collector, UTC timestamps, Android CLI version, device serial, app/source/APK identity, predecessor evidence, actual command argument arrays, artifact sizes, MIME types, and SHA-256 hashes.
Validation compares the index, metadata, manifest, actual bytes, frozen environment, scope digest, device check, and source check in both directions.
The migration controller additionally records the sealed package manifest and metadata digests in `controller/evidence-anchor-registry.csv`. Phase 2 copies the verified rows into `evidence-anchors.snapshot.csv` at closure. Both the Phase 2 validator and controller gate require an exact match, so changing the package and its local index together is still detected.

Lifecycle:

```text
staged -> captured -> indexed -> sealed -> accepted | rejected | superseded
```

- A capture becomes referenceable only after all files validate, the index entry is written, and `COMMITTED` exists.
- Recapture always creates a new evidence ID. Preserve the old package and link it through `supersedes_evidence_id` or a rework record.
- A state transition is proven by predecessor evidence, written steps, `layout-diff.json`, and the new complete state package.
- An unreferenced sealed package is an orphan and fails closure unless it is explicitly rejected or superseded.
- A successful final review writes an acceptance registry, changes active index rows to `ACCEPTED`, changes active inventory rows to `REVIEWED`, and freezes the complete package with `closure-manifest.sha256` plus `CLOSED`.

The same closure covers `asset-inventory.csv`, every file below `asset-package/files/`, its exact `manifest.sha256`, and its manifest-digest `COMMITTED` marker. Screenshots prove observed states; archived assets preserve the actual reusable source bytes. Neither can substitute for the other.
