# Deploy & accounts

## Local accounts (default)

| 帳號 | 密碼 | 角色 |
|------|------|------|
| admin | yukari-admin | 全部控制台 + 訓練 chatroom |
| trainer | yukari-train | 只見 `/train` 文字訓練房 |

上線前請改 `config.yaml` 內 `auth.users`（可改 password 明文後重跑一次 hash，或直接換 password_hash/salt）。

## 訓練 Chatroom

- 網址：`/train`
- API：`POST /api/train/chat`（無 TTS、無 VTS）
- 歷史：`GET /api/train/history`

## Docker

```bash
cp .env.example .env
# 改 AILIVE2D_SECRET 同 Ollama URL
docker compose up -d --build
```

開 `http://伺服器IP:8787/login`。

注意：容器內通常冇 VTube Studio／本機音效；Docker 部署主要用嚟做文字訓練同遠端管理。VTS／TTS 繼續喺串流機本地跑即可。
