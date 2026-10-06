# SDK and bridge gaps found while running the weather tool over MCP

Run of evarness-ai/iris-harness#82, locally. Each entry says what was tried, what happened,
the evidence (a test in this repository, `xfail(strict)` where the test states the behaviour
the criteria need and the harness does not provide), and the core change proposed. None is
worked around in this repository: where a test needs a harness internal to get any
observation at all, that is itself recorded (GAP-M1, GAP-M2).

Built against iris-harness `main` at `cb94a0b` (the wheel in `../wheelhouse`), Python 3.12.13,
macOS, `mcp` 2.3.0 for the server. Source references are to that commit.

Severity: **A** = the property cannot be had at all; **B** = the bridge is wrong or unsafe on a
path an ordinary server takes; **C** = missing coverage or diagnosability.

---

## A. The host does not exist

### GAP-M1 (A, SDK): there is no supported way to register, call or test an MCP server

* Tried: the stable tier (`iris_harness.sdk`, `iris_harness.testing`) for anything MCP.
* Result: nothing. The bridge (`tools/mcp_bridge.py`), the signing package
  (`kernel/governance/mcp_signing/`), the governor and the kernel are not in
  `sdk/stable_tier.yaml`. `tests/test_closure.py::test_the_stable_tier_does_not_cover_what_this_suite_needs_from_the_harness`
  runs `check_stable_imports` over this repository's tests and pins the exact list that fell
  outside: `tools`, `tools.mcp_bridge`, `kernel.governor`, `kernel.governance.audit.log`,
  `kernel.governance.mcp_signing`, `foundation.paths`, `server.iris_api.mcp_server`. (The
  server process itself, `src/`, has none and imports nothing of the harness.)
* Registration is a file the coding agent owns: `config/coding-agent/mcp-servers.yaml`
  (`tools/mcp_bridge.py::load_mcp_bridge_config`). The shipped config has no such directory
  (`test_the_bridge_is_off_and_unconfigured_in_a_fresh_install`); the bridge is disabled
  unless the operator writes it, and the coding agent is out of release 1.
* Proposed: either make MCP servers a plugin kind the host mounts (GAP-M2) and publish the
  registration/signing helpers in the stable tier, or state that the bridge is not a plugin
  surface and remove the "`trust: mcp` is the real boundary" sentences from
  `plugin-contract.md:169` and `plugin-capabilities.md:359`.

### GAP-M2 (A, core): `trust: mcp` is a declaration with no host; a bridged tool cannot reach the governed loop

* Tried: a plugin whose manifest says `trust: mcp`, mounted from `$IRIS_HOME/plugins/` and a
  profile row (the harness's own discovery).
* Result: status `unsupported`, `load_error` "trust: mcp (out-of-process host) is not
  available yet; not loaded" (`runtime/plugin_host/loader.py:488-490`;
  `test_a_trust_mcp_plugin_is_not_loaded_by_the_plugin_host`). The tool never enters the tool
  pool (`ToolService.describe()` lacks it), so a scripted model that asks for it gets no
  call, no row and no server process (`test_no_server_without_an_adapter_is_reachable_from_the_loop`).
  The only shipped surface that reaches the bridge is the HTTP router, and that cannot
  succeed (GAP-M13).
* Consequence: every governed-loop observation in this repository goes through a
  **measurement adapter** (`tests/support.py::Adapter`), a test-only in-process plugin that
  forwards its tool to the bridge. It is a device to see what a turn records, not a design:
  the loop governs the adapter as an ordinary in-process tool and the bridge governs the MCP
  call a second time, so the rows are double.
* Proposed: an out-of-process host behind `trust: mcp`: launch or attach through the bridge's
  transport, register the manifest-declared tools into `ToolService` (so `effect`, `content`,
  caller stamp, `uses` grants, approval and the single PRE/POST pair apply exactly as for an
  in-process tool), and compile the manifest's declarations into the bridge's gate.

### GAP-M3 (B, testing helper): `iris_harness.testing.plugin()` cannot describe an out-of-process plugin

