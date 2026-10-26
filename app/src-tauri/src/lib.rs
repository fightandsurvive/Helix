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
mod opening_explorer;
mod personal;
mod play;
mod training_tools;
mod weakness;
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
            let rt = tokio::runtime::Runtime::new()?;
            let pool = rt.block_on(db::create_pool())?;
            app.manage(db::AppState { pool });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            // capabilities
            capabilities::get_capabilities,
            // coach
            coach::coach_analyze,
            // play — game management, move submission
            play::sf_play_move,
            play::save_played_game,
            // annotations — per-game, per-ply arrows, highlights, comments
            annotations::get_annotations,
            annotations::save_annotation,
            // eval graph — per-ply eval points + user-move markers
            eval_graph::get_eval_curve,
            // engine bridge — style weights, adapters
            engine_bridge::analyze_position,
            engine_bridge::get_style_weights,
            engine_bridge::set_style_weights,
            engine_bridge::count_adapters,
            engine_bridge::list_adapters,
            // opening explorer
            opening_explorer::explore_opening,
            opening_explorer::best_openings_vs,
            opening_explorer::spy_player_openings,
            // legends panel (persistent service)
            panel_service::panel_analysis,
            panel_service::panel_commentary,
            // god mode
            god_mode::analyze_god_mode,
            // training telemetry
            training::get_training_state,
            // training tools (opening study, puzzle trainer)
            training_tools::get_due_opening_lines,
            training_tools::record_opening_attempt,
            training_tools::get_due_puzzles,
            training_tools::record_puzzle_attempt,
            training_tools::import_study_pgn,
            training_tools::get_puzzle_themes,
            // daily puzzle
            daily_puzzle::get_daily_puzzle,
            daily_puzzle::record_daily_attempt,
            // analytics
            analytics::get_participant_gambit_stats,
            analytics::get_participant_eco_stats,
            analytics::get_recent_accuracy,
            analytics::get_participant_summaries,
            analytics::get_gambit_matchup,
            weakness::get_weakness_insights,
            weakness::refresh_weakness_insights,
            weakness::has_weakness_insights,
            // personal game management
            personal::get_opening_stats,
            personal::best_repertoire_against,
            personal::get_gambit_stats,
            personal::review_personal_game,
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                let _ = window.app_handle();
                panel_service::shutdown();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
