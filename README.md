# ninja-publisher

Butler's multi-platform content dispatcher. Pulls approved drafts from Notion, generates brand-styled hero images via MiniMax `image-01`, publishes articles to Medium with canonical URLs pointing back to `unicebondoc.com/blog/{slug}`, and cross-posts to LinkedIn via self-hosted Postiz.

## Architecture

- **Notion** — source of truth (approved drafts, status lifecycle)
- **Slack** — approval UI (Block Kit cards with hero + hook picker)
- **Medium** — primary long-form destination (via official API)
- **Postiz** (self-hosted) — LinkedIn (and future socials) with OAuth
- **MiniMax `image-01`** — hero image generation (Token Plan, free tier)
- **Telegram** — operational pings

## Status

Phase B complete — all core services landed locally with mock-backed unit tests. No VPS writes yet. Dispatcher, Makefile, and Postiz integration come in Phases C–D.

## Quickstart (local dev)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # fill in secrets
pytest tests/ -v
ruff check .
```

## Services

### `base.py`
`Article`, `PublishResult`, `PublishError`, `BasePublisher` ABC. Article is the
single typed contract passed between Notion, image gen, publishers, and Slack.

### `publishers/medium.py`
Medium API integration. Sets `canonicalUrl = https://unicebondoc.com/blog/{slug}`
(overridable via `CANONICAL_BASE_URL`) so Medium rel=canonical's back to the
portfolio. Caps tags at 5.

Pass `dry_run=True` to preview what would happen without hitting the API:

```python
pub = MediumPublisher(token="...", dry_run=True)
result = pub.publish(article, images=[])  # logs title, tags, endpoints — zero HTTP calls
```

### `services/image_gen.py`
MiniMax `image-01` wrapper. Locked brand prompt: bioluminescent / purple / teal
/ Filipino mystical / editorial / no text. Defaults to 16:9 for heroes; caller
can override for OG cards etc.

### `services/notion_client.py`
Typed wrapper around the official `notion-client` SDK. Property names are
centralized on `NotionClient.PROPS` / `PLATFORM_URL_PROPS` / `PLATFORM_STATS_PROPS`
so a schema rename touches one file. Articles in, Articles out — Notion dicts
never escape the module.

Methods: `query_rows_by_status`, `save_draft`, `update_status`,
`save_selected_hook`, `save_platform_url`, `save_stats`, `log_error`.

### `services/slack_handler.py`
Builds the Block Kit approval card:
- Hero image
- Title header + metadata context (word count, reading time, platforms)
- 10 numbered hook buttons (2 rows of 5) — user clicks to select
- Decision row: **Approve & Publish** (primary), **Edit draft**, **Reject** (danger with confirm dialog)

Also: `update_card_status(ts, state)` for in-place status rewrites, and
`parse_interaction(payload) -> InteractionEvent` which validates every action
id before returning.

### `services/telegram_notify.py`
Operational pings via raw Bot API (no SDK). `notify(message, urgent=False)` —
normal is silent (`disable_notification=True`); urgent prepends ⚠️ and makes
noise. Raises `TelegramError` on failure so callers can choose whether to
swallow on the publish path.

### `approval_server.py`
Flask webhook (port 8080, configurable via `PORT`).

- `POST /slack/interact` — verifies v0 HMAC + 5-min replay window, then dispatches:
  - `hook_N` → `NotionClient.save_selected_hook(N)` + card updated to "Hook N selected"
  - `approve_publish` → Notion status → "Publishing", card → "Publishing…"
  - `edit_draft` → returns Notion page URL
  - `reject_draft` → Notion status → "Rejected", card → "Rejected"
- `GET /health` — `{"status": "ok", "version": ...}` for Butler's systemd probe

Always returns 200 to Slack within the 3s window — exceptions are logged, never
propagated (Slack retries aggressively on non-200 and we don't want duplicate
dispatches).

## Layout

```
ninja-publisher/
├── base.py                      # Article / PublishResult / BasePublisher
├── publishers/
│   └── medium.py                # MediumPublisher  [Phase A]
├── services/
│   ├── image_gen.py             # MiniMax image-01 wrapper  [Phase A]
│   ├── notion_client.py         # Notion CRUD  [Phase B]
│   ├── slack_handler.py         # Block Kit approval card  [Phase B]
│   ├── telegram_notify.py       # Operational pings  [Phase B]
│   └── postiz_client.py         # LinkedIn via Postiz  [Phase C]
├── approval_server.py           # Flask webhook  [Phase B]
├── dispatcher.py                # Parallel fan-out  [Phase D]
├── stats_sync.py                # Daily metrics back to Notion  [Phase D]
└── tests/                       # 83 tests, all mocked, no real APIs
```

## Phases

- [x] **A** — Scaffold + MediumPublisher + MiniMax wrapper + tests
- [x] **B** — Notion + Slack + Telegram + approval server ← *(this PR)*
- [ ] **C** — Butler VPS audit + Postiz deploy + Postiz client
- [ ] **D** — Dispatcher + stats sync + Makefile + systemd + cron
- [ ] **E** — Live smoke test (blocked on LinkedIn OAuth approval)
