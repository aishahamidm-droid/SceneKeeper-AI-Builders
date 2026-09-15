from agent import execute_clickhouse_query, execute_clickhouse_write

commands = [
    """
    ALTER TABLE scenekeeper.scenes
    ADD COLUMN IF NOT EXISTS production_id String DEFAULT 'demo_sample'
    """,
    """
    ALTER TABLE scenekeeper.character_states
    ADD COLUMN IF NOT EXISTS production_id String DEFAULT 'demo_sample'
    """,
    """
    ALTER TABLE scenekeeper.prop_tracker
    ADD COLUMN IF NOT EXISTS production_id String DEFAULT 'demo_sample'
    """,
    """
    CREATE TABLE IF NOT EXISTS scenekeeper.productions (
        production_id String,
        title String,
        imported_at DateTime DEFAULT now(),
        scene_count UInt16,
        character_count UInt16,
        prop_count UInt16
    )
    ENGINE = ReplacingMergeTree()
    PRIMARY KEY production_id
    """,
    """
    INSERT INTO scenekeeper.productions
        (production_id, title, scene_count, character_count, prop_count)
    SELECT
        'demo_sample',
        'Demo Script (Sample)',
        2,
        1,
        1
    WHERE NOT EXISTS (
        SELECT 1
        FROM scenekeeper.productions
        WHERE production_id = 'demo_sample'
    )
    """
]

for i, command in enumerate(commands, 1):
    print(f"\n=== MIGRATION {i} ===")
    print(execute_clickhouse_write(command))

print("\n=== PRODUCTIONS ===")
print(execute_clickhouse_query(
    "SELECT * FROM scenekeeper.productions"
))

print("\n=== SCENES ===")
print(execute_clickhouse_query(
    "DESCRIBE TABLE scenekeeper.scenes"
))

print("\n=== CHARACTER STATES ===")
print(execute_clickhouse_query(
    "DESCRIBE TABLE scenekeeper.character_states"
))

print("\n=== PROP TRACKER ===")
print(execute_clickhouse_query(
    "DESCRIBE TABLE scenekeeper.prop_tracker"
))