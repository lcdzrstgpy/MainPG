-- Durable execution epoch and progress lease for POD batch fencing.
-- Every worker-side mutation is an atomic conditional write keyed on
-- execution_epoch, so a stale worker cannot overwrite reaper-revoked state.
-- last_progress_at tracks inactivity separately from updated_at, which is
-- also touched by control operations (pause/cancel) that do not constitute
-- forward progress.

ALTER TABLE pod_customization_batches ADD COLUMN execution_epoch INTEGER NOT NULL DEFAULT 0;
ALTER TABLE pod_customization_batches ADD COLUMN last_progress_at TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_pod_batches_stale_check
    ON pod_customization_batches (status, last_progress_at)
    WHERE status IN ('generating_patterns', 'compositing', 'generating_titles');
