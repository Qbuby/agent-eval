-- Seed one agent reply for a candidate case so the candidate export path can be
-- verified with a non-empty "Agent reply" column. ASCII-only marker on purpose.
-- Cleanup: see cleanup-candidate-reply.sql
WITH v AS (
  INSERT INTO agent_reply_versions (
    id, dataset_type, case_ref, version_number, version_label,
    content, agent_config, status
  ) VALUES (
    gen_random_uuid(),
    'candidate',
    '3cdcb2c0-deb3-4f24-8f52-56d49038fa2f',
    1,
    'v1-verify119',
    'VERIFY119-CANDIDATE-REPLY-BODY: Paris is the capital of France.',
    '{}'::jsonb,
    'succeeded'
  )
  ON CONFLICT (dataset_type, case_ref, version_number) DO UPDATE
    SET content = EXCLUDED.content,
        version_label = EXCLUDED.version_label
  RETURNING id
)
INSERT INTO agent_reply_case_states (
  id, dataset_type, case_ref, current_version_id
)
SELECT gen_random_uuid(), 'candidate', '3cdcb2c0-deb3-4f24-8f52-56d49038fa2f', v.id
FROM v
ON CONFLICT (dataset_type, case_ref) DO UPDATE
  SET current_version_id = EXCLUDED.current_version_id;

SELECT dataset_type, case_ref, version_label, left(content, 40) AS body
FROM agent_reply_versions WHERE dataset_type = 'candidate';
