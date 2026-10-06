# In-process vs MCP: the weather forecast tool on both rungs of the trust ladder

Run of evarness-ai/iris-harness#82, locally; no hosted CI ran. This is comparison data for the
trust-ladder / isolation question, not a production requirement. It was first run against an
earlier harness build and re-run on 2026-10-06 against a wheel built from iris-harness `main`
at `2166717`; the results below are the re-run.

* Harness: `iris-harness` 0.1.0 wheel built from `main` at `2166717`, Python 3.12.13, macOS.
  Source file and line references in this document and in `GAPS.md` (for example
  `mcp_bridge.py:905`) were taken from the earlier build and were NOT re-checked line by line;
  the behaviours they describe are the ones the tests in `tests/` assert, and those tests pass
  or xfail as marked against `2166717`.
* What changed between the two builds, as far as this suite sees it: the kernel now writes one
  more PRE row (`pre_tool_use_ledger`, iris-harness#95) and one more POST row
  (`mcp_client_egress`) for a bridged call, so the row counts below are 10 PRE + 4 POST (they
  were 9 + 3); the stable `TurnAuditRow` now has a `caller` field (#124); and in-process tool
  rows now name the owning plugin and ReAct model calls name the model (#124, #131). All 14
  strict xfails still hold: no MCP gap listed in `GAPS.md` was fixed.
* In-process arm: `iris-plugin-weather-now` (`../iris-weather-plugin`, unmodified, imported
  from its checkout) mounted through `iris_harness.testing.plugin`, its HTTP on
  `httpx.MockTransport`. Its own graduation results are `../iris-trip-advisory/docs/GRADUATION.md`.
* MCP arm: `src/iris_weather_mcp` (official `mcp` 2.3.0 SDK, stdio), the same two tools
  (`weather_code_meaning`, `weather_forecast`), same upstream calls and same rendered text.
  Run in its own venv with **no `iris_harness`**; HTTP goes to a loopback stub through two
  environment variables. Registered the only way the harness has: an entry in
  `config/coding-agent/mcp-servers.yaml` read by `MCPBridge`, signed with the shipped
  `iris mcp keygen|sign|verify`, governed by the kernel the harness builds.
* Where each result comes from: `tests/`. Both arms run in the same suite so each row is
  measured side by side. `scripts/ci_local.sh` last result: see "What was run" at the end.
* The verdict wording follows the graduation suite: **PASS** = shown by a run here; **FAIL** =
  a run showed it does not hold; **NOT-EVIDENCED** = this run cannot show it. `xfail`
  entries in the suite are `strict`: each states a behaviour the criteria need and turns red
  the day the harness provides it. Gaps are in `GAPS.md` (GAP-M*).

## The finding that frames everything

`trust: mcp` is not a runnable trust level. The plugin host marks such a plugin `unsupported`
and never starts it (`runtime/plugin_host/loader.py:488-490`; GAP-M2). What exists is the
**bridge**, a coding-agent component that can launch a configured stdio server for one call
and fire the governance kernel around it. A bridged tool is not in the governed loop's tool
pool, so a scripted model asking for it gets nothing. To observe the loop at all, the suite
puts a test-only in-process adapter between the loop and the bridge. Every "through the
loop" cell below for MCP is that adapter, and the rows are double (the loop's own call and
the bridge's). Read the MCP column as "what the bridge does", not "what a finished
out-of-process host would do".

## The table

Properties are the P0/P1 graduation items (`GRADUATION.md`) that matter for the G1-G10
exit criteria, plus the isolation properties the MCP rung is supposed to add. Evidence names
a test in `tests/`.

Provenance of the in-process column: cells marked **(G)** are the graduation suite's
results (`GRADUATION.md`, run in `../iris-trip-advisory`), **not re-run here**. Unmarked
in-process cells were re-run in `tests/test_compare.py` against the unmodified plugin
(rows and caller on a scripted turn, its egress log lines, plain-tool masking, the output
classifier and the guard on its result, the raising-tool rows, and turn latency). Every MCP
cell was run in this repository.

| # | Property (graduation ref) | In-process (control arm) | MCP (bridge) |
|---|---|---|---|
| 1 | The plugin process holds no harness code (G1 purity / the point of #82) | **n/a by design.** The plugin imports `iris_harness.sdk` (stable tier; `check_stable_imports` clean (G)). | **PASS.** Four proofs: `pip list` of the server venv has no `iris-harness`; `import iris_harness` raises `ModuleNotFoundError`; the server's `sys.modules` has no `iris_*` module after importing it; an AST scan of `src/` finds no import (`test_isolation.py`; `ci_local.sh` repeats the first two on a fresh venv). The server's own dependency set is `mcp` and `httpx`. |
| 2 | Mounted by the plugin host (G2 SDK sufficiency) | **PASS (G)** (P0-2..P0-4). | **FAIL.** `trust: mcp` -> `unsupported: ... not available yet; not loaded`; `testing.plugin()` cannot describe it (`TypeError`). Reachable only through the coding agent's bridge config and non-stable imports. `test_registration.py` (GAP-M1, M2, M3). |
| 3 | Deterministic and network tools through the governed loop: PRE/POST_TOOL_USE rows, stamped caller (P0-2/3/4, G3) | **PASS.** 10 PRE + 4 POST rows, caller `model:system`, agent `system`, turn's run id; `chat` and `chat_stream`. | **FAIL as hosted; PASS via the adapter.** Without the adapter the loop cannot reach the server: no tool, no row, no process. Through the adapter: the loop's rows as in-process plus a second set, 10 PRE + 4 POST on route `mcp/weather/weather_forecast`, all `allow`, on both entry points, `audit_gaps() == []`. `test_in_the_loop_through_the_measurement_adapter`. |
| 4 | Undeclared tool refused (P0-5) | **PASS (G)** (manifest `tools:`). | **FAIL.** The bridge forwards any tool name: `delete_everything` passed 10 PRE and 4 POST `allow` rows and only the server's answer (`isError: true`) said it does not exist. No declared-tool list exists. (GAP-M9b) |
| 5a | Fault: the plugin raises or crashes (P0-6) | **PASS.** Re-run: turn answered; PRE **and POST** rows written; plugin `degraded` with `failure_count`. (G): reason in Health; siblings unaffected. | **PASS for containment, FAIL for accounting.** Crash mid-call and crash at start: caller gets `ValueError: unexpected EOF`, turn survives, child reaped. But PRE rows only (10), **no POST row**; the harness has no record of the server so no `degraded`, no failure count, no Health; the failure is charged to the adapter, and the server's stderr/exit code are lost (GAP-M8). `test_faults.py`. |
| 5b | Fault: the plugin hangs / exceeds its timeout | **NOT-EVIDENCED.** Not run here, and `GRADUATION.md` lists no hang test. | **PASS for a silent hang.** Cut at `timeout_seconds` (1 s -> 1.0-3.5 s), child killed and reaped; same PRE-only rows; inside a turn the synchronous call held the turn for the timeout. **FAIL for three ordinary shapes:** half a line then silence blocks past the timeout (7 s on 1 s); one banner line on stdout blocks (9 s on 3 s); a server logging >64 KB to stderr stalls. Each ended only by the test's watchdog. (GAP-M6, M9, M7b) |
| 5c | Fault: malformed or lying results | **n/a** (a Python return value). | **Mixed.** Non-JSON line -> contained `ValueError`. A string / missing / wrongly shaped `result` is returned **unvalidated** and passes POST. `isError: true` is a success (no error, no count, POST `allow`). A wrong message id ends the wait with a bare `KeyError`. A 3 MB result passes uncapped. A fast server timed out 4 of 10 runs although it answered (select/buffer race; deterministic reproduction with a batched write). (GAP-M7a, M9b, M10) |
| 6 | Caller policy: an unauthorized caller is denied, row names it (P1-3) | **PASS (G).** `plugin:<probe>` without `uses: tools` is `held` with a `caller_policy` deny row naming it; the grant runs only that tool; `tool-access.yaml` overrides. | **FAIL.** A bridged call carries **no caller**: `CallerPolicyHook` logs `caller_policy: model` and allows; no row has a caller; a plugin cannot even name the route. The one control is the coding agent's `(persona, server, tool)` allowlist: **PASS as a control** (no persona / unlisted persona / unlisted tool each denied by `mcp_allowlist`, deny rows with reasons; granted persona runs) but the model's loop has no persona, so turning it on makes the server unreachable from the loop; an ungoverned server is allowed with a `warn` row. The operator's `IRIS_GOVERNANCE_BLOCKED_TOOLS` denies the route name. `test_caller_policy.py` (GAP-M14). |
| 7 | Approval (P1-4) | **PASS (G)** for writes (queue, pinned args); a read needs none. | **FAIL for a read.** Governor `coding/mcp` is `requires_approval: true`: the read-only forecast is refused (`MCPApprovalRequired`) unless the caller passes `approval_granted=True`; the HTTP router passes `False` always, so over the shipped HTTP surface every call/list answers 403. Nothing is queued for the owner; the refusal is in the governor DB, the kernel's rows say `allow`. A write was not run (the tool is read-only). (GAP-M13) |
| 8 | Unsigned or tampered registration is refused | **n/a / NOT-EVIDENCED.** `party` is "declaration only; nothing enforces it yet" (`manifest.py:509`); a package is trusted by being installed. | **PASS for what is signed, FAIL for what is not.** With `enforce`/`deny`, refused **before the process starts** (no pid recorded): unsigned, forged signature, changed args / command / name, unknown signer, revoked key; shadow warns and runs; the CLI signs and verifies and `verify` exits non-zero on unsigned. **But:** with no signing file the bridge is disabled and an unsigned server runs silently; the signature covers the launch line, key names of env, and a hash of the *interpreter*, so editing the server's source or re-pointing an env value (the upstream URL) still verifies and runs; the `executor=` path skips the gate; the verdict is a log line, not a ledger row; the refusal is a `RuntimeError` raised after ten `allow` PRE rows. `test_signing.py` (GAP-M4a-d). |
| 9 | Removal closure (P0-7, G8) | **PASS (G).** Uninstall leaves only the plugin's files; a stale profile boots `failed` naming the plugin, Health red; consumers degrade. | **PASS for the files, weaker for the report.** Disabling or deleting the registration closes the door and starts nothing; no harness record ever existed to clean up; pip-uninstalling the package from a venv removed exactly its files (nothing else added or removed; its dependencies `mcp`, `httpx`, ... stay, as in any venv). **Not reported:** a registration left behind after the package is gone is invisible at boot (`plugins()` has no entry) and fails at the first call with `unexpected EOF` (the cause `No module named` is lost). The trust-store key outlives the server and the CLI has no remove/revoke. No process outlives a call (spawn per call). `test_closure.py`, `test_registration.py`. |
| 10 | Offline verification (P0-8, G9) | **PASS (G).** `no_network()` confines the plugin; its suite ran with 0 stray sockets. | **FAIL for confinement; PASS for the suite.** `no_network()` refused the parent's connect (`NetworkBlockedError`) while the child, started by the same harness, reached the loopback stub: the guard does not cross the process boundary. The suite is offline only because the server is told by environment to use a loopback stub, i.e. by the plugin's discipline. `test_the_harnesses_offline_guard_stops_the_parent_but_not_the_child`. |
| 11 | **Egress governance (in-process GAP-1)** | **FAIL** (the known GAP-1): no declaration, no mediation; re-run: the plugin's `log_egress` writes two `EGRESS service` lines to the harness's own egress logger (self-reported, but in the harness's log). | **FAIL, and less visible.** No field to declare hosts (`ServerSpec`/`MCPServerConfig` have none); `network_egress` says `not a network tool` on every row (`mcp_*` pattern vs `mcp/...` route); no sandbox/proxy; stdio egress is not logged by the bridge, so the harness's egress logger and the ledger are **silent** about the two requests the server made (its own log file is the only record, outside the harness); an undeclared host contact (a connection to a listener the harness never heard of) was neither stopped nor recorded; the child inherited the host's whole environment **including `IRIS_VAULT_MASTER_KEY`**; env values (the upstream URL) are unsigned. `test_egress.py` (GAP-M5, M5b, M4b). |
| 12 | Result masking (P1-6) | **PASS (G) for the capability path** (owner email -> `[owner:email#1]`, copy, literal absent from the ledger); a plain tool's result is **not** masked (re-run: the model saw the email). | **FAIL / not available.** A bridged result is not masked (`capability_redaction: not a capability`; the model saw the email through the adapter), same as a plain in-process tool. The masking path (a capability) has **no MCP form**: `api.provide` takes an in-process object (not run; stated from the API). The output classifier **cannot read** a bridged result (dict with a list of blocks): `tool returned no text`, so it cannot raise the run's data class, where in-process it classified the same text. The ledger holds digests only, no literal. `test_content.py` (GAP-M11). |
| 13 | External-content treatment (P1-6) | **NOT-EVIDENCED (as graduation): default FAIL.** `content: external` reaches the guard only when `IRIS_GOVERNANCE_PROMPT_GUARD=1`; offline the guard reports `guard unavailable` and passes the text through. | **FAIL, worse.** The bridge stamps `content: external` on every call (the server cannot say otherwise, nor can the operator), but with the guard on the result is never submitted: `no scannable text`. With or without weights the injected instruction passes verbatim. Default off, as in-process. (GAP-M11) |
| 14 | Explainability: who / what / which decision / which plugin (G10) | **PASS at `2166717` (G, GAP-4 fixed on main).** Rows carry (re-run) tool name, `caller` (`model:system`), decision and reason, session, turn run id, digests; the graduation suite's G10 tests, run against the same wheel, show the owning plugin on tool rows, the model on ReAct model calls and a `caller` on the stable audit row type (it was FAIL against the earlier build). | **FAIL.** Rows carry the route (`mcp/<config name>/<tool>`), digests, decisions; **no caller**, a constant `run_id` (`mcp-weather`) that is not the turn's, `agent_type: mcp`; the session id when called on the turn's thread; no server version, spec hash or signer. The signing verdict is not in the ledger. `test_attribution.py` (GAP-M12). |
| 15 | Cross-plugin contract (G6) | **PASS (G)** (typed `weather.forecast`, governed, masked copy, `uses`/`requires`, removal). | **NOT SUPPORTED / not run.** No registration kind lets an MCP server provide a capability. |
| 16 | Cost of a call | one `httpx` call in the harness process; whole scripted turn **0.16 s** (median of 4, one run; the first turn is ~1 s cold). | a process per call (spawn, `initialize`, call, terminate; no reuse): **+0.31 s** per call (median of 4); whole scripted turn **0.47 s** (median of 4, one run, same machine; first turn ~1.25 s cold). Synchronous: a hang holds the caller for the timeout. |
| 17 | Upgrade stability (G7) | NOT-EVIDENCED (one harness version). | NOT-EVIDENCED. Additionally the server pins `mcp>=2,<3`, whose v2 renamed `FastMCP` and changed unknown-tool behaviour. |
| 18 | Tier clarity (G5) | PASS for what ran (graduation). | Unchanged: the model calls are the loop's; not re-measured. |

## What the trust ladder buys, honestly

**What the MCP rung gives that the in-process rung cannot, and the run showed:**

1. **A real no-import boundary.** The plugin process has no harness code to call, no private
   attributes to reach, no registry to mutate. The in-process contract is "a contract, not a
   sandbox" (`plugin-contract.md:169`) and graduation G4 recorded a plugin calling another
   plugin's function directly with no row; none of that can happen across this boundary.
   (Shown: the isolation proofs. Not shown: adversarial behaviour of a hostile server beyond
   the exfiltration and environment probes.)
2. **OS-level containment of crashes and silent hangs.** A crash is an exception in the host,
   a silent hang is cut at its timeout and the child is killed and reaped, the host
   survives, and no process outlives a call.
3. **A cryptographic registration gate that refuses before spawn.** Ed25519 over the launch
   spec with a trust store, revocation, and shadow-then-enforce rollout; nothing comparable
   exists for an installed in-process package.
4. **Clean removal on the harness side.** There is no plugin record to unmount.

**What it costs, and the run showed:**

1. **It is a boundary without a host.** The things that make an in-process plugin
   *governed* (the stamped caller and `uses` grants, declared `effect` and `content`, the
   single PRE/POST pair on the turn's run id, the approval queue, capability masking,
   the content classifier and injection guard reading the result, attribution rows) are
   features of the plugin host and the loop. The bridge reaches the kernel but not the host,
   so on rows 2, 3, 4, 6, 7, 12 and 13 the MCP rung is weaker than, or unavailable
   against, the in-process rung today; on row 11 both rungs FAIL and MCP fails
   further (less recorded); on row 14 the in-process rung now passes and MCP fails (less attributed).
2. **The boundary does not close egress.** In-process GAP-1 says nothing mediates a
   plugin's own HTTP. Moving the HTTP to another process does not change that, and it removes
   the one thing the in-process arm still had (a `log_egress` line in the harness's log); the
   child also holds the vault master key. What the boundary adds is that *enforcement becomes
   possible at the OS level* (proxy, sandbox, namespace, env allow-list), which an in-process
   plugin can never have. Nothing builds it.
3. **The bridge is not production-grade transport.** Four ordinary server behaviours
   (fast reply, partial line, stray stdout line, stderr volume) hang or time out spuriously,
   an id mismatch raises `KeyError`, `isError` is success, and a dying server loses its
   explanation. These are bugs in a 160-line read loop, not properties of the trust level.
4. **It costs a process and 0.3 s per call**, synchronously.

**Reading for the trust-ladder / isolation decision.**

* Do not present `trust: mcp` as the answer to GAP-1 or to "third-party plugins can be
  trusted". Today it is the weaker rung on governance and equal on egress; its advantage is
  isolation of the *process*, and that advantage is only worth having once a host applies the
  same governance to it that the loop applies to a plugin.
* The cheapest path to making the upper rung real is the host in GAP-M2: mount the manifest's
  declared tools into `ToolService` over the bridge transport so `caller`, `effect`,
  `content`, approval and the single PRE/POST apply unchanged; normalise the result's text
  before PostToolUse; stamp identity (server, signer, spec hash) on the row; sign the code and
  the pinned env values; scrub the environment; and give `egress.hosts` one manifest field with
  two enforcers (a governed client in-process, a launch wrapper over MCP).
* The in-process rung is the right place to close GAP-1 first: a governed HTTP client is a
  small SDK change and fixes the plugin that exists; the MCP rung would need the OS-level
  mechanism on top of the same declaration.
* This comparison used one plugin, read-only, one transport (stdio), one harness version.
  A write tool, a long-lived server, an HTTP/SSE server and a hostile server were not run.

## What was run, and what was not

Run: everything cited above, in `tests/` (assertions and `xfail(strict)` as marked).
`scripts/ci_local.sh` builds two fresh venvs (server: no harness; host: the harness wheel),
proves the server venv has no harness, lints (ruff, black), scans the server source, and runs
the suite. Last result, against the wheel built from `main` at `2166717`: `83 passed, 14 xfailed` (all 14 `xfail`s are `strict`), `all steps passed`, on a fresh pair of venvs (about 2.5 min)

Not run: a live model, the real Open-Meteo (the server was pointed at a loopback stub; its
default URLs are the real ones and were never contacted), GitHub or any hosted CI, a write
tool, an HTTP or SSE MCP server, `iris mcp serve` (IRIS as an MCP server; the other
direction), a hostile server beyond the probes listed, a second harness version, a Windows
or Linux host, and the capability path over MCP (no way to register one). The latency
figures are one run of 4 on one machine and will vary. Stub, fixtures and the control arm
are test code in this repository and in `../iris-weather-plugin`, which was not modified.
