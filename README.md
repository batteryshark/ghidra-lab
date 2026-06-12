# Ghidra Lab

A home-lab Ghidra you can drive two ways at once: **you** in the Ghidra GUI, and
**an AI agent** over MCP — both working the same Ghidra Server repositories.

Two containers, one image:

- **`ghidra-server`** — a stock Ghidra Server, the canonical store for your work.
  Holds your repositories and user accounts (`admin` for you, `agent` for the
  lab). Ports `13100-13102`.
- **`ghidra-lab`** — the MCP endpoint. Runs the upstream
  [`bethington/ghidra-mcp`](https://github.com/bethington/ghidra-mcp) headless
  server and bridge plus a thin FastMCP facade that adds sample uploads and a
  Ghidra Server round-trip, all behind one bearer-authenticated streamable-HTTP
  URL. It connects to the server as the `agent` account — effectively a Ghidra
  client the agent operates.

```
agent ──MCP/HTTP──▶ ghidra-lab ──Ghidra client (agent)──▶ ghidra-server ◀──GUI── you
                    (facade + bridge + headless)            (repositories)
```

The facade keeps the upstream Ghidra tool names un-namespaced, so existing
GhidraMCP prompts still apply. `list_tools` stays small (the lab tools plus
`search_tools`/`call_tool`); the full upstream catalog is reachable through
search.

## Start

```bash
git clone https://github.com/batteryshark/ghidra-lab.git
cd ghidra-lab
cp .env.example .env
openssl rand -hex 32                              # → set GHIDRA_LAB_TOKEN in .env
openssl rand -hex 24 > .ghidra-agent-password     # the agent account password
docker compose up -d --build
```

Edit `.env` for your network before `up` (see [Running on a server](#running-on-a-server-over-tailscale))
and set `GHIDRA_VERSION` to match the Ghidra version your GUI runs.

The `ghidra-server` container creates the `agent` account on first boot from
`.ghidra-agent-password`; the `ghidra-lab` container creates a server-bound
shared project for `GHIDRA_LAB_DEFAULT_REPOSITORY` and then comes up. First boot
takes a few minutes (Ghidra download + build).

Expose the MCP endpoint on your tailnet:

```bash
sudo tailscale serve --bg --tcp 8081 tcp://127.0.0.1:18080
```

Connect an agent to:

```text
http://<tailscale-ip>:8081/mcp
Authorization: Bearer <GHIDRA_LAB_TOKEN>
```

Turn the listener off with `sudo tailscale serve --tcp=8081 off`.

## Agent flow

1. `lab_sample_upload_start(filename=…)` → returns an `upload_url` that already
   carries a one-time per-sample token, plus a ready-to-run `curl_example`. The
   agent (which holds the MCP bearer only via its harness) can therefore upload
   without that token — it just `PUT`s the bytes to the URL, no auth header.
2. `PUT` the raw file bytes to `upload_url`.
3. `lab_repository_import_sample(sample_id=…)` → imports + auto-analyzes the
   sample into the server repository as version 1 (immediately visible in the
   GUI), then loads it read-only and leaves it **unlocked**. Because
   analyzeHeadless takes minutes on a real binary, this runs in the **background**
   and returns immediately with `status: running`; poll
   `lab_repository_import_status(sample_id)` until `complete`. Pass
   `checkout_for_edit=true` when the agent will edit and check it back in.
4. Explore and annotate with the upstream Ghidra tools via
   `search_tools(query="…")` and `call_tool(name=…, arguments=…)`.
5. `lab_repository_checkin(path=<program_path>, comment="…")` → saves a new
   version on the server.

## Your GUI flow

Open Ghidra on your desktop, **File → New Project → Shared Project**, connect to
`<tailscale-ip>:13100` as `admin`, and open the `GhidraLab` repository. The
agent's imports and check-ins show up there with full version history.

If the `agent` account ever fails to auto-create, make it once by hand:

```bash
docker compose exec ghidra-server /opt/ghidra/server/svrAdmin -add agent --p
```

## Organizing work — one repo, folders per project

Everything lives in the `GhidraLab` repository, separated by **folder**, not piled
together. Give each piece of work its own folder:

- On import, pass `folder` (e.g. `lab_repository_import_sample(sample_id, folder="/operationX")`),
  or set the sample's `collection` and it becomes the folder. Browse with
  `lab_repository_list(path="/operationX")`.
- Continue an existing program with `lab_repository_load_program(path="/operationX/sample")`,
  then `lab_repository_checkin` when done.

**Handing your own work to the agent:** open the `GhidraLab` repository in your
desktop Ghidra (it's a *shared project* bound to that one repo), work in a folder,
and check in. The agent is bound to the same repo, so
`lab_repository_load_program(path="/yourfolder/binary")` checks it out and
continues it — its check-ins become new versions in your history. (Verified: a
file added outside the agent's session is found and round-tripped to a new
version.) The reverse works too: you open what the agent imported. Standard
Ghidra checkout locking applies — whoever holds an exclusive checkout edits until
they check in.

If you ever want hard isolation (a *separate repository* per engagement, or the
agent attaching to a repo you created as its own shared project), that's a small
generalization on top of this — ask and it can be added.

## Pinned tools

`lab_sample_upload_start`, `lab_sample_list`, `lab_sample_get`,
`lab_server_status`, `lab_repository_list`, `lab_repository_import_sample`,
`lab_repository_load_program`, `lab_repository_checkin`, plus the upstream
`list_instances`, `connect_instance`, `check_tools` and the synthetic
`search_tools` / `call_tool`. Quick-look (`lab_sample_import`, in-memory only)
and `lab_sample_download_url` / `lab_sample_delete` exist but are discoverable
through search rather than pinned.

The bridge auto-exposes the headless server's raw project/version-control
endpoints as MCP tools, several of which are agent footguns (read-only loads
that can't save, headless-broken imports, session-stomping project ops,
destructive deletes). The facade hides them from `list_tools` **and** search via
`HIDDEN_BRIDGE_TOOLS` in [main.py](facade/ghidra_lab_mcp/main.py) so agents reach
for the orchestrated `lab_repository_*` tools instead. The facade still calls the
underlying REST endpoints directly — hiding only affects the agent-facing MCP
surface.

## Verify

```bash
scripts/smoke.sh              # build, start, full end-to-end check
scripts/smoke.sh --no-build   # against an already-running stack
```

It confirms the agent account, repository, and shared project exist; that the
host auth surface is correct (`/healthz` 200, unauthenticated `/mcp` 401); and
runs an upload → import → analyze → check-in round-trip through MCP.

## Running on a server (over Tailscale)

Clone the repo on the server and set `.env` for tailnet addressing: put the
host's Tailscale IPv4 in `GHIDRA_LAB_BIND`, `GHIDRA_SERVER_PUBLIC_HOST`, and
`GHIDRA_AGENT_SERVER_HOST`, and `http://<tailscale-ip>:8081` in
`GHIDRA_LAB_PUBLIC_BASE_URL`; keep `GHIDRA_AGENT_SERVER_USER=agent` and set
`GHIDRA_LAB_REPO_USERS` to your GUI account name. Then:

```bash
docker compose up -d --build
sudo tailscale serve --bg --tcp 8081 tcp://127.0.0.1:18080
scripts/smoke.sh --no-build
```

`.env`, the `.ghidra-*` secret files, and `data/` are gitignored, so they stay
on the server and never get committed.

## Addressing note

The agent reaches the server at `GHIDRA_AGENT_SERVER_HOST`. Using the host's
Tailscale IP there means one address works for both the lab container and your
desktop GUI, and matches the server's TLS certificate — at the cost of coupling
the lab to `tailscaled` being up. The in-compose name `ghidra-server` (already a
certificate alternate name) is the alternative used for self-contained local
tests.

## Notes

- Imports use `analyzeHeadless -connect … -commit` (the only headless import
  path — the native `/import_file` is GUI-only and `/server/version_control/add`
  is a no-op stub headless).
- Check-out, save, and check-in go through one small added service,
  `LabVersionControlService`, that drives Ghidra's project DomainFile API.
  Upstream's headless `/server/version_control/*` leave the checked-out program
  read-only and don't actually commit (ghidra-mcp discussion #119), so this is
  the piece that makes the agent's edits land as new server versions. It is a
  single self-contained class plus one registration line, injected at
  [image/extensions/LabVersionControlService.java](image/extensions/LabVersionControlService.java)
  and validated by the Maven compile during the build.
- Re-importing over a program that is currently checked out will fail
  server-side; check it in (or undo the checkout) first.
- Set `GHIDRA_VERSION`/`GHIDRA_DATE` in `.env` to match the Ghidra version your
  GUI clients run (default `12.1.2`). A program's data uses version-specific
  language definitions, so an older headless agent cannot open programs a newer
  GUI created.
- The Apple-Silicon decompiler native is absent from the stock Ghidra release,
  so `decompile_*` tools fail on an arm64 host; disassembly, function listing,
  and annotation all still work. The x86-64 VM is unaffected.
