# Run with Docker Compose

All commands run in the `service/` directory.

## Start the service

```bash
cp .env.example .env      # set DJANGO_SECRET_KEY and, if needed, DJANGO_ALLOWED_HOSTS
docker compose up --build -d
```

The service listens on port 8000. Only ExifTool and MediaInfo are installed and enabled by default.

## Optional profiles

Profiles start extra containers. A plain `docker compose up` starts none of them.

| Profile | Starts | Use it for |
|---|---|---|
| `redis` | `redis` and `mes-purge` | [Shared job state and purging](async-jobs.md) |
| `tika` | `tika` | [Apache Tika](optional-tools.md#apache-tika) |
| `docs` | `docs` | This documentation, on port 8001 |

Combine them by repeating the flag: `docker compose --profile redis --profile tika up -d`.

## Serve the documentation

```bash
docker compose --profile docs up --build
```

The site is then at <http://localhost:8001>. Set `MES_DOCS_URL=http://localhost:8001` in `.env` to link to it from the web UI footer.

## Configuration

Settings come from the environment (`.env`); see [Environment variables and build args](../reference/configuration.md). The `config/` directory is mounted read-only at `/app/config`, so [local format rules](local-format-rules.md) change without a rebuild.