* Tried: `plugin(None, manifest={..., "trust": "mcp"})`.
* Result: `TypeError: in-process plugin 'weather-mcp' needs a setup callable`
  (`test_the_testing_harness_cannot_mount_a_trust_mcp_plugin`). `harness(home=...)` also
  refuses a non-empty home, so the only way to put a plugin directory in the profile for
  the probe was to point `IRIS_HOME` at a prepared directory through `env=`.
* Proposed: `plugin(manifest=..., server=<command>)` and a way to seed the home, so a
  plugin author's own CI can run a `trust: mcp` plugin under the governed harness.

---

## B. The bridge is unsafe or wrong on ordinary paths

These are in `tools/mcp_bridge.py::_run_stdio_exchange` and `_read_stdio_message`. All are
about a server the bridge starts over stdio; the real SDK server worked through all of them
except where noted.

### GAP-M7a (B): a fast server races the reader and times out although it answered

* Tried: a stdlib server that answers `initialize` and `tools/call` immediately.
* Result: with zero delay, **4 of 10** calls timed out (`timed out waiting for MCP stdio
  responses`) although both responses were written; with a 0.25 s delay in the server, 0 of
  5. The real SDK server (slower to answer) passed every run. Mechanism: the loop waits with
  `select.select([process.stdout], ...)` (line 905) and then `readline()` on a buffered
  reader (line 936). If both responses reach the pipe before the first `readline`, the
  buffer holds the second line while `select` watches only the kernel fd, and waits out the
  timeout. Deterministic reproduction: a server that writes both responses in one write
  (`test_two_responses_in_one_write_are_both_read`, xfail).
* Consequence: reliability depends on the server being slow. A server in a compiled language
  (fast `initialize`) is the likely victim.
* Proposed: read with `os.read` into the bridge's own buffer and parse lines from it, or
  use `asyncio` subprocess streams; test with a batched-response fixture.

### GAP-M6 (B): the timeout is not enforced on a partial line

* Tried: a server that writes half of a JSON line and stops.
* Result: the call blocked until the test's watchdog killed the child, 7 s on a 1 s
  `timeout_seconds` (`test_a_half_written_response_still_times_out`, xfail). The deadline is
  checked before `readline`, which then blocks.
* Proposed: non-blocking reads against the deadline, as above.

### GAP-M9 (B): one stray line on stdout hangs the call

