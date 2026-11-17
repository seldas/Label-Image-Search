import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

import numpy as np
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PostgresStore:
    """Unified PostgreSQL + pgvector store for SPL metadata, FTS (tsvector), vectors (HNSW), and sync state."""

    def __init__(self, dsn: str | None = None, dim: int | None = None) -> None:
        self.dsn = dsn or settings.database_url
        self.dim = dim or settings.embedding_dim
        self._pool: ConnectionPool | None = None

    def _get_pool(self) -> ConnectionPool:
        if self._pool is None:
            def _configure(conn: Any) -> None:
                register_vector(conn)

            self._pool = ConnectionPool(
                conninfo=self.dsn,
                min_size=1,
                max_size=10,
                configure=_configure,
                kwargs={"row_factory": dict_row},
            )
        return self._pool

    @contextmanager
    def connect(self) -> Iterator[Any]:
        pool = self._get_pool()
        with pool.connection() as conn:
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def sync_db(self) -> None:
        """
        Idempotent schema creation & additive column/index migration.
        Safe to run on every app startup or upgrade.
        """
        dim = self.dim
        # First ensure vector extension exists before register_vector is used on new pools
        with ConnectionPool(conninfo=self.dsn, min_size=1, max_size=2).connection() as raw_conn:
            raw_conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            raw_conn.commit()

        with self.connect() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS spl_documents (
                    setid TEXT PRIMARY KEY,
                    version_number INTEGER DEFAULT 0,
                    effective_time TEXT DEFAULT '',
                    product_name TEXT DEFAULT '',
                    generic_name TEXT DEFAULT '',
                    manufacturer TEXT DEFAULT '',
                    category TEXT DEFAULT '',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS labeling_images (
                    image_id TEXT PRIMARY KEY,
                    md5_hash TEXT UNIQUE NOT NULL,
                    phash TEXT NOT NULL DEFAULT '',
                    width INTEGER NOT NULL DEFAULT 0,
                    height INTEGER NOT NULL DEFAULT 0,
                    orig_rel_path TEXT NOT NULL,
                    thumb_rel_path TEXT NOT NULL,
                    vlm_caption TEXT DEFAULT '',
                    embedding vector({dim}),
                    search_tsv tsvector,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS image_spl_links (
                    id BIGSERIAL PRIMARY KEY,
                    image_id TEXT NOT NULL REFERENCES labeling_images(image_id) ON DELETE CASCADE,
                    setid TEXT NOT NULL REFERENCES spl_documents(setid) ON DELETE CASCADE,
                    media_id TEXT DEFAULT '',
                    section_title TEXT DEFAULT '',
                    xml_context TEXT DEFAULT '',
                    is_active BOOLEAN DEFAULT TRUE,
                    UNIQUE(image_id, setid, media_id)
                );

                CREATE TABLE IF NOT EXISTS sync_logs (
                    id BIGSERIAL PRIMARY KEY,
                    sync_type TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    status TEXT NOT NULL,
                    setids_processed INTEGER DEFAULT 0,
                    images_added INTEGER DEFAULT 0,
                    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    finished_at TIMESTAMPTZ,
                    error_message TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS sync_requests (
                    id BIGSERIAL PRIMARY KEY,
                    sync_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    started_at TIMESTAMPTZ,
                    finished_at TIMESTAMPTZ,
                    message TEXT DEFAULT ''
                );
                """
            )

            # Declarative additive column migrations for future app updates
            migrations = [
                ("spl_documents", "version_number", "INTEGER DEFAULT 0"),
                ("spl_documents", "effective_time", "TEXT DEFAULT ''"),
                ("spl_documents", "category", "TEXT DEFAULT ''"),
                ("labeling_images", "vlm_caption", "TEXT DEFAULT ''"),
                ("labeling_images", "embedding", f"vector({dim})"),
                ("labeling_images", "search_tsv", "tsvector"),
                ("image_spl_links", "is_active", "BOOLEAN DEFAULT TRUE"),
            ]
            for table, col, col_def in migrations:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_def};")

            # Indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_images_phash ON labeling_images(phash);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_links_setid ON image_spl_links(setid);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_links_image ON image_spl_links(image_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_images_tsv ON labeling_images USING GIN (search_tsv);")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_images_embedding_hnsw "
                "ON labeling_images USING hnsw (embedding vector_cosine_ops);"
            )
        logger.info("PostgreSQL schema & indexes synced successfully.")

    def is_initial_sync_needed(self) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM sync_logs WHERE sync_type = 'full' AND status = 'completed'"
            ).fetchone()
            return (row["cnt"] if row else 0) == 0

    def is_filename_processed(self, filename: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM sync_logs WHERE filename = %s AND status = 'completed' LIMIT 1",
                (filename,),
            ).fetchone()
            return row is not None

    def start_sync_log(self, sync_type: str, filename: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "INSERT INTO sync_logs (sync_type, filename, status, started_at) "
                "VALUES (%s, %s, 'running', %s) RETURNING id",
                (sync_type, filename, _utc_now()),
            ).fetchone()
            return int(row["id"])

    def finish_sync_log(
        self,
        log_id: int,
        status: str,
        setids_processed: int = 0,
        images_added: int = 0,
        error_message: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE sync_logs
                SET status = %s, setids_processed = %s, images_added = %s, finished_at = %s, error_message = %s
                WHERE id = %s
                """,
                (status, setids_processed, images_added, _utc_now(), error_message, log_id),
            )

    def find_image_by_md5(self, md5_hash: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT image_id FROM labeling_images WHERE md5_hash = %s LIMIT 1",
                (md5_hash,),
            ).fetchone()
            return row["image_id"] if row else None

    def upsert_spl_document(
        self,
        setid: str,
        version_number: int,
        effective_time: str,
        product_name: str,
        generic_name: str,
        manufacturer: str,
        category: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE image_spl_links SET is_active = FALSE WHERE setid = %s", (setid,))
            conn.execute(
                """
                INSERT INTO spl_documents (
                    setid, version_number, effective_time, product_name,
                    generic_name, manufacturer, category, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(setid) DO UPDATE SET
                    version_number = EXCLUDED.version_number,
                    effective_time = EXCLUDED.effective_time,
                    product_name = EXCLUDED.product_name,
                    generic_name = EXCLUDED.generic_name,
                    manufacturer = EXCLUDED.manufacturer,
                    category = EXCLUDED.category,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    setid,
                    version_number,
                    effective_time,
                    product_name,
                    generic_name,
                    manufacturer,
                    category,
                    _utc_now(),
                ),
            )

    def insert_image_and_link(
        self,
        image_id: str,
        md5_hash: str,
        phash: str,
        width: int,
        height: int,
        orig_rel_path: str,
        thumb_rel_path: str,
        setid: str,
        media_id: str,
        section_title: str,
        xml_context: str,
        product_name: str,
        generic_name: str,
        manufacturer: str,
        vlm_caption: str = "",
    ) -> bool:
        """Inserts image if new, links to SetID, and updates search_tsv. Returns True if newly created."""
        is_new = False
        tsv_text = " ".join(
            p for p in [product_name, generic_name, manufacturer, section_title, xml_context, vlm_caption] if p
        )
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT image_id FROM labeling_images WHERE md5_hash = %s",
                (md5_hash,),
            ).fetchone()
            if existing:
                image_id = existing["image_id"]
                conn.execute(
                    "UPDATE labeling_images SET search_tsv = to_tsvector('english', %s) WHERE image_id = %s",
                    (tsv_text, image_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO labeling_images (
                        image_id, md5_hash, phash, width, height,
                        orig_rel_path, thumb_rel_path, vlm_caption, search_tsv, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, to_tsvector('english', %s), %s)
                    """,
                    (
                        image_id,
                        md5_hash,
                        phash,
                        width,
                        height,
                        orig_rel_path,
                        thumb_rel_path,
                        vlm_caption,
                        tsv_text,
                        _utc_now(),
                    ),
                )
                is_new = True

            conn.execute(
                """
                INSERT INTO image_spl_links (
                    image_id, setid, media_id, section_title, xml_context, is_active
                ) VALUES (%s, %s, %s, %s, %s, TRUE)
                ON CONFLICT(image_id, setid, media_id) DO UPDATE SET
                    section_title = EXCLUDED.section_title,
                    xml_context = EXCLUDED.xml_context,
                    is_active = TRUE
                """,
                (image_id, setid, media_id, section_title, xml_context),
            )
        return is_new

    def upsert_vectors(self, records: list[dict[str, Any]]) -> None:
        """Updates embedding column for a batch of {'image_id': str, 'vector': list[float]}."""
        if not records:
            return
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "UPDATE labeling_images SET embedding = %s WHERE image_id = %s",
                    [(np.array(r["vector"], dtype=np.float32), r["image_id"]) for r in records],
                )

    def search_similar(self, query_vector: list[float], limit: int = 50) -> list[tuple[str, float]]:
        """pgvector cosine similarity search returning [(image_id, similarity_score)]."""
        vec = np.array(query_vector, dtype=np.float32)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT image_id, 1 - (embedding <=> %s) AS similarity
                FROM labeling_images
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s
                LIMIT %s
                """,
                (vec, vec, limit),
            ).fetchall()
            return [(row["image_id"], max(0.0, float(row["similarity"]))) for row in rows]

    def fts_search(self, query: str, limit: int = 50) -> list[tuple[str, float]]:
        """PostgreSQL full-text search using websearch_to_tsquery + ts_rank."""
        clean = " ".join(query.strip().split())
        if not clean:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT image_id, ts_rank(search_tsv, q) AS rank
                FROM labeling_images, websearch_to_tsquery('english', %s) q
                WHERE search_tsv @@ q
                ORDER BY rank DESC
                LIMIT %s
                """,
                (clean, limit),
            ).fetchall()
            return [(row["image_id"], float(row["rank"])) for row in rows]

    def get_images_by_ids(self, image_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not image_ids:
            return {}
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT ON (img.image_id)
                    img.image_id, img.width, img.height,
                    img.orig_rel_path, img.thumb_rel_path, img.vlm_caption,
                    lnk.setid, lnk.section_title, lnk.xml_context,
                    doc.product_name, doc.generic_name, doc.manufacturer, doc.category
                FROM labeling_images img
                LEFT JOIN image_spl_links lnk ON img.image_id = lnk.image_id AND lnk.is_active = TRUE
                LEFT JOIN spl_documents doc ON lnk.setid = doc.setid
                WHERE img.image_id = ANY(%s)
                ORDER BY img.image_id, lnk.id DESC
                """,
                (image_ids,),
            ).fetchall()
            return {row["image_id"]: dict(row) for row in rows}

    def get_stats(self) -> dict[str, Any]:
        with self.connect() as conn:
            total_images = conn.execute("SELECT COUNT(*) AS cnt FROM labeling_images").fetchone()["cnt"]
            total_docs = conn.execute("SELECT COUNT(*) AS cnt FROM spl_documents").fetchone()["cnt"]
            last_sync = conn.execute(
                "SELECT sync_type, filename, status, setids_processed, images_added, "
                "COALESCE(finished_at::text, '') AS finished_at "
                "FROM sync_logs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return {
                "total_images": total_images,
                "total_spl_documents": total_docs,
                "last_sync": dict(last_sync) if last_sync else None,
            }

    def enqueue_sync_request(self, sync_type: str) -> int:
        with self.connect() as conn:
            active = conn.execute(
                "SELECT id, sync_type, status FROM sync_requests WHERE status IN ('pending', 'running') LIMIT 1"
            ).fetchone()
            if active:
                raise ValueError(
                    f"Sync already {active['status']} (id={active['id']}, type={active['sync_type']})."
                )
            row = conn.execute(
                "INSERT INTO sync_requests (sync_type, status, requested_at) "
                "VALUES (%s, 'pending', %s) RETURNING id",
                (sync_type, _utc_now()),
            ).fetchone()
            return int(row["id"])

    def claim_next_sync_request(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                UPDATE sync_requests
                SET status = 'running', started_at = %s
                WHERE id = (
                    SELECT id FROM sync_requests
                    WHERE status = 'pending'
                    ORDER BY id ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, sync_type, requested_at::text
                """,
                (_utc_now(),),
            ).fetchone()
            return dict(row) if row else None

    def complete_sync_request(self, req_id: int, status: str, message: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE sync_requests SET status = %s, finished_at = %s, message = %s WHERE id = %s",
                (status, _utc_now(), message, req_id),
            )

    def get_sync_logs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, sync_type, filename, status, setids_processed, images_added,
                       started_at::text, COALESCE(finished_at::text, '') AS finished_at, error_message
                FROM sync_logs
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_active_sync_status(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, sync_type, status, requested_at::text, COALESCE(started_at::text, '') AS started_at
                FROM sync_requests
                WHERE status IN ('pending', 'running')
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            return dict(row) if row else None
