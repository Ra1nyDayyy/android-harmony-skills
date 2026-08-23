#!/usr/bin/env python3
"""Create an immutable controller workspace for one migration run."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
ASSETS = SKILL_ROOT / "assets"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", value.strip()).strip("-").upper()
    return normalized[:32] or "PROJECT"


def render_template(name: str, replacements: dict[str, str]) -> str:
    text = (ASSETS / name).read_text(encoding="utf-8")
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="Directory that will contain migration runs")
    parser.add_argument("--project-root", required=True, help="Absolute or relative Android project root")
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--run-id", help="Optional explicit run ID; must not already exist")
    args = parser.parse_args()

    project_root = Path(args.project_root).expanduser().resolve()
    if not project_root.is_dir():
        parser.error(f"Android project root does not exist: {project_root}")

    output_input = Path(args.output).expanduser().absolute()
    if output_input.is_symlink():
        parser.error("Output root must not be a symbolic link")
    output_root = output_input.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = args.run_id or f"MIG-{stamp}-{uuid.uuid4().hex[:6].upper()}"
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{2,79}", run_id):
        parser.error("Run ID may contain only uppercase letters, numbers, dot, underscore, and hyphen")

    final_dir = output_root / run_id
    if final_dir.exists():
        parser.error(f"Run already exists; overwrite is prohibited: {final_dir}")

    created_at = utc_now()
    replacements = {
        "__RUN_ID__": run_id,
        "__PROJECT_ID__": slug(args.project_name),
        "__PROJECT_ROOT__": str(project_root),
        "__CREATED_AT__": created_at,
    }

    with tempfile.TemporaryDirectory(prefix=f".{run_id}-", dir=output_root) as temp_name:
        temp_dir = Path(temp_name)
        controller = temp_dir / "controller"
        controller.mkdir()

        scope_text = render_template("scope.template.json", replacements)
        json.loads(scope_text)
        (controller / "scope.json").write_text(scope_text + "\n", encoding="utf-8")

        for source, target in (
            ("task-ledger.template.csv", "task-ledger.csv"),
            ("decision-log.template.csv", "decision-log.csv"),
            ("rework-log.template.csv", "rework-log.csv"),
            ("work-order-registry.template.csv", "work-order-registry.csv"),
            ("evidence-anchor-registry.template.csv", "evidence-anchor-registry.csv"),
        ):
            shutil.copyfile(ASSETS / source, controller / target)

        gate_text = render_template("gate-report.template.json", replacements)
        json.loads(gate_text)
        (controller / "gate-report.json").write_text(gate_text + "\n", encoding="utf-8")

        manifest = {
            "run_id": run_id,
            "project_id": replacements["__PROJECT_ID__"],
            "project_root": str(project_root),
            "created_at": created_at,
            "controller_skill": "android-harmony-migration-controller",
            "status": "IN_PROGRESS",
        }
        (temp_dir / "run-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        temp_dir.rename(final_dir)

    print(json.dumps({"run_id": run_id, "run_dir": str(final_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
