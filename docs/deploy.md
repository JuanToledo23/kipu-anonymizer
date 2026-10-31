# Deploy (Fly.io)

The service runs on Fly.io as app `kipu-anonymizer` (org `juantoledo`), one always-on machine
(`shared-cpu-2x`, 1 GB) in `dfw` with the volume `data` mounted at `/data`. Config is `fly.toml`.

- Public URL: `https://kipu-anonymizer.fly.dev`; portal ENTRYPOINT: `https://kipu-anonymizer.fly.dev/v1`
- Every route is also served at the root (`/health`, `/anonymize`, ...). `GET /health` needs no token;
  everything else requires `Authorization: Bearer $ENTRYPOINT_TOKEN`.
- `/health` and `/anonymize` answer CORS from any origin (demo page); no other route does.
- `mia` is no longer a Fly region; `dfw` is the closest one (and what Fly maps `mia` to).

## Rule

**Never deploy, restart or rotate secrets while an evaluation run is in progress.** A deploy
restarts the machine, kills the running job and the run ends without a score but counts as used.
Check first: `fly logs -a kipu-anonymizer` must show no recent `/run` activity.

## Volume

`/data` holds `registry.sqlite` (person ids and surrogates) and `output/`. It is never rebuilt:
deleting or recreating the volume changes every pseudonym on the next run. The container starts
as root only to `chown` the mounted volume, then drops to the `app` user (`docker-entrypoint.sh`).
Fly takes daily volume snapshots (`fly volumes snapshots list <vol_id>`).

## First deploy

```bash
fly auth whoami
fly launch --no-deploy --copy-config --name kipu-anonymizer --region dfw --org juantoledo
fly volumes create data --region dfw --size 1 --yes -a kipu-anonymizer
# secrets from .env, values never echoed
bash -c 'set -a; . ./.env; set +a; fly secrets set --stage -a kipu-anonymizer \
  FUENTES_URL="$FUENTES_URL" FUENTES_TOKEN="$FUENTES_TOKEN" PSEUDONYM_SECRET="$PSEUDONYM_SECRET" \
  ENTRYPOINT_TOKEN="$ENTRYPOINT_TOKEN" OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
  ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" >/dev/null'
fly deploy -a kipu-anonymizer --ha=false
fly scale count 1 -a kipu-anonymizer
```

## Redeploy

```bash
make lint test
fly deploy -a kipu-anonymizer --ha=false
curl -s https://kipu-anonymizer.fly.dev/health
```

Keep exactly one machine: jobs and the registry lock live in one process, and the volume
attaches to one machine.

## Rotate a secret

```bash
bash -c 'set -a; . ./.env; set +a; fly secrets set -a kipu-anonymizer ENTRYPOINT_TOKEN="$ENTRYPOINT_TOKEN" >/dev/null'
fly secrets list -a kipu-anonymizer      # names and status only
```

`fly secrets set` restarts the machine (same rule as deploys). Never rotate `PSEUDONYM_SECRET`
after the first evaluation: new persons would get surrogates from a different seed. After
changing `ENTRYPOINT_TOKEN`, update it in the portal.

## Status and logs

```bash
fly status -a kipu-anonymizer
fly machines list -a kipu-anonymizer     # state and health checks
fly logs -a kipu-anonymizer              # live; add --no-tail for recent history
fly ssh console -a kipu-anonymizer       # shell on the machine (ls /data)
```

Logs carry request lines and job ids, never tokens or secrets.