* Tried: a server that prints a startup banner to stdout (a library's warning does the same).
* Result: the line is not `{`, so the bridge enters the `Content-Length` header branch, whose
  `readline` loop has no deadline and consumes the next JSON line as a "header": blocked 9 s
  on a 3 s timeout until the watchdog killed it (`test_a_banner_line_on_stdout_is_ignored`,
  xfail). The "legacy framed output" branch makes ordinary noise fatal.
* Proposed: treat a non-JSON line as noise (log it, continue) under the same deadline.

### GAP-M7b (B): stderr is a pipe nobody drains

* Tried: a server that writes 400 KB to stderr before replying.
* Result: the call timed out and the timeout message carried the stderr text; the server was
  blocked writing to a full pipe (`test_a_chatty_server_stderr_does_not_stall_the_call`,
  xfail). `stderr=subprocess.PIPE` is read only on the timeout path (`_read_process_stderr`).
* Proposed: drain stderr on a thread to a bounded log, or inherit it to a file.

### GAP-M10 (B): an unexpected message id ends the wait and raises a bare `KeyError`

* Tried: a server that replies with the wrong id (the same happens for a server-initiated
  request with an id, such as a `ping`).
* Result: the loop counts id-bearing messages (`len(responses) < len(expected_response_ids)`,
  line 898) instead of checking the ids, so a stray message stands in for the answer and the
  lookup raises `KeyError: 'iris-weather-tools-call'`, which no caller treats as a transport
  failure (`test_a_response_with_an_unexpected_id_ends_the_wait_with_a_keyerror`; the clean
  error is xfail).
* Proposed: wait until the expected ids are present; answer or ignore server requests.

### GAP-M8 (C): a server that dies loses its own explanation

* Tried: a server that writes a line to stderr and exits at start; and a venv from which the
  server package was uninstalled.
* Result: `ValueError: unexpected EOF while reading MCP stdio message` and nothing else. The
  stderr text and the exit code are read only when the call times out
  (`test_a_crash_at_start_says_why`, xfail; `test_a_stale_registration_...` shows
  `No module named iris_weather_mcp` is lost too).
* Proposed: on EOF, wait for the process and put its exit code and stderr tail in the error.

### GAP-M9b (B): `isError: true` is a success to the bridge and the ledger

* Tried: a tool result with `isError: true`; and a call to a tool the server does not have
  (the real SDK server answers that with `isError: true`, not a JSON-RPC error).
* Result: the bridge returns it, PostToolUse is `allow` on all hooks, nothing is raised or
  counted (`test_an_is_error_result_is_not_recorded_as_a_success`, xfail;
  `test_the_bridge_does_not_check_the_tool_against_what_the_server_declared` with the real
  server). In-process a failing tool is charged to its plugin and degrades it.
* Also: the bridge forwards any tool name the caller supplies and runs the nine PRE hooks on
  it as on a declared one; there is no declared-tool list to refuse against (in-process
  `tools:` does that, graduation P0-5).
* Proposed: surface `isError` as a failed call (a `deny`/error outcome on POST); check the
  name against `tools/list` or a manifest `tools:` block.

### GAP-M11 (B): the result's shape hides it from the content checks

* Tried: the injection guard (`IRIS_GOVERNANCE_PROMPT_GUARD=1`) and the output classifier on
  a bridged result carrying an injected instruction and the owner's email.
* Result: both read strings (`kernel/governance/plugins/prompt_guard.py:268`,
  `output_classifier.py:111`); an MCP result is `{"content": [{"type": "text", "text": ...}]}`
  with a LIST under `content`, so the guard reports `no scannable text` and the classifier
  `tool returned no text` (`test_with_the_guard_on_the_bridged_result_is_not_even_read_by_it`,
  `test_the_output_classifier_does_not_read_a_bridged_result`; the reach is xfail). The
  in-process arm reaches the guard (`guard unavailable` without weights) and classifies its
  text (`test_in_process_arm_*`). `content: external` is stamped on every MCP call
  (`mcp_bridge.py:553`), and the injection never gets scanned, with or without weights.
* Proposed: normalise a `tools/call` result to its text before PostToolUse (keep the original
  for the caller), then declare effect/content per tool (GAP-M2).

---

## C. Signing and registration

### GAP-M4a (B): the signature covers the launch line, not the code

* Tried: sign the server (`iris mcp keygen`, `iris mcp sign`), set `enforce`/`deny`, then edit
  the server's launcher in place.
* Result: `iris mcp verify` still exits 0, the bridge runs it, and the added line executes in
  the server process (`test_edited_server_source_still_verifies_and_runs_under_enforce`; the
  refusal is xfail). `ServerSpec` (`mcp_signing/spec.py`) hashes `command` only when it is a
  file; for `python script.py` or `python -m pkg` that is the interpreter. `build_server_spec`
  documents the scope ("a launcher like `npx <pkg>` is not a file").
* Proposed: hash the entry script/package (or require a self-contained executable or a pinned
  wheel hash) and say what is covered in `iris mcp sign`'s output.

### GAP-M4b (B): env values are not signed, so the egress target is not pinned

* Tried: re-point `WEATHER_MCP_FORECAST_URL` after signing.
* Result: signature still `verified`, traffic goes to the new host
  (`test_a_repointed_env_value_still_verifies_and_the_traffic_goes_there`; refusal is xfail).
  Only env KEY names are in the spec, deliberately (secrets are not to be signed), but
  non-secret configuration such as an upstream URL is the part an operator wants pinned.
* Proposed: sign declared non-secret env values (a `pinned_env` map), keep secret ones out.

### GAP-M4c (C): the `executor=` path skips the signature gate

* `invoke_external_tool(..., executor=fn)` returns before `_invoke_transport`
  (`mcp_bridge.py:421`), so `_enforce_signature` (line 617) never runs
  (`test_the_executor_path_runs_an_unverified_server_under_enforce`; xfail for the fix).
  Internal today (used by tests), a trap for the host GAP-M2 needs.

### GAP-M4d (C): signing is off unless a file exists, and its verdict is not in the ledger

* A bridge over a root with no `config/governance/mcp-signing.yaml` is disabled: an unsigned
  server runs with no warning (`test_with_no_signing_file_an_unsigned_server_runs_silently`).
  (The shipped file is `enabled: true, mode: shadow`; the API's bridge reads that root. Run
  here: the file-less case and shadow/enforce with files written by the operator.)
* A refusal is a `RuntimeError` (`MCPSignatureError`), not the `PermissionError` every other
  refusal is, so the HTTP route would answer 400 instead of 403, and it is raised after
  PRE_TOOL_USE wrote nine `allow` rows for a call that never ran (no `deny`, no POST).
* The verdict (`verified`, signer, key id) goes to a logger warning only; the ledger rows
  carry none of it (`test_the_signing_verdict_is_logged_but_never_written_to_the_ledger`).
* The CLI has `keygen`, `sign`, `verify` and no `revoke`, `remove` or `list`
  (`test_the_mcp_cli_has_no_remove_or_revoke_command`); revocation is a hand edit of the
  trust store, and removing a server leaves its key trusted.

---

## D. Authorization, approval, attribution

### GAP-M5 (A, core): the harness neither governs nor records a server's egress, and the boundary adds no control

This is the key comparison with in-process GAP-1 (see COMPARISON.md E1-E5).

* Tried: what the harness can see or refuse once the HTTP happens in another process.
* Result, each by test (`tests/test_egress.py`):
  * The registration cannot declare hosts (`ServerSpec` and `MCPServerConfig` have no egress
    field); the signed spec contains no host (and env values are unsigned: GAP-M4b).
  * The `network_egress` hook never fires for an MCP call: its tool list says `mcp_*`
    (`plugins/network_egress.py:61`), the route is `mcp/<server>/<tool>` (slash), and every
    row says `network_egress: not a network tool`. It would not help anyway: it inspects a
    URL in the call's arguments, and the server's upstream is not an argument.
  * The bridge starts the server with a plain `Popen` (no sandbox, proxy, namespace or host
    list) and logs egress only for the http/sse transports (`log_egress` is absent from
    `_run_stdio_exchange`), so the ledger and the harness's `iris.egress` logger name no host
    the server contacted; the in-process plugin's `log_egress` lines are at least in the
    harness's log.
  * `no_network()` (what makes the in-process suite offline) patches this interpreter: the
    child reached the loopback stub while the parent, in the same harness, got
    `NetworkBlockedError`.
  * A signed server that connects to an undeclared host is neither refused nor recorded
    (`test_a_server_that_contacts_an_undeclared_host_is_neither_stopped_nor_recorded`;
    the refusal/record is xfail).
* Proposed: the boundary makes enforcement possible at the OS level and nothing builds it. A
  per-server `egress: hosts` declaration (signed, GAP-M4b), a launch wrapper that applies it
  (proxy env at minimum; sandbox profile or network namespace where available) and a ledger
  row per contact reported by that wrapper. This is a different mechanism from the governed
  HTTP client proposed for in-process plugins (weather GAP-1), so the manifest field should
  be shared and the enforcement per trust level.

### GAP-M5b (A, security): the server inherits the host's whole environment, including the vault master key

* `environment = os.environ.copy()` (`mcp_bridge.py:877`) then the server's `env:`.
  `test_the_server_inherits_the_whole_host_environment_including_the_vault_key` shows the
  child holds `IRIS_VAULT_MASTER_KEY` (the harness's throwaway one; presence and a hash prefix
  only, never the value) and every `IRIS_*` setting; the clean version is xfail.
* Proposed: start from an allow-list (`PATH`, locale) plus the declared `env:` keys; vault
  handles through the credential broker, not the environment.

### GAP-M13 (A): every bridged call needs an approval nothing can give

* The shipped governor policy has `coding/mcp: requires_approval: true`
  (`config/governor/policy.yaml:19`), so a read-only forecast call is refused with
  `MCPApprovalRequired` unless the caller passes `approval_granted=True`
  (`test_the_governor_requires_approval_for_every_call_and_nothing_can_ask`). The HTTP router
  passes `False` always (`server/iris_api/mcp_server.py:108`), so over the only shipped
  surface `tools/list` and `tools/call` answer 403 and `session` returns `allowed: false`
  (`tests/test_http_surface.py`). Nothing is queued for the owner (contrast the in-process
  write approvals), and the refusal is in the governor's own DB, not the user-facing ledger
  (the kernel's nine rows for the call say `allow`).
* Proposed: take approval from the tool's declared effect (a read needs none; a write goes
  through the existing queue) instead of a boolean per route.

### GAP-M14 (A): a bridged call has no caller, so the plugin permission model does not reach it

* `MCPBridge._fire_governance_pre_tool` builds the context with no `metadata["caller"]`;
  `CallerPolicyHook` therefore logs `caller_policy: model` and allows, and no row carries a
  caller (`test_a_bridged_call_carries_no_caller_...`). `uses: tools` grants and
  `tool-access.yaml` apply to `plugin:<name>` callers of tools the host mounted; a bridged
  tool is not mounted, so a probe plugin cannot name it (`test_the_plugin_grant_model_does_not_reach_the_bridge`).
* The one control is `(persona, server, tool)` from the coding agent's `governance:` block
  (`MCPAllowlistHook`): default-allow with a warning when the server has no block, deny when
  the call has no persona. The model's loop has no persona, so turning the allowlist on makes
  the server unreachable from the loop
  (`test_the_loops_calls_have_no_persona_so_a_governed_server_is_unreachable_from_it`). The
  operator's `IRIS_GOVERNANCE_BLOCKED_TOOLS` does deny the route name.
* Proposed: stamp the real caller on the bridge's context (the host knows it), and key the
  allowlist by caller, not persona.

### GAP-M12 (C): a bridged call's rows cannot be joined to the turn or to a server identity

* Rows: `agent_type: mcp`, `run_id: mcp-weather` (constant), `tool_name: mcp/weather/<tool>`,
  digests, no caller, no signer, version or hash (`tests/test_attribution.py`). The session id
  stamp is present when the call is made on the turn's thread
  (`test_a_bridged_row_does_carry_the_session_when_called_inside_a_turn`); the run id join is
  xfail. `invoke_external_tool` accepts `persona` and `run_id` but no caller, and a host has
  no stable accessor for the turn's run id.
* Proposed: caller and run id from the host, and the server's identity (name, signer key id,
  spec hash) on the PRE row.

