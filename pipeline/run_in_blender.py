"""One-command driver for the Blender-side stages — no hand-written
snippets. Talks to the live Blender through the MCP add-on socket.

Usage (any Python, Blender open on your rig scene):

  python pipeline\\run_in_blender.py apply  action_specs\\<motion>.json
  python pipeline\\run_in_blender.py curves action_specs\\<motion>.json
  python pipeline\\run_in_blender.py stills action_specs\\<motion>.json [--frames 1,30,60]
  python pipeline\\run_in_blender.py all    action_specs\\<motion>.json

`apply` keys the action (Blender's UI freezes for a few minutes on a
300-frame clip — normal). `stills` defaults to the spec's
`qa_src_frames` converted to destination frames. `all` runs the three
in sequence. Exit code is nonzero on any in-Blender error.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_exec import execute_file  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "pipeline"

TEMPLATE = """import importlib, sys
sys.path.insert(0, {pipeline})
import apply_mixamo_fk
importlib.reload(apply_mixamo_fk)
{body}
"""

BODIES = {
    "apply": 'r = apply_mixamo_fk.run({spec})\n'
             'result = {{"action": r["action"], "frames": r["frames"], "constraints": r["constraints"]}}',
    "curves": 'result = apply_mixamo_fk.dump_curves({spec})',
    "stills": 'result = apply_mixamo_fk.run_stills_render({spec}, {frames})',
    "contact": 'result = apply_mixamo_fk.pair_mesh_contact({spec}, {other})',
}

TIMEOUTS = {"apply": 600.0, "curves": 400.0, "stills": 400.0, "contact": 1800.0}


def python_literal(value: str) -> str:
    """Encode paths as Python string literals before sending code to Blender."""
    return repr(value)


def repo_path(value: str | Path) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else REPO / path).resolve()


def dest_frames_from_spec(spec: dict) -> list[int]:
    src_fps = float(spec.get("src_fps", 24))
    dst_fps = float(spec.get("dst_fps", 30))
    return sorted({max(1, int(round((sf - 1) * dst_fps / src_fps + 1)))
                   for sf in spec.get("qa_src_frames", [1])})


def run_stage(stage: str, spec_path: Path, frames: list[int], other: Path | None = None) -> None:
    body = BODIES[stage].format(
        spec=python_literal(str(spec_path)),
        frames=frames,
        other=python_literal(str(other) if other else ""),
    )
    code = TEMPLATE.format(pipeline=python_literal(str(PIPELINE)), body=body)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        tmp = f.name
    try:
        res = execute_file(tmp, TIMEOUTS[stage])
    finally:
        Path(tmp).unlink(missing_ok=True)
    print(f"[{stage}]", json.dumps(res.get("result", res), indent=1))
    if res.get("status") != "ok":
        print(res.get("message", ""), file=sys.stderr)
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["apply", "curves", "stills", "contact", "all"])
    ap.add_argument("spec")
    ap.add_argument("--frames", help="comma-separated dest frames for stills (default: spec qa frames)")
    ap.add_argument("--with", dest="other", help="second character's spec, for the `contact` stage")
    args = ap.parse_args()

    spec_path = repo_path(args.spec)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    other_path = repo_path(args.other) if args.other else None
    frames = ([int(x) for x in args.frames.split(",")] if args.frames else dest_frames_from_spec(spec))
    if any(frame < 1 for frame in frames):
        raise SystemExit("frames must be positive destination-frame numbers")

    if args.stage == "contact" and not other_path:
        raise SystemExit("contact needs a second character: --with action_specs/<other>.json")
    if other_path and not other_path.is_file():
        raise SystemExit(f"second spec not found: {other_path}")
    stages = ["apply", "curves", "stills"] if args.stage == "all" else [args.stage]
    for s in stages:
        run_stage(s, spec_path, frames, other_path)


if __name__ == "__main__":
    main()
