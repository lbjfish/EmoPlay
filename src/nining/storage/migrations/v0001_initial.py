"""Initial schema adapted from the reviewed storage design."""

SQL = r"""
CREATE TABLE playthroughs (
    playthrough_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    materials_manifest_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active','completed','archived')),
    default_view_mode TEXT NOT NULL CHECK (default_view_mode IN ('story','god')),
    metadata_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE branches (
    branch_id TEXT PRIMARY KEY,
    playthrough_id TEXT NOT NULL REFERENCES playthroughs(playthrough_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    parent_branch_id TEXT REFERENCES branches(branch_id),
    forked_from_checkpoint_id TEXT REFERENCES checkpoints(checkpoint_id),
    status TEXT NOT NULL CHECK (status IN ('active','merged','archived')),
    created_at_utc TEXT NOT NULL,
    UNIQUE (playthrough_id, name),
    UNIQUE (playthrough_id, branch_id)
);

CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    playthrough_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    status TEXT NOT NULL CHECK (status IN ('running','completed','failed','cancelled')),
    mode TEXT NOT NULL CHECK (mode IN ('offline','mock','local','hybrid','azure','release-view')),
    requested_storage TEXT NOT NULL CHECK (requested_storage IN ('auto','sqlite','file','memory')),
    effective_storage TEXT NOT NULL CHECK (effective_storage IN ('sqlite','file','memory')),
    fallback_reason TEXT,
    materials_manifest_sha256 TEXT NOT NULL,
    config_sha256 TEXT NOT NULL,
    code_version TEXT,
    capabilities_json TEXT NOT NULL,
    safe_error TEXT,
    FOREIGN KEY (playthrough_id, branch_id) REFERENCES branches(playthrough_id, branch_id)
);

CREATE TABLE story_events (
    story_event_id TEXT PRIMARY KEY,
    playthrough_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    run_id TEXT REFERENCES runs(run_id),
    trigger_id TEXT,
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL CHECK (actor IN ('user','system','rule','model_assist')),
    payload_json TEXT NOT NULL,
    reversible INTEGER NOT NULL CHECK (reversible IN (0,1)),
    parent_event_id TEXT REFERENCES story_events(story_event_id),
    created_at_utc TEXT NOT NULL,
    UNIQUE (branch_id, sequence_no),
    UNIQUE (playthrough_id, branch_id, story_event_id),
    CHECK (trigger_id IS NULL OR run_id IS NOT NULL),
    FOREIGN KEY (playthrough_id, branch_id) REFERENCES branches(playthrough_id, branch_id)
);

CREATE TABLE checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    playthrough_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    head_event_id TEXT REFERENCES story_events(story_event_id),
    generated_by_run_id TEXT REFERENCES runs(run_id),
    label TEXT,
    checkpoint_kind TEXT NOT NULL CHECK (
        checkpoint_kind IN ('auto','manual','trigger','pre_generation','post_generation','release')
    ),
    state_json TEXT NOT NULL,
    state_sha256 TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    UNIQUE (playthrough_id, checkpoint_id),
    UNIQUE (playthrough_id, branch_id, checkpoint_id),
    FOREIGN KEY (playthrough_id, branch_id) REFERENCES branches(playthrough_id, branch_id),
    FOREIGN KEY (playthrough_id, branch_id, head_event_id)
        REFERENCES story_events(playthrough_id, branch_id, story_event_id)
);

CREATE TABLE playthrough_heads (
    playthrough_id TEXT PRIMARY KEY REFERENCES playthroughs(playthrough_id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    checkpoint_id TEXT,
    head_event_id TEXT,
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY (playthrough_id, branch_id) REFERENCES branches(playthrough_id, branch_id),
    FOREIGN KEY (playthrough_id, branch_id, checkpoint_id)
        REFERENCES checkpoints(playthrough_id, branch_id, checkpoint_id),
    FOREIGN KEY (playthrough_id, branch_id, head_event_id)
        REFERENCES story_events(playthrough_id, branch_id, story_event_id)
);

CREATE TABLE save_slots (
    playthrough_id TEXT NOT NULL REFERENCES playthroughs(playthrough_id) ON DELETE CASCADE,
    slot_name TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    label TEXT,
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY (playthrough_id, slot_name),
    FOREIGN KEY (playthrough_id, checkpoint_id)
        REFERENCES checkpoints(playthrough_id, checkpoint_id)
);

CREATE TABLE feature_selections (
    selection_id TEXT PRIMARY KEY,
    playthrough_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    checkpoint_id TEXT,
    feature_key TEXT NOT NULL,
    option_key TEXT NOT NULL,
    config_json TEXT NOT NULL,
    selected_by TEXT NOT NULL CHECK (selected_by IN ('default','capability','user','system')),
    effective INTEGER NOT NULL CHECK (effective IN (0,1)),
    created_at_utc TEXT NOT NULL,
    FOREIGN KEY (playthrough_id, branch_id) REFERENCES branches(playthrough_id, branch_id),
    FOREIGN KEY (playthrough_id, branch_id, checkpoint_id)
        REFERENCES checkpoints(playthrough_id, branch_id, checkpoint_id)
);

CREATE TABLE provider_calls (
    call_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    trigger_id TEXT,
    story_event_id TEXT REFERENCES story_events(story_event_id),
    purpose TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT,
    mode TEXT NOT NULL CHECK (mode IN ('mock','local','azure')),
    endpoint_alias TEXT,
    request_sha256 TEXT NOT NULL,
    prompt_sha256 TEXT,
    parameters_json TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    latency_ms INTEGER CHECK (latency_ms IS NULL OR latency_ms >= 0),
    usage_json TEXT NOT NULL,
    cost_amount_micros INTEGER CHECK (cost_amount_micros IS NULL OR cost_amount_micros >= 0),
    cost_currency TEXT,
    billing_state TEXT NOT NULL CHECK (billing_state IN ('actual','estimated','zero','unknown')),
    status TEXT NOT NULL CHECK (status IN ('pending','success','failed','cancelled','unknown')),
    error_code TEXT,
    safe_error TEXT,
    retry_of_call_id TEXT REFERENCES provider_calls(call_id)
);

CREATE TABLE assets (
    asset_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    trigger_id TEXT,
    provider_call_id TEXT REFERENCES provider_calls(call_id),
    role TEXT NOT NULL CHECK (role IN ('reference','candidate','final','rejected','placeholder','draft')),
    media_type TEXT NOT NULL CHECK (media_type IN ('image','audio','video','json','document','other')),
    relative_path TEXT NOT NULL,
    mime_type TEXT,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    width_px INTEGER CHECK (width_px IS NULL OR width_px > 0),
    height_px INTEGER CHECK (height_px IS NULL OR height_px > 0),
    duration_ms INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
    sha256 TEXT NOT NULL,
    provenance TEXT NOT NULL CHECK (provenance IN ('deterministic','fixture','local','azure','manual')),
    final_eligible INTEGER NOT NULL CHECK (final_eligible IN (0,1)),
    created_at_utc TEXT NOT NULL,
    UNIQUE (run_id, relative_path),
    CHECK (substr(relative_path, 1, 1) <> '/'),
    CHECK (instr(relative_path, '..') = 0)
);

CREATE TABLE decisions (
    decision_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    trigger_id TEXT,
    subject_kind TEXT NOT NULL CHECK (
        subject_kind IN ('spec','asset','conflict','run','checkpoint','branch','feature','release')
    ),
    subject_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('accept','reject','needs_review','supersede','block')),
    reason TEXT NOT NULL,
    decided_by TEXT NOT NULL CHECK (decided_by IN ('human','rule','model_assist')),
    supersedes_decision_id TEXT REFERENCES decisions(decision_id),
    created_at_utc TEXT NOT NULL
);

CREATE INDEX idx_branches_playthrough ON branches(playthrough_id, created_at_utc);
CREATE INDEX idx_story_events_branch_sequence
    ON story_events(playthrough_id, branch_id, sequence_no);
CREATE INDEX idx_checkpoints_branch_created
    ON checkpoints(playthrough_id, branch_id, created_at_utc);
CREATE INDEX idx_feature_selections_branch
    ON feature_selections(playthrough_id, branch_id, feature_key, created_at_utc);
CREATE INDEX idx_calls_run_time ON provider_calls(run_id, started_at_utc);
CREATE INDEX idx_assets_run_role ON assets(run_id, role);
CREATE INDEX idx_decisions_run_time ON decisions(run_id, created_at_utc);

CREATE VIEW v_playthrough_history AS
SELECT p.playthrough_id, b.branch_id, b.name AS branch_name,
       e.story_event_id, e.sequence_no, e.event_type, e.actor,
       e.trigger_id, e.created_at_utc
FROM playthroughs AS p
JOIN branches AS b ON b.playthrough_id = p.playthrough_id
JOIN story_events AS e ON e.branch_id = b.branch_id;

CREATE VIEW v_provider_costs AS
SELECT r.playthrough_id, r.branch_id, c.run_id, c.provider, c.model,
       c.cost_currency, COUNT(*) AS call_count,
       SUM(CASE WHEN c.status = 'success' THEN 1 ELSE 0 END) AS success_count,
       SUM(c.cost_amount_micros) AS total_cost_micros,
       SUM(c.latency_ms) AS total_latency_ms
FROM provider_calls AS c
JOIN runs AS r ON r.run_id = c.run_id
GROUP BY r.playthrough_id, r.branch_id, c.run_id, c.provider, c.model, c.cost_currency;
"""
