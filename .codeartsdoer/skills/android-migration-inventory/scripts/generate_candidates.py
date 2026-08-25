#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""generate_candidates -- skill 入口，兼容旧调用名，内部委托给新版 gmi 引擎（8 表）。

产出（与 gmi 完全同构）：
  candidates/code-map.candidates.full.csv
  candidates/business-rules.candidates.csv
  candidates/asset-mapping.candidates.csv
  candidates/inventory.candidates.csv
  candidates/page-fields.candidates.csv
  candidates/third-party-dependencies.candidates.csv
  candidates/field-options.candidates.csv
  candidates/navigation-relations.candidates.csv
  candidates/candidates.json + manifest.sha256
  coverage/coverage-ledger.csv

用法：
  python generate_candidates.py --project <android root> --workspace <out> \
        [--features A,B,C] [--page-features map.csv] [--allow-unmapped] [--verbose]

参数留空/沿用旧语义会被自动推导：features 缺省时从页面自动派生、page-features
为可选的页面↔特性覆盖表。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 唯一入口：新版通用引擎（8 张候选表 + 覆盖台账 + UNMAPPED 门禁）
import gmi_generate


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate migration candidate pack (8 tables)")
    ap.add_argument("--project", required=True, help="Android project root to scan")
    ap.add_argument("--workspace", required=True, help="output directory: candidates/ + coverage/")
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