---

## E. Observations (not scored as defects)

* **Spawn per call.** Each call starts a process, performs `initialize` and ends it
  (`_invoke_stdio_transport`); measured +0.31 s per call (median of 4), 0.47 s vs 0.16 s for a
  whole scripted turn (median of 4, same machine, one run). No session reuse, so a server
  with state or a costly start pays it every call.
* **Synchronous.** The bridge call blocks the calling thread for up to `timeout_seconds`
  (default 10 s); a hang inside a turn held the turn for the timeout
  (`test_a_hang_inside_a_turn_blocks_the_turn_for_the_timeout`). `fire_sync` refuses to run
  inside an event loop, so the bridge is not callable from async code as written.
* **No result limits.** A 3 MB result passed the bridge unchanged
  (`test_a_three_megabyte_result_is_not_capped_by_the_bridge`); the loop's context budget is
  the only cap, downstream.
* **Capabilities have no MCP form.** `api.provide` takes a Python object; an MCP server cannot
  provide `weather.forecast` to a consumer plugin, so the cross-plugin contract (G6) has no
  out-of-process path. Not run (there is nothing to run); stated from the registration kinds.
* **Server SDK notes (mcp 2.3.0).** `FastMCP` is now `mcp.server.mcpserver.MCPServer`; an
  unknown tool is a successful result with `isError: true`; the server speaks
  newline-delimited JSON, which the bridge reads.
