//! Typed error surface — serializes to structured JSON for the frontend.

use serde::Serialize;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum AppError {
    #[error("database error: {0}")]
    Database(#[from] sqlx::Error),

    #[error("http error: {0}")]
    Http(#[from] reqwest::Error),

    #[error("invalid input: {0}")]
    InvalidInput(String),

    #[error("not found: {0}")]
    NotFound(String),

    #[error("internal error: {0}")]
    Internal(String),
}

impl Serialize for AppError {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        use serde::ser::SerializeStruct;
        let mut s = serializer.serialize_struct("AppError", 2)?;
        s.serialize_field(
            "kind",
            match self {
                AppError::Database(_) => "database",
                AppError::Http(_) => "http",
                AppError::InvalidInput(_) => "invalid_input",
                AppError::NotFound(_) => "not_found",
                AppError::Internal(_) => "internal",
            },
        )?;
        s.serialize_field("message", &self.to_string())?;
        s.end()
    }
}
