"""Transaction-local checkpoint undo records for the pinned saver table keys."""

from __future__ import annotations

from typing import Any

TABLE_KEYS = {
    "checkpoints": ("thread_id", "checkpoint_ns", "checkpoint_id"),
    "checkpoint_writes": ("thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"),
    "checkpoint_blobs": ("thread_id", "checkpoint_ns", "channel", "version"),
}


def key_match(table: str, row: str = "t", undo: str = "u") -> str:
    return " AND ".join(
        f"{row}.{key}=({undo}.row_key->>'{key}')" + ("::integer" if key == "idx" else "")
        for key in TABLE_KEYS[table]
    )


async def install_journal(connection: Any) -> None:
    """Called under the schema setup advisory lock, after the native saver setup."""
    async with connection.transaction():
        for table, keys in TABLE_KEYS.items():
            key_json = ", ".join(f"'{key}', source.{key}" for key in keys)
            await connection.execute(f"""
                CREATE OR REPLACE FUNCTION graphharbor_journal_{table}() RETURNS trigger
                LANGUAGE plpgsql AS $$
                DECLARE source {table}; writer text;
                BEGIN
                    IF TG_OP='DELETE' THEN source := OLD; ELSE source := NEW; END IF;
                    IF TG_OP <> 'DELETE' AND current_setting('graphharbor.checkpoint_format', true)
                            IS DISTINCT FROM 'journal-v1'
                       AND (EXISTS(SELECT 1 FROM threads WHERE thread_id::text=source.thread_id)
                            OR EXISTS(SELECT 1 FROM runs
                                WHERE run_id::text=source.thread_id AND thread_id IS NULL)) THEN
                        RAISE EXCEPTION 'checkpoint writer requires journal-v1; drain old workers'
                            USING ERRCODE='55000';
                    END IF;
                    writer := nullif(current_setting('graphharbor.checkpoint_writer', true), '');
                    IF writer IS NOT NULL AND (TG_OP <> 'UPDATE' OR NEW IS DISTINCT FROM OLD) THEN
                        INSERT INTO run_checkpoint_undo(run_id, table_name, row_key, before_image)
                        VALUES(writer::uuid, '{table}', jsonb_build_object({key_json}),
                            CASE WHEN TG_OP='INSERT' THEN NULL ELSE to_jsonb(OLD) END)
                        ON CONFLICT DO NOTHING;
                    END IF;
                    RETURN NULL;
                END $$
            """)
            await connection.execute(f"DROP TRIGGER IF EXISTS graphharbor_journal ON {table}")
            await connection.execute(f"""
                CREATE TRIGGER graphharbor_journal AFTER INSERT OR UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION graphharbor_journal_{table}()
            """)
