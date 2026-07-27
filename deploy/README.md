# Deploying the Expertise Explorer

Target: `http://ritmeisz8d02.chmcres.cchmc.org/expert/`, behind the host's nginx,
whole stack in Docker Compose.

## How the sub path works

One variable drives it. `BASE_PATH=/expert/` in the server's `.env` becomes a
Docker build argument, which Vite uses for two things: it rewrites every asset
URL in the built HTML, and it exposes the value to the app as
`import.meta.env.BASE_URL`. The app derives its API base and its admin route from
that, so no source file contains the string `/expert`.

The container's own nginx keeps serving from its root. The host nginx strips the
`/expert` prefix on the way in, using the trailing slash on `proxy_pass`. That
means the container is identical whether the app is mounted at the root or under
a prefix, and only two places know the mount point: the server's `.env` and the
host nginx config.

```
browser  GET /expert/assets/app.js
   host nginx   location /expert/   proxy_pass http://127.0.0.1:8080/   strips prefix
      container nginx   GET /assets/app.js   serves from disk

browser  POST /expert/api/query/stream
   host nginx   location /expert/api/   proxy_pass http://127.0.0.1:8011/api/
      backend   POST /api/query/stream
```

## First deployment

```bash
git clone https://github.com/rohzzn/dbeexpert.git && cd dbeexpert
```

```bash
cp .env.example .env
```

Edit `.env` and set, at minimum:

```
BASE_PATH=/expert/
BIND_HOST=127.0.0.1
OPENAI_API_KEY=<the real key>
NEO4J_PASSWORD=<a strong password>
POSTGRES_PASSWORD=<a strong password>
ADMIN_PASSWORD=<a strong password>
```

Every one of those secrets is required. Compose refuses to start and names the
missing variable rather than falling back to a placeholder password.

Put the graph in place. It is 163 MB, so it is not in Git:

```bash
mkdir -p dump && cp /path/to/neo4j.dump dump/neo4j.dump
```

Install the nginx config. Copy the blocks from `deploy/nginx-expert.conf` into the
existing `server { }` for this host, replacing the old `/expert/` block that
pointed at Streamlit on 8501, then:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

Bring the stack up:

```bash
docker compose down && docker compose up -d --build
```

## Every deployment after that

```bash
git pull && docker compose down && docker compose up -d --build
```

`down` without `-v` keeps the volumes, so the graph and the feedback data
survive. The dump is restored only when the volume is empty, guarded by a
`/data/.dbe-restored` marker, so repeated deploys never re-import or wipe it.

`--build` matters more than it looks: `BASE_PATH` is baked into the bundle at
build time, so a plain `up -d` after changing it would serve a stale bundle.

## Verifying a deployment

Run these on the server, in order. Each one isolates a different layer, so the
first failure tells you where the problem is.

**1. Containers.** Three healthy, one up, one exited zero.

```bash
docker compose ps
```

`neo4j-load` showing `Exited (0)` is correct: it is a one shot restore job, not a
service.

**2. Ports.** Exactly two, both on loopback.

```bash
docker compose ps --format '{{.Name}}\t{{.Ports}}'
```

Expect `127.0.0.1:8080->80` and `127.0.0.1:8011->8000`. If either shows
`0.0.0.0`, `BIND_HOST` is wrong and the containers are reachable around nginx.

**3. Databases are not exposed.** All three must fail to connect.

```bash
for p in 7474 7687 5432; do (echo > /dev/tcp/127.0.0.1/$p) 2>/dev/null && echo "$p OPEN, problem" || echo "$p closed, correct"; done
```

**4. Backend directly, bypassing nginx.** Isolates the app from the proxy.

```bash
curl -s http://127.0.0.1:8011/api/health
```

Expect `"status":"ok"`, `neo4j.connected` true, `nodes` 43915, and
`openai.configured` true. A `degraded` status means the OpenAI key is missing or
Neo4j has not finished starting.

**5. Through nginx at the public URL.** Isolates the proxy.

```bash
curl -s http://ritmeisz8d02.chmcres.cchmc.org/expert/api/health
```

Same payload as step 4. A 404 here with step 4 passing means the nginx
`location /expert/api/` block or its trailing slashes are wrong.

**6. The app shell and its assets.** All must be 200, and the asset paths must be
`/expert/`-prefixed.

```bash
curl -s http://ritmeisz8d02.chmcres.cchmc.org/expert/ | grep -oE '(src|href)="[^"]+"'
```

Expect `/expert/assets/index-*.js`, `/expert/assets/index-*.css`, and
`/expert/favicon.png`. If these come back without the prefix, `BASE_PATH` was not
set at build time; rebuild with `--build`.

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://ritmeisz8d02.chmcres.cchmc.org/expert/assets/$(curl -s http://ritmeisz8d02.chmcres.cchmc.org/expert/ | grep -oE 'assets/index-[^"]+\.js' | head -1 | cut -d/ -f2)
```

**7. Redirect and admin route.**

```bash
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://ritmeisz8d02.chmcres.cchmc.org/expert
curl -s -o /dev/null -w '%{http_code}\n' http://ritmeisz8d02.chmcres.cchmc.org/expert/admin
```

Expect `301` then `200`.

**8. Streaming, the one most likely to be silently broken.** Watch the
timestamps, not just the output.

```bash
curl -sN -X POST http://ritmeisz8d02.chmcres.cchmc.org/expert/api/query/stream \
  -H 'Content-Type: application/json' \
  -d '{"question":"Which faculty have expertise in cystic fibrosis?","mode":"hybrid","sessionId":"deploy-check"}' \
  | while IFS= read -r line; do printf '%s %s\n' "$(date +%T)" "$line"; done
```

Events must start appearing **immediately**, with timestamps spread across the
next 25 to 45 seconds. If nothing prints and then everything arrives at once, an
nginx layer is buffering: check `proxy_buffering off` and the empty `Connection`
header in the `/expert/api/` block.

**9. A real answer in the browser.** Open
`http://ritmeisz8d02.chmcres.cchmc.org/expert/` and ask "Which faculty have
expertise in cystic fibrosis?". Expect live pipeline stages, then three faculty
cards with scores. Then open the browser console and confirm there are no 404s
and no requests to a bare `/api/...`.

**10. Feedback and admin.** Submit feedback from an answer, then open
`/expert/admin`, enter `ADMIN_PASSWORD`, and confirm the row appears with its
question, answer, and technical details attached.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Blank page, console 404s for `/assets/...` without the prefix | Built without `BASE_PATH`. Rebuild with `--build`. |
| App loads, every API call 404s | The `/expert/api/` nginx block is missing, or is declared after `/expert/`, or lost a trailing slash. |
| Answers appear all at once after a long pause | An nginx layer is buffering the SSE stream. See step 8. |
| `/expert/admin` shows the chat instead of the admin page | Bundle predates the base aware route check. Rebuild. |
| Compose exits naming a variable | That secret is missing from `.env`. This is deliberate. |
| Neo4j restart loops with `Unrecognized setting` | An env var on the neo4j service starts with `NEO4J_` but is not a real Neo4j setting. That image turns every `NEO4J_*` var into a config key. |
| Graph empty after a deploy | The volume was removed. `down -v` and `make reset-db` do that; plain `down` does not. |
