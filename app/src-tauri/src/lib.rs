//! Application initialization — tracing, DB pool, command registration.

mod analytics;
mod annotations;
mod capabilities;
mod coach;
mod daily_puzzle;
mod db;
mod engine_bridge;
mod errors;
mod eval_graph;
mod god_mode;
mod import;
mod opening_explorer;
mod panel_service;
mod personal;
mod play;
mod repo;
mod training;
mod training_tools;
mod weakness;

use tauri::Manager;
use tracing_subscriber::{fmt, EnvFilter};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .json()
        .init();

    tauri::Builder::default()
        .setup(|app| {
            // Use Tauri's built-in async runtime instead of creating a
            // throwaway one. The pool's background tasks need a runtime
            // that stays alive for the app's whole lifetime; Tauri's
            // does. A `tokio::runtime::Runtime::new()` here would be
            // dropped when the closure returns, causing every pool
            // operation to fail with "context is being shutdown."
            let pool = tauri::async_runtime::block_on(db::create_pool())
                .expect("failed to create database pool");
            app.manage(db::AppState { pool });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            // capabilities
            capabilities::get_capabilities,
            // coach (Stockfish + LLM)
            coach::coach_analyze,
            // import
            import::import_personal_games,
            // play vs Stockfish
            play::sf_play_move,
            play::save_played_game,
            // annotations
            annotations::get_annotations,
            annotations::save_annotation,
            // eval graph
            eval_graph::get_eval_curve,
            // god mode
            god_mode::analyze_god_mode,
            // opening explorer
            opening_explorer::explore_opening,
            opening_explorer::best_openings_vs,
            opening_explorer::spy_player_openings,
            // opening study + puzzles
            training_tools::get_due_opening_lines,
            training_tools::record_opening_attempt,
            training_tools::get_due_puzzles,
            training_tools::record_puzzle_attempt,
            training_tools::import_study_pgn,
            training_tools::get_puzzle_themes,
            // daily puzzle
            daily_puzzle::get_daily_puzzle,
            daily_puzzle::record_daily_attempt,
            // analytics (gambits, ECO, accuracy)
            analytics::get_participant_gambit_stats,
            analytics::get_participant_eco_stats,
            analytics::get_recent_accuracy,
            analytics::get_participant_summaries,
            analytics::get_gambit_matchup,
            // personal game management
            personal::get_opening_stats,
            personal::best_repertoire_against,
            personal::get_gambit_stats,
            personal::review_personal_game,
            // engine bridge (capability-gated in UI, but always available)
            engine_bridge::analyze_position,
            engine_bridge::get_style_weights,
            engine_bridge::set_style_weights,
            engine_bridge::count_adapters,
            engine_bridge::list_adapters,
            engine_bridge::save_position,
            engine_bridge::list_saved_positions,
            engine_bridge::get_active_profile,
            // weakness finder
            weakness::get_weakness_insights,
            weakness::refresh_weakness_insights,
            weakness::has_weakness_insights,
            // panel service (Legends panel — gated by engine_available)
            panel_service::panel_analysis,
            panel_service::panel_commentary,
            // training monitor (gated by training_available)
            training::get_training_state,
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                let _ = window.app_handle();
                panel_service::shutdown();
                play::shutdown();   // ← add this line
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
