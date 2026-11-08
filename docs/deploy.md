# Deploy & accounts

## Local accounts (default)

| Account | Password | Role |
|---------|----------|------|
| admin | yukari-admin | Full console + training chatroom |
| trainer | yukari-train | `/train` only |

Change `config.yaml` `auth.users` before going public. Session secret via `AILIVE2D_SECRET` in `.env`.

## Training chatroom

- UI: `/train`
- API: `POST /api/train/chat` (no TTS / no VTS)
- History: `GET /api/train/history`

## Docker + nginx-proxy (same as trpg)

When the server already runs `jwilder/nginx-proxy` (+ acme-companion):

1. DNS: `ai-trainning.innovationowl.com` A/AAAA -> Layerstack IP
2. Confirm network: `docker network ls` (usually `nginx-proxy`)
3. Deploy:

```bash
git clone https://github.com/ariapurple/AIVtuber.git
cd AIVtuber
cp .env.example .env
# set AILIVE2D_SECRET, AILIVE2D_LLM_BASE_URL, LETSENCRYPT_EMAIL
docker compose up -d --build
```

Compose sets:

- `VIRTUAL_HOST` / `VIRTUAL_PORT=8787` for nginx-proxy routing
- `LETSENCRYPT_HOST` for auto SSL
- `expose: 8787` (no host port publish)
- join external network `nginx-proxy`

Open: `https://ai-trainning.innovationowl.com/login`

Safe with other stacks: only `up` this compose; do not `down` nginx-proxy.

### Local without proxy

Temporarily add `ports: ["8787:8787"]` and drop the external `nginx-proxy` network.

Note: containers usually cannot reach VTube Studio / local audio; Docker is for text training and remote admin. Keep VTS/TTS on the stream PC.

## Config hot reload (no downtime)

Live process watches `config.yaml` every ~2s. Editing persona / safety / TTS voice /
LLM model applies on the next poll — no restart, in-flight chat may still use the
old prompt until it finishes.

Manual: `POST /api/config/reload` (admin).

Still needs full restart: `auth.secret` (session cookies), Chroma `persist_dir`,
VTS host/port, or any Python code change.
