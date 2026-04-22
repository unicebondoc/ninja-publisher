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

Phase A scaffold — local package only, no VPS wiring yet. See `tests/` for mocked unit coverage.

## Quickstart (local dev)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in secrets
pytest tests/ -v
```

## Layout

```
ninja-publisher/
├── base.py                      # BasePublisher ABC
├── publishers/
│   └── medium.py                # MediumPublisher
├── services/
│   ├── image_gen.py             # MiniMax image-01 wrapper
│   ├── notion_client.py         # Notion CRUD  (Phase B)
│   ├── slack_handler.py         # Block Kit approval card  (Phase B)
│   ├── postiz_client.py         # LinkedIn via Postiz  (Phase C)
│   └── telegram_notify.py       # Operational pings  (Phase B)
├── dispatcher.py                # Parallel fan-out  (Phase D)
├── approval_server.py           # Flask webhook  (Phase B)
├── stats_sync.py                # Daily metrics back to Notion  (Phase D)
└── tests/
```

## Phases

- **A** — Scaffold + MediumPublisher + MiniMax wrapper + tests ← *(this PR)*
- **B** — Notion, Slack, Telegram, approval server
- **C** — Butler audit + Postiz deploy + Postiz client
- **D** — Dispatcher, stats sync, Makefile, systemd, cron
- **E** — Live smoke test (blocked on LinkedIn OAuth approval)
