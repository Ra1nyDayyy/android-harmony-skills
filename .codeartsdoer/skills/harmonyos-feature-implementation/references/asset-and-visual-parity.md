# Asset and visual parity

## Source-first policy

An existing Android-owned SVG, PNG, WebP, JPG, icon, illustration, logo, font, or other visual file is the source of truth.

- `DIRECT_COPY`: copy the archived bytes; source and target SHA-256 must match.
- `FORMAT_CONVERSION`: allowed only through a frozen conversion contract and a sealed conversion package. Record source/target hashes, contract-bound argv, conversion-record ID/hash, and emulator evidence. Final status is `CONVERSION_VERIFIED`; do not redesign.
- `RECREATE_FROM_PUBLIC_UI`: allowed only when Phase 3 planned `RECREATE_LATER`. Final status is `RECREATED_VERIFIED` and requires emulator evidence plus an `ASSET_RECREATION` decision approved both locally and by the controller.

Do not use a text glyph, system symbol, hand-authored path, AI-generated image, stock substitute, or screenshot crop when a source asset exists.

Run a format conversion only through the frozen contract and frozen visual-asset agent:

```bash
python3 scripts/convert_asset.py \
  --workspace <phase-04-workspace> \
  --conversion-id <new-conversion-id> \
  --asset-id <Asset-ID> \
  --contract-id <frozen-contract-id> \
  --executed-by <visual-asset-agent-id>
```

Never reuse a Conversion-ID or overwrite an old target.

## Visual elements

Every parity row has a page-state root element. Record every business control and asset-bearing element that affects parity, including container shape, color, size, position, inset, typography, source asset, Harmony source file/symbol, and decision ID when applicable.

Without an approved decision, geometry must remain within the frozen tolerance and structured visual properties must match. Platform chrome may be excluded only through the environment comparison policy; business content cannot be masked.

The example failure this policy prevents is replacing an Android rounded-square confirmation button and its dark source icon with a larger circular HarmonyOS button and a white check glyph. That is `REWORK`, not native adaptation.

## Gate rules

Every in-scope archived asset is registered. Every project visual file must be a registered target; the policy cannot whitelist an untracked visual path. A legitimate exception must enter the asset registry and, when observable behavior changes, the nativeization decision chain. Every asset used by a parity row is referenced by its real Harmony source. `DIRECT_COPY` byte mismatch, an unsealed conversion, an unregistered visual file, forbidden glyph substitution, or an unapproved recreation prevents `PASS`.
