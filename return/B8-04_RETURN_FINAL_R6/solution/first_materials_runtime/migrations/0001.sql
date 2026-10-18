CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.scope_locks (
    scope_key text PRIMARY KEY,
    CONSTRAINT scope_locks_scope_key_nonempty
        CHECK (char_length(scope_key) BETWEEN 2 AND 4096)
);

CREATE TABLE IF NOT EXISTS {schema}.state (
    scope_key text NOT NULL,
    bucket text NOT NULL,
    item_key text NOT NULL,
    value jsonb NOT NULL,
    PRIMARY KEY (scope_key, bucket, item_key),
    CONSTRAINT state_scope_key_nonempty
        CHECK (char_length(scope_key) BETWEEN 2 AND 4096),
    CONSTRAINT state_bucket_identifier
        CHECK (
            char_length(bucket) BETWEEN 1 AND 128
            AND bucket = btrim(bucket)
        ),
    CONSTRAINT state_item_key_nonempty
        CHECK (char_length(item_key) BETWEEN 1 AND 4096)
);
