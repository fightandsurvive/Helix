//! Entry point — delegates to lib::run().

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    helix_app_lib::run();
}
