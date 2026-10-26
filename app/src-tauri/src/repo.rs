//! Locate the repo root so subprocesses can run `python -m engine.X`.

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
