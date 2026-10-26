//! Board annotations — per-game, per-ply arrows, highlights, comments.

use serde::{Deserialize, Serialize};
use tauri::State;

use crate::db::AppState;
use crate::errors::AppError;

#[derive(Serialize, Deserialize, Clone)]
pub struct Arrow { pub from: String, pub to: String, pub color: String }

#[derive(Serialize, Deserialize, Clone)]
pub struct Highlight { pub square: String, pub color: String }

#[derive(Serialize, Deserialize, Clone)]
pub struct Annotation {
    pub ply_number: i32,
    pub arrows: Vec<Arrow>,
    pub highlights: Vec<Highlight>,
    pub comment: Option<String>,
}

#[derive(Deserialize)]
pub struct SaveAnnotation {
    pub game_id: i64,
    pub ply_number: i32,
    pub arrows: Vec<Arrow>,
    pub highlights: Vec<Highlight>,
    pub comment: Option<String>,
}

#[tauri::command]
pub async fn get_annotations(
    state: State<'_, AppState>,
    game_id: i64,
) -> Result<Vec<Annotation>, AppError> {
    let rows: Vec<(i32, serde_json::Value, serde_json::Value, Option<String>)> = sqlx::query_as(
        "SELECT ply_number, arrows, highlights, comment
         FROM app.game_annotations WHERE game_id = $1 ORDER BY ply_number"
    )
    .bind(game_id)
    .fetch_all(&state.pool)
    .await?;

    Ok(rows
        .into_iter()
        .map(|(ply, arr, hl, cm)| Annotation {
            ply_number: ply,
            arrows: serde_json::from_value(arr).unwrap_or_default(),
            highlights: serde_json::from_value(hl).unwrap_or_default(),
            comment: cm,
        })
        .collect())
}

#[tauri::command]
pub async fn save_annotation(
    state: State<'_, AppState>,
    a: SaveAnnotation,
) -> Result<(), AppError> {
    let arrows_json = serde_json::to_value(&a.arrows).map_err(|e| AppError::Internal(e.to_string()))?;
    let highlights_json = serde_json::to_value(&a.highlights).map_err(|e| AppError::Internal(e.to_string()))?;

    sqlx::query(
        "INSERT INTO app.game_annotations
            (game_id, ply_number, arrows, highlights, comment, updated_at)
         VALUES ($1, $2, $3, $4, $5, NOW())
         ON CONFLICT (game_id, ply_number) DO UPDATE SET
            arrows = EXCLUDED.arrows,
            highlights = EXCLUDED.highlights,
            comment = EXCLUDED.comment,
            updated_at = NOW()",
    )
    .bind(a.game_id)
    .bind(a.ply_number)
    .bind(arrows_json)
    .bind(highlights_json)
    .bind(a.comment)
    .execute(&state.pool)
    .await?;
    Ok(())
}
