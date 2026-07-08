CREATE TABLE IF NOT EXISTS llm_inference_events (
  event_id VARCHAR PRIMARY KEY,
  provider VARCHAR NOT NULL,
  model VARCHAR NOT NULL,
  status VARCHAR NOT NULL CHECK (status IN ('success', 'error')),
  error_type VARCHAR,
  error_message VARCHAR,
  started_at VARCHAR NOT NULL,
  ended_at VARCHAR NOT NULL,
  latency_ms INTEGER NOT NULL,
  session_id VARCHAR,
  conversation_id VARCHAR,
  request_id VARCHAR,
  input_preview VARCHAR,
  output_preview VARCHAR,
  input_preview_length INT NOT NULL DEFAULT 0,
  output_preview_length INT NOT NULL DEFAULT 0,
  input_tokens INT,
  output_tokens INT,
  total_tokens INT,
  has_error INT NOT NULL,
  metadata_json VARCHAR NOT NULL,
  metadata_keys_json VARCHAR NOT NULL,
  raw_event_json VARCHAR NOT NULL,
  client_ip VARCHAR,
  user_agent VARCHAR,
  received_at VARCHAR NOT NULL
);
CREATE INDEX IF NOT EXISTS llm_events_started_at_idx
  ON llm_inference_events (started_at);

CREATE INDEX IF NOT EXISTS llm_events_session_id_idx
  ON llm_inference_events (session_id);

CREATE INDEX IF NOT EXISTS llm_events_conversation_id_idx
  ON llm_inference_events (conversation_id);

CREATE INDEX IF NOT EXISTS llm_events_provider_model_idx
  ON llm_inference_events (provider, model);

CREATE INDEX IF NOT EXISTS llm_events_status_idx
  ON llm_inference_events (status);


CREATE TABLE IF NOT EXISTS conversations(
  conversation_id VARCHAR PRIMARY KEY,
  session_id VARCHAR(200) NOT NULL DEFAULT '',
  title VARCHAR(200) NOT NULL DEFAULT '',
  provider VARCHAR(100) NOT NULL DEFAULT '',
  model VARCHAR(100) NOT NULL DEFAULT '',
  created_at VARCHAR(200) NOT NULL,
  updated_at VARCHAR(200) NOT NULL
);

CREATE INDEX IF NOT EXISTS conversations_session_id_idx
  ON conversations (session_id);


CREATE TABLE IF NOT EXISTS conversation_messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id VARCHAR NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
  role VARCHAR(100) NOT NULL CHECK (role IN ('User', 'Assistant')),
  content TEXT NOT NULL,
  created_at VARCHAR NOT NULL
);

CREATE INDEX IF NOT EXISTS conv_messages_conversation_id_idx
  ON conversation_messages (conversation_id);
