//! Locate the repo root so subprocesses can run `python -m engine.X`.
//!
//! All Python subprocess calls in the Rust backend must:
//!   1. Run from the repo root (cwd = root) so relative `-m` imports resolve
//!   2. Set PYTHONPATH = root so imports work even if cwd fails
//!   3. Use the venv Python binary directly so the right site-packages is used
//!
//! This module provides helpers for all three.

use std::path::PathBuf;

pub fn find_root() -> Option<PathBuf> {
    let mut dir = std::env::current_exe().ok()?;
    for _ in 0..10 {
        dir = dir.parent()?.to_path_buf();
        if dir.join("pyproject.toml").exists() && dir.join("config").is_dir() {
            return Some(dir);
        }
    }
    None
}

/// Returns the path to the venv Python if it exists, otherwise "python".
pub fn python_exe() -> String {
    if let Some(root) = find_root() {
        let candidate = root.join(".venv").join("Scripts").join("python.exe");
        if candidate.exists() {
            return candidate.display().to_string();
        }
    }
    "python".to_string()
}

/// Fully-configure a Command to run a Python module from the repo root.
/// Sets: exe = venv python, cwd = repo root, PYTHONPATH = repo root.
pub fn python_command(module: &str, extra_args: &[&str]) -> std::process::Command {
    let mut cmd = std::process::Command::new(python_exe());
    cmd.arg("-m").arg(module);
    for a in extra_args {
        cmd.arg(a);
    }
    if let Some(root) = find_root() {
        cmd.current_dir(&root);
        // PYTHONPATH ensures modules are importable even if cwd fails
        // for any reason (e.g., path resolution issue on a weird mount).
        let existing = std::env::var("PYTHONPATH").unwrap_or_default();
        let sep = if cfg!(windows) { ";" } else { ":" };
        let new_path = if existing.is_empty() {
            root.display().to_string()
        } else {
            format!("{}{}{}", root.display(), sep, existing)
        };
        cmd.env("PYTHONPATH", new_path);
    }
    cmd
}
