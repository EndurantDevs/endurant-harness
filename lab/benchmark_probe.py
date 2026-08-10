#!/usr/bin/env python3
"""Benchmark the unchanged and probe-diet runtimes on scoped and aggregate repositories."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from eval_lib import ARTIFACTS, LAB_ROOT, write_json


CURRENT = LAB_ROOT / "subjects" / "current" / "endurant-harness" / "scripts" / "endurant.py"


def run_probe(script: Path, root: Path, max_items: int = 10) -> tuple[float, dict[str, Any], int]:
    started = time.perf_counter()
    completed = subprocess.run(
        [
            sys.executable,
            "-S",
            str(script),
            "probe",
            "--repo",
            str(root),
            "--task",
            "speed candidate",
            "--format",
            "json",
            "--max-items",
            str(max_items),
        ],
        cwd=LAB_ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=90,
    )
    duration = time.perf_counter() - started
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.decode("utf-8", errors="replace")[-2000:])
    return duration, json.loads(completed.stdout), len(completed.stdout)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def summarize(samples: list[float], sizes: list[int]) -> dict[str, Any]:
    return {
        "samples_seconds": [round(value, 6) for value in samples],
        "p50_seconds": round(statistics.median(samples), 6),
        "p95_seconds": round(percentile(samples, 0.95), 6),
        "median_output_bytes": int(statistics.median(sizes)),
    }


def paired(
    root: Path, repetitions: int, candidate_script: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    for script in (CURRENT, candidate_script):
        run_probe(script, root)
    samples = {"current": [], "candidate": []}
    sizes = {"current": [], "candidate": []}
    payloads: dict[str, dict[str, Any]] = {}
    for index in range(repetitions):
        order = (("current", CURRENT), ("candidate", candidate_script))
        if index % 2:
            order = tuple(reversed(order))
        for name, script in order:
            duration, payload, output_size = run_probe(script, root)
            samples[name].append(duration)
            sizes[name].append(output_size)
            payloads[name] = payload
    return (
        summarize(samples["current"], sizes["current"]),
        summarize(samples["candidate"], sizes["candidate"]),
        payloads,
    )


def create_scoped_git(parent: Path) -> Path:
    root = parent / "scoped-git"
    (root / ".agents").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "AGENTS.md").write_text("# Instructions\nPreserve behavior.\n", encoding="utf-8")
    (root / ".agents" / "endurant-harness-profile.md").write_text(
        "# Profile\nFocused test: `python3 -m unittest`\n", encoding="utf-8"
    )
    (root / "pyproject.toml").write_text("[project]\nname='probe-fixture'\n", encoding="utf-8")
    (root / ".github" / "workflows" / "ci.yml").write_text("name: ci\n", encoding="utf-8")
    (root / "src" / "candidate.py").write_text("SPEED_CANDIDATE = True\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    return root


def create_git_ignored_noise(parent: Path) -> Path:
    root = parent / "git-ignored-noise"
    (root / "packages" / "visible").mkdir(parents=True)
    (root / "tracked-generated").mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname='probe-root'\n", encoding="utf-8"
    )
    (root / "packages" / "visible" / "Cargo.toml").write_text(
        "[package]\nname='visible'\n", encoding="utf-8"
    )
    (root / "tracked-generated" / "Cargo.toml").write_text(
        "[package]\nname='tracked'\n", encoding="utf-8"
    )
    deleted = root / "deleted" / "Cargo.toml"
    deleted.parent.mkdir()
    deleted.write_text("[package]\nname='deleted'\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "add",
            "pyproject.toml",
            "tracked-generated/Cargo.toml",
            "deleted/Cargo.toml",
        ],
        cwd=root,
        check=True,
    )
    deleted.unlink()
    (root / ".gitignore").write_text(
        "generated/\ntracked-generated/\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", ".gitignore"], cwd=root, check=True)
    for index in range(80):
        generated = root / "generated" / f"package-{index:03d}"
        generated.mkdir(parents=True)
        (generated / "pyproject.toml").write_text(
            f"[project]\nname='ignored-{index:03d}'\n", encoding="utf-8"
        )
    return root


def create_standalone(parent: Path) -> Path:
    root = parent / "standalone-nongit"
    (root / "src").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='standalone'\n", encoding="utf-8")
    (root / "src" / "candidate.py").write_text("SPEED_CANDIDATE = True\n", encoding="utf-8")
    return root


def create_aggregate(parent: Path) -> Path:
    root = parent / "aggregate-root"
    root.mkdir()
    for repository_index in range(18):
        repository = root / f"repo-{repository_index:02d}"
        (repository / ".git").mkdir(parents=True)
        source = repository / "src" / "nested"
        source.mkdir(parents=True)
        for file_index in range(180):
            (source / f"candidate-{file_index:03d}.txt").write_text(
                "speed candidate\n", encoding="utf-8"
            )
        ignored = repository / ".venv" / "lib"
        ignored.mkdir(parents=True)
        for file_index in range(20):
            (ignored / f"ignored-{file_index:03d}.txt").write_text(
                "speed candidate\n", encoding="utf-8"
            )
    return root


def important_payload(payload: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "git_worktree", "instructions", "profiles", "working_tree", "diff_stat",
        "top_level", "project_files", "ci_files", "command_hints", "task_terms",
        "candidate_paths",
    )
    return {key: payload.get(key) for key in keys}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-root", type=Path)
    parser.add_argument("--candidate-subject", default="probe-diet")
    parser.add_argument("--output-name", default="probe-diet.json")
    args = parser.parse_args()
    if Path(args.output_name).name != args.output_name or not args.output_name.endswith(".json"):
        parser.error("--output-name must be a JSON filename")
    candidate_script = (
        LAB_ROOT
        / "subjects"
        / args.candidate_subject
        / "endurant-harness"
        / "scripts"
        / "endurant.py"
    )
    if not candidate_script.is_file():
        parser.error(f"candidate subject does not exist: {args.candidate_subject}")
    benchmark_root = ARTIFACTS / "benchmarks"
    benchmark_root.mkdir(parents=True, exist_ok=True)
    fixture_parent = Path(tempfile.mkdtemp(prefix="probe-fixtures-"))
    scoped = create_scoped_git(fixture_parent)
    ignored_noise = create_git_ignored_noise(fixture_parent)
    standalone = create_standalone(fixture_parent)
    aggregate = create_aggregate(fixture_parent)

    results: dict[str, Any] = {
        "fixtures": f"<temporary>/{fixture_parent.name}",
        "candidate_subject": args.candidate_subject,
    }
    for name, root, repetitions in (
        ("scoped_git", scoped, 15),
        ("standalone_nongit", standalone, 15),
        ("aggregate_synthetic", aggregate, 7),
    ):
        current, candidate, payloads = paired(root, repetitions, candidate_script)
        results[name] = {
            "current": current,
            "candidate": candidate,
            "median_improvement_fraction": round(
                (current["p50_seconds"] - candidate["p50_seconds"])
                / current["p50_seconds"],
                6,
            ),
            "important_payload_equal": important_payload(payloads["current"])
            == important_payload(payloads["candidate"]),
            "candidate_repository_choices": payloads["candidate"].get("repository_choices", []),
            "candidate_incomplete": payloads["candidate"].get("incomplete"),
        }

    noise_current, noise_candidate, noise_payloads = paired(
        ignored_noise, 31, candidate_script
    )
    candidate_projects = noise_payloads["candidate"].get("project_files", [])
    results["git_ignored_noise"] = {
        "current": noise_current,
        "candidate": noise_candidate,
        "median_improvement_fraction": round(
            (noise_current["p50_seconds"] - noise_candidate["p50_seconds"])
            / noise_current["p50_seconds"],
            6,
        ),
        "current_project_files": noise_payloads["current"].get("project_files", []),
        "candidate_project_files": candidate_projects,
        "candidate_excludes_ignored": not any(
            path.startswith("generated/") for path in candidate_projects
        ),
        "candidate_preserves_tracked_ignored": (
            "tracked-generated/Cargo.toml" in candidate_projects
        ),
        "candidate_preserves_visible_untracked": (
            "packages/visible/Cargo.toml" in candidate_projects
        ),
        "candidate_excludes_deleted_tracked": (
            "deleted/Cargo.toml" not in candidate_projects
        ),
        "absolute_regression_bounded": (
            noise_candidate["p50_seconds"] - noise_current["p50_seconds"] <= 0.05
        ),
    }

    if args.real_root:
        current_duration, current_payload, current_size = run_probe(CURRENT, args.real_root)
        candidate_samples: list[float] = []
        candidate_sizes: list[int] = []
        candidate_payload: dict[str, Any] = {}
        for _ in range(5):
            duration, candidate_payload, output_size = run_probe(candidate_script, args.real_root)
            candidate_samples.append(duration)
            candidate_sizes.append(output_size)
        results["real_aggregate_root"] = {
            "path": "<local-aggregate-workspace>",
            "current": summarize([current_duration], [current_size]),
            "candidate": summarize(candidate_samples, candidate_sizes),
            "observed_improvement_fraction": round(
                (current_duration - statistics.median(candidate_samples)) / current_duration,
                6,
            ),
            "current_incomplete": current_payload.get("incomplete"),
            "candidate_incomplete": candidate_payload.get("incomplete"),
            "candidate_repository_choices": [
                f"repo-{index:02d}/"
                for index, _ in enumerate(candidate_payload.get("repository_choices", []))
            ],
        }

    results["passed"] = bool(
        results["scoped_git"]["important_payload_equal"]
        and results["standalone_nongit"]["important_payload_equal"]
        and results["aggregate_synthetic"]["candidate_incomplete"] is True
        and len(results["aggregate_synthetic"]["candidate_repository_choices"]) == 10
        and results["git_ignored_noise"]["candidate_excludes_ignored"] is True
        and results["git_ignored_noise"]["candidate_preserves_tracked_ignored"] is True
        and results["git_ignored_noise"]["candidate_preserves_visible_untracked"] is True
        and results["git_ignored_noise"]["candidate_excludes_deleted_tracked"] is True
        and results["git_ignored_noise"]["absolute_regression_bounded"] is True
    )
    write_json(benchmark_root / args.output_name, results)
    print(json.dumps(results, indent=2, sort_keys=True))
    shutil.rmtree(fixture_parent)
    return 0 if results["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
