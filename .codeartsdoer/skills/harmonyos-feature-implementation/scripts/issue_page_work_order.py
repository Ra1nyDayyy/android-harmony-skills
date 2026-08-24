#!/usr/bin/env python3
"""Issue one immutable page-owned Phase 4 work order."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from stage4_work_orders import issue_page_order


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--ui-understanding-agent-id")
    parser.add_argument("--codearts-task-id", required=True)
    parser.add_argument("--exclusive-code-path", action="append", required=True)
    args = parser.parse_args()
    try:
        path = issue_page_order(
            Path(args.workspace), args.page_id, args.owner_id, args.codearts_task_id,
            tuple(args.exclusive_code_path), args.ui_understanding_agent_id,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"work_order": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
