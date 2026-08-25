#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gmi -- Generic Migration Inventory.

Build a fully-covering migration candidate pack ("卷面") for ANY Android project:

    python gmi.py --project <android project root> --workspace <out dir> \
                  [--features A,B,C] [--page-features map.csv] [--allow-unmapped] [--verbose]

Guarantees:
  * full-repo scan (manifest / nav / layout / drawable / values / gradle / tests ...)
  * every in-scope file maps to >=1 candidate, or the run FAILS as UNMAPPED
  * app-agnostic: features and page->feature mapping are data-driven, not hardcoded
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gmi_generate


def main() -> int:
    ap = argparse.ArgumentParser(description="Generic Migration Inventory (gmi)")
    ap.add_argument("--project", required=True, help="Android project root to scan")
    ap.add_argument("--workspace", required=True, help="output directory for candidates/ + coverage/")
    ap.add_argument("--features", default="", help="comma-separated feature ids (default: auto-derive)")
    ap.add_argument("--page-features", default="", help="CSV override: page_symbol,feature_id")
    ap.add_argument("--allow-unmapped", action="store_true",
                    help="don't fail when files have no candidate (report as GAP)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    features = [f.strip() for f in args.features.split(",") if f.strip()] if args.features else None
    gmi_generate.generate(
        project=args.project, workspace=args.workspace, features=features,
        page_feature_csv=args.page_features or None,
        allow_unmapped=args.allow_unmapped, verbose=args.verbose,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
