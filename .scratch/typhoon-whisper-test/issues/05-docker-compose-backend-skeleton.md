# 05: docker-compose brings up TimescaleDB + a live backend

**What to build:** Running `docker-compose up` starts a TimescaleDB container and a FastAPI backend container, on the same docker network, reachable from the host. `curl localhost:<port>/health` returns 200. No event storage yet — this ticket only proves the two containers exist, can talk to each other, and are reachable, per ADR 0003 (backend + database containerized; Speech-to-Text stays native for mic access).

**Blocked by:** None (can start immediately)

**Status:** done

- [x] `docker-compose up` starts a TimescaleDB container and a backend (FastAPI) container
- [x] The backend container can reach the TimescaleDB container over the docker network (verified: `/health` runs `SELECT 1` through the backend's DB connection)
- [x] `GET /health` on the backend, from the host, returns a 200 response
- [x] Both containers restart cleanly with `docker-compose up` after `docker-compose down` (no manual cleanup needed)

Note: host port for Postgres/TimescaleDB is `5433`, not the default `5432` — that port was already taken by an unrelated project's container on this machine. Internal container-to-container traffic (backend → timescaledb) is unaffected, still on 5432.
