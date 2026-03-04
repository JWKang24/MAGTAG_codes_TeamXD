# generated_server_integration_v1

Server-integrated runtime generated from current baseline.

## Main files
- `code.py` (entrypoint; line 1 is `import maintenance_mode`)
- `user_survey.py`
- `mode_change_one_button.py`
- `server_match_client.py`

## Required settings.toml additions
- `MATCH_ENABLE_SERVER=1`
- `MATCH_SERVER_BASE_URL="http://<server-ip>:8000"`
- `MATCH_SERVER_APP_KEY="<app-key>"`
- `MATCH_HTTP_TIMEOUT_S=2.0`
- `MATCH_OBSERVE_INTERVAL_S=2.0`
- `MATCH_REQUEST_INTERVAL_S=3.0`
- `MATCH_ERROR_BACKOFF_S=8.0`
- `MATCH_RSSI_RECHECK_DELTA=8`

## Interest ownership
- Device does not track `MY_INTERESTS` anymore.
- Interest profile should live on server and be keyed by device id.
- Optional testing path exists via `GET /v1/interests/{device_id}` in `server_match_client.py`.

## Matching behavior
- SEARCH and badge match behavior are driven by server `decision=true` only.
- Known non-matches are not actively re-queried while they remain nearby.
- No local fallback match path is used if server is unavailable.
- Client requests `return_topic=true` on `/v1/match`.
- Topic parsing uses a single server `topic` string and supports delimiters `|`, `,`, `;`.
- Device keeps at most 2 topics per matched peer.
- Topic priority is: server topics, then peer-broadcast topic, then `Conversation`.
- CHAT topics are locked at entry (no live topic swapping mid-chat).
- Topic image UI is all-or-nothing:
  - If all required topic images exist, render visual topic panel.
  - If any topic image is missing, fallback to text-based UI behavior.
