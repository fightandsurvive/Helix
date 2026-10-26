//! PostgreSQL connection pool — created once at startup, shared via AppState.

use sqlx::{postgres::PgPoolOptions, Pool, Postgres};

pub struct AppState {
    pub pool: Pool<Postgres>,
}

pub async fn create_pool() -> Result<Pool<Postgres>, sqlx::Error> {
    let url = std::env::var("DATABASE_URL")
        .unwrap_or_else(|_| "postgresql://trin:trin_dev@localhost:5432/helix_db".into());
    PgPoolOptions::new()
        .max_connections(10)
        .min_connections(2)
        .connect(&url)
        .await
}
