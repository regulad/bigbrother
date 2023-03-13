# bigbrother

Big brother listens to your discord voice chats and lets you recall the audio data.

## Environment Variables

| Name                                 | Description                          | Default     |
|--------------------------------------|--------------------------------------|-------------|
| `BIGBROTHER_POSTGRES_HOST`           | Postgres host                        | `localhost` |
| `BIGBROTHER_POSTGRES_PORT`           | Postgres port                        | `5432`      |
| `BIGBROTHER_POSTGRES_USER`           | Postgres user                        | `postgres`  |
| `BIGBROTHER_POSTGRES_PASSWORD`       | Postgres password                    | `postgres`  |
| `BIGBROTHER_POSTGRES_DATABASE`       | Postgres database                    | `postgres`  |
| `BIGBROTHER_DISCORD_WEBHOOK`         | Discord webhook (see dislog)         |             |
| `BIGBROTHER_DISCORD_WEBHOOK_MESSAGE` | Discord webhook message (see dislog) |             |
| `BIGBROTHER_BOT_TOKEN`               | Discord token                        |             |


## Installation

```bash
poetry install
```

## Test

```bash
tox
```

## Running

```bash
docker-compose up -d
```
