use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};

const IGNORED_DIRS: &[&str] = &[
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "target",
    "coverage",
    ".next",
    ".turbo",
    ".worktrees",
    ".task-worktrees",
    ".venvs",
    ".codex_tmp",
    ".codex-tmp",
    ".direnv",
    ".nox",
    "__pycache__",
];

const PROJECT_FILES: &[&str] = &[
    "package.json",
    "pnpm-workspace.yaml",
    "yarn.lock",
    "pnpm-lock.yaml",
    "package-lock.json",
    "pyproject.toml",
    "poetry.lock",
    "uv.lock",
    "setup.cfg",
    "tox.ini",
    "pytest.ini",
    "ruff.toml",
    "Cargo.toml",
    "go.mod",
    "go.work",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "Makefile",
    "makefile",
    "justfile",
    "Taskfile.yml",
    "Taskfile.yaml",
    "composer.json",
    "Gemfile",
    "mix.exs",
    "WORKSPACE",
    "WORKSPACE.bazel",
    "MODULE.bazel",
    "BUILD",
    "BUILD.bazel",
    "CMakeLists.txt",
    "meson.build",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
    ".pre-commit-config.yaml",
];

const CI_FILES: &[&str] = &[
    ".gitlab-ci.yml",
    ".gitlab-ci.yaml",
    "azure-pipelines.yml",
    "azure-pipelines.yaml",
    "Jenkinsfile",
];

#[derive(Default)]
struct Scan {
    top_level: Vec<String>,
    project_files: Vec<String>,
    ci_files: Vec<String>,
    truncated: bool,
    budget_exhausted: bool,
}

fn ignored(name: &str) -> bool {
    IGNORED_DIRS.contains(&name)
}

fn is_project(name: &str) -> bool {
    PROJECT_FILES.contains(&name)
        || requirements_file(name)
        || nonempty_extension(name, ".sln")
        || nonempty_extension(name, ".csproj")
}

fn requirements_file(name: &str) -> bool {
    if name == "requirements.txt" {
        return true;
    }
    let Some(suffix) = name
        .strip_prefix("requirements-")
        .and_then(|value| value.strip_suffix(".txt"))
    else {
        return false;
    };
    !suffix.is_empty()
        && suffix
            .bytes()
            .all(|value| value.is_ascii_alphanumeric() || matches!(value, b'_' | b'.' | b'-'))
}

fn nonempty_extension(name: &str, extension: &str) -> bool {
    name.strip_suffix(extension)
        .is_some_and(|stem| !stem.is_empty())
}

fn is_ci(relative: &str, name: &str) -> bool {
    CI_FILES.contains(&name)
        || (relative.starts_with(".github/workflows/")
            && (name.ends_with(".yml") || name.ends_with(".yaml")))
        || relative == ".circleci/config.yml"
        || relative == ".circleci/config.yaml"
        || (relative.starts_with(".buildkite/")
            && (name.ends_with(".yml") || name.ends_with(".yaml")))
}

fn sorted_entries(directory: &Path, case_insensitive: bool) -> std::io::Result<Vec<PathBuf>> {
    let mut entries = fs::read_dir(directory)?
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .collect::<Vec<_>>();
    entries.sort_by(|left, right| {
        let left = left.file_name().unwrap_or_default().to_string_lossy();
        let right = right.file_name().unwrap_or_default().to_string_lossy();
        if case_insensitive {
            left.to_lowercase().cmp(&right.to_lowercase())
        } else {
            left.cmp(&right)
        }
    });
    Ok(entries)
}

fn relative_string(path: &Path, root: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace(std::path::MAIN_SEPARATOR, "/")
}

fn walk(
    root: &Path,
    current: &Path,
    depth: usize,
    max_depth: usize,
    max_items: usize,
    scan: &mut Scan,
) {
    let Ok(entries) = sorted_entries(current, false) else {
        return;
    };
    let mut directories = Vec::new();
    let mut files = Vec::new();
    for path in entries {
        let name = path.file_name().unwrap_or_default().to_string_lossy();
        let Ok(kind) = fs::symlink_metadata(&path).map(|metadata| metadata.file_type()) else {
            continue;
        };
        if kind.is_symlink() {
            continue;
        }
        if kind.is_dir() {
            if !ignored(&name) {
                directories.push(path);
            }
        } else if kind.is_file() {
            files.push(path);
        }
    }
    for path in files {
        let name = path.file_name().unwrap_or_default().to_string_lossy();
        let relative = relative_string(&path, root);
        if is_project(&name) {
            if scan.project_files.len() < max_items {
                scan.project_files.push(relative.clone());
            } else {
                scan.truncated = true;
            }
        }
        if is_ci(&relative, &name) {
            if scan.ci_files.len() < max_items {
                scan.ci_files.push(relative);
            } else {
                scan.truncated = true;
            }
        }
    }
    if depth < max_depth {
        for directory in directories {
            walk(root, &directory, depth + 1, max_depth, max_items, scan);
        }
    }
}

