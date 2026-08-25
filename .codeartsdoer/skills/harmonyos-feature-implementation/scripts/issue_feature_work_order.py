#!/usr/bin/env python3
"""Reject obsolete feature-level Phase 4 work-order requests."""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace")
    parser.add_argument("--feature-id")
    parser.parse_known_args()
    parser.error(
        "Feature-level Phase 4 orders are obsolete and are never translated. "
        "Issue one page order per frozen Page-ID and separate page and capability orders."
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