fn json_string(value: &str) -> String {
    let mut result = String::from("\"");
    for character in value.chars() {
        match character {
            '\\' => result.push_str("\\\\"),
            '"' => result.push_str("\\\""),
            '\n' => result.push_str("\\n"),
            '\r' => result.push_str("\\r"),
            '\t' => result.push_str("\\t"),
            value if value.is_control() => result.push_str(&format!("\\u{:04x}", value as u32)),
            value => result.push(value),
        }
    }
    result.push('"');
    result
}

fn json_array(values: &[String]) -> String {
    format!(
        "[{}]",
        values
            .iter()
            .map(|value| json_string(value))
            .collect::<Vec<_>>()
            .join(", ")
    )
}

fn scan_command(root: &Path, max_depth: usize, max_items: usize) -> std::io::Result<()> {
    let mut scan = Scan::default();
    let entries = sorted_entries(root, true)?;
    for path in entries {
        let name = path.file_name().unwrap_or_default().to_string_lossy();
        if ignored(&name) {
            continue;
        }
        let suffix = if path.is_dir() { "/" } else { "" };
        if scan.top_level.len() < max_items {
            scan.top_level.push(format!("{name}{suffix}"));
        } else {
            scan.truncated = true;
            break;
        }
    }
    walk(root, root, 0, max_depth, max_items, &mut scan);
    println!(
        "{{\"budget_exhausted\": {}, \"ci_files\": {}, \"project_files\": {}, \"top_level\": {}, \"truncated\": {}}}",
        if scan.budget_exhausted {
            "true"
        } else {
            "false"
        },
        json_array(&scan.ci_files),
        json_array(&scan.project_files),
        json_array(&scan.top_level),
        if scan.truncated { "true" } else { "false" }
    );
    Ok(())
}

fn usage() -> ExitCode {
    eprintln!(
        "usage: endurant-runtime-spike template | scan ROOT MAX_DEPTH MAX_ITEMS | spawn COUNT"
    );
    ExitCode::from(2)
}

fn main() -> ExitCode {
    let arguments = env::args().skip(1).collect::<Vec<_>>();
    match arguments.as_slice() {
        [command] if command == "template" => {
            print!("{}", include_str!("../template.json"));
            ExitCode::SUCCESS
        }
        [command, root, max_depth, max_items] if command == "scan" => {
            let Ok(max_depth) = max_depth.parse::<usize>() else {
                return usage();
            };
            let Ok(max_items) = max_items.parse::<usize>() else {
                return usage();
            };
            match scan_command(Path::new(root), max_depth, max_items) {
                Ok(()) => ExitCode::SUCCESS,
                Err(error) => {
                    eprintln!("endurant-runtime-spike: {error}");
                    ExitCode::from(2)
                }
            }
        }
        [command, count] if command == "spawn" => {
            let Ok(count) = count.parse::<usize>() else {
                return usage();
            };
            for _ in 0..count {
                match Command::new("/usr/bin/true").status() {
                    Ok(status) if status.success() => {}
                    Ok(status) => return ExitCode::from(status.code().unwrap_or(1) as u8),
                    Err(error) => {
                        eprintln!("endurant-runtime-spike: {error}");
                        return ExitCode::from(2);
                    }
                }
            }
            println!("spawned={count}");
            ExitCode::SUCCESS
        }
        _ => usage(),
    }
}

#[cfg(test)]
mod tests {
    use super::{ignored, is_project};

    #[test]
    fn project_patterns_match_the_python_contract() {
        for accepted in [
            "requirements.txt",
            "requirements-dev.txt",
            "requirements-a_b.c-1.txt",
            "solution.sln",
            "app.csproj",
        ] {
            assert!(is_project(accepted), "expected project file: {accepted}");
        }
        for rejected in [
            "requirements-.txt",
            "requirements🔥.txt",
            ".sln",
            ".csproj",
            "solution.SLN",
        ] {
            assert!(!is_project(rejected), "unexpected project file: {rejected}");
        }
    }

    #[test]
    fn current_candidate_noise_directories_are_ignored() {
        for name in [
            ".worktrees",
            ".task-worktrees",
            ".venvs",
            ".codex_tmp",
            ".codex-tmp",
            ".direnv",
            ".nox",
            "__pycache__",
        ] {
            assert!(ignored(name), "expected ignored directory: {name}");
        }
    }
}
