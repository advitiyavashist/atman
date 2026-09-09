# ADR-001: Build Atman Core in Go

- **Status:** Accepted
- **Date:** 2026-09-09
- **Decision owner:** T-641
- **Scope:** Core architecture and migration contract. This ADR does not authorize a rewrite or a live-board cutover.

## Decision

Atman Core will be implemented in **Go**. The module will require Go 1.26 and
pin the contributor and release toolchain to Go 1.27.1. Release artifacts will
be native binaries for macOS, Linux, and Windows on amd64 and arm64 where the
platform supports it. One Go core will back the public library and the single
executable that exposes the existing `tickets` command surface, HTTP/SSE
service, hooks, and local runner supervision.

Python remains the behavioral oracle and rollback path until the differential
gates in [the migration plan](core-migration-and-benchmarks.md) pass. The
current board is never dual-written.

## Why this is the decision

Atman is a laptop-first coordinator. Its difficult work is filesystem
correctness across processes, process lifecycle management, a loopback HTTP/SSE
server, fast repeated hook invocations, and reliable distribution to machines
that may have none of our development stack. Go has the best combined fit:

- `go build` produces an executable, and the toolchain cross-compiles the
  targets Atman needs without requiring a language runtime on the user's
  machine.
- The standard library covers HTTP, streaming flushes, subprocesses,
  cancellation, JSON, embedded assets, structured logging, testing, and
  profiling. Atman can keep its production dependency set small.
- Goroutines and `context.Context` fit SSE clients, message watches, hook
  delivery, and supervised agent processes without making concurrency the
  dominant contributor learning cost.
- Go packages give us a small public library surface while `internal/` keeps
  storage, CLI, Git, and transport details changeable.
- The language and tooling favor explicit, consistent code. That matters more
  to this project than squeezing out the last few milliseconds or sharing a
  language with the React dashboard.

The current Go toolchain release is 1.27.1. The module's lower `go 1.26`
language line keeps one prior supported release usable; the `toolchain` line
and release workflow pin 1.27.1. Go documents both directives as versioned
toolchain requirements and continues the Go 1 compatibility promise.

## Measured Atman surface

This repository is no longer the small script described by its oldest header.
Measurements at `origin/main@3a19777` are:

| Surface | Measured value |
| --- | ---: |
| Root `tickets.py` | 12,628 lines / 585,129 bytes |
| Packaged `src/ticket_board/cli.py` | 4,455 lines / 180,888 bytes |
| Function names present in both CLI files | 172 |
| Top-level CLI commands registered by the root parser | 64 |
| Frozen HTTP paths in `docs/contracts/openapi.yaml` | 34 |
| Python package implementation | 49 files / 22,032 lines |
| Python tests | 212 files / 40,899 lines |
| React/TypeScript UI | 35 files / 5,526 lines |

`ticket_coordination.py` and `board_backup.py` are also byte-for-byte copies of
their namesakes under `src/ticket_board/`. Tests explicitly describe a
"two-copy rule" and exercise both entry points. This is already causing fixes
to require parallel edits and is the first architecture defect to remove.

The local timing sample below is directional evidence, not a synthetic claim
that `rg`, `gh`, or Node predict Atman's final performance. Each startup number
is 41 post-warmup invocations on an Apple Silicon Mac; p95 is the nearest-rank
sample. Resident set measurements are five initialized processes sampled after
150 ms. `rg` waited on stdin, `gh api` waited on stdin, and Node waited on a
timer. The Go and Rust toolchains were not installed, so the next ticket must
measure Atman-shaped stubs rather than extrapolate from these programs.
The machine-readable measurement record is
[`evidence/t641-local-baseline.json`](evidence/t641-local-baseline.json).

| Local executable | Startup p50 / p95 | Initialized RSS p50 | Executable |
| --- | ---: | ---: | ---: |
| Current Atman Python `--version` | 105.9 / 112.4 ms | — | Python + 572 KiB root script |
| Current Atman Python `ui` ready | 126.5 ms | 55.2 MiB | Python + source |
| Node 24.7 `--version` | 17.6 / 20.1 ms | 38.2 MiB | 77.8 MiB runtime |
| ripgrep 15.2 (Rust reference) | 2.6 / 3.2 ms | 3.3 MiB | 3.8 MiB |
| GitHub CLI 2.89 (Go reference) | 24.6 / 26.4 ms | 40.4 MiB | 35.8 MiB |

## Weighted comparison

Scores run from 1 (poor fit) to 5 (strong fit). Weights were fixed from the
ticket requirements before totaling. TypeScript is scored using Deno for
standalone distribution; the Node route scores lower because Node's
single-executable applications remain in active development and add a separate
bundling/signing path.

| Criterion | Weight | Rust | Go | TypeScript |
| --- | ---: | ---: | ---: | ---: |
| One-file distribution and cross-builds | 15 | 5 | 5 | 3 |
| Startup and idle footprint | 12 | 5 | 4 | 2 |
| Atomic filesystem work and portable locks | 15 | 5 | 4 | 4 |
| HTTP and SSE | 10 | 4 | 5 | 5 |
| Process supervision and cancellation | 12 | 4 | 5 | 5 |
| Cross-platform hooks | 8 | 4 | 5 | 4 |
| Embeddable library boundary | 8 | 5 | 4 | 3 |
| Schema and development tooling | 6 | 4 | 4 | 5 |
| Contributor accessibility | 8 | 3 | 5 | 5 |
| Incremental migration risk | 6 | 2 | 4 | 4 |
| **Weighted result** | **100** | **86.0** | **90.6** | **77.8** |

### Rust

Rust is the strongest option for a minimal runtime, memory control, and
compile-time safety. Current `std::fs::File` exposes locks backed by `flock` on
Unix and `LockFileEx` on Windows. Cargo workspaces and ripgrep show that a
native CLI can be split into coherent crates and distributed broadly.

It loses here on migration and contribution cost. Atman's core is dominated by
coordination state, JSON, HTTP, processes, and error-rich control flow rather
than data-plane computation. Rebuilding 64 commands and 34 HTTP paths while
introducing ownership and async-runtime decisions increases the chance that
the rewrite stalls before reaching parity. Rust remains appropriate for a
future high-throughput component if measurements identify one.

### Go

Go supplies the server and process primitives directly: `net/http` response
writers support flushing for SSE, and `os/exec.CommandContext` binds child
process cancellation to a context. Cross-platform advisory file locking is the
one core primitive missing from the standard library; the plan allows one
small, platform-specific dependency for it.

GitHub CLI is the closest useful repository blueprint. Its documented layout
separates binary entry points in `cmd/`, command implementations, public
packages, internal packages, docs, and scripts. We adopt that legibility, not
its product surface or dependency count. Temporal's Go repository also makes a
useful distinction between unit, integration, and functional tests; Atman adds
cross-process conformance and crash tests as a fourth tier.

### TypeScript

TypeScript would share types and contributors with the dashboard and is strong
for HTTP and process APIs. It fails the distribution/footprint priority. Deno's
official compiler produces self-contained cross-platform executables, but its
own simple example is roughly 70 MiB because it embeds a JavaScript runtime.
Node's single-executable path is still marked active development and, depending
on the supported Node line, constrains how scripts and assets are bundled.

Choosing Deno would also make core filesystem behavior depend on Deno-specific
APIs while the UI remains a Node/Vite project. That creates two TypeScript
runtimes rather than one coherent contributor environment.

## Consequences

- T-643 scaffolds Go packages and proves an Atman-shaped binary on the six
  target pairs. It does not port write behavior.
- T-642's language-neutral corpus, not Python implementation structure,
  defines parity.
- CLI, HTTP, and hook adapters contain parsing and presentation only. They call
  one domain implementation.
- The React UI stays TypeScript and is embedded into the release binary as
  static assets. Its generated API types come from the frozen OpenAPI document.
- We do not use Go's `plugin` facility. External harnesses use the documented
  subprocess/config protocol so they remain language- and platform-neutral.
- No CGo dependency enters the default build. A pure-Go SQLite driver, if the
  SQLite adapter needs one, requires a focused ADR with binary-size,
  cross-build, migration, and license evidence.

## Primary sources reviewed

- Go executable build and install: <https://go.dev/doc/tutorial/compile-install>
- Go cross-compilation targets: <https://go.dev/doc/install/source#environment>
- Go toolchain selection and pinning: <https://go.dev/doc/toolchain>
- Go 1.27.1 release record and support policy: <https://go.dev/doc/devel/release>
- Go subprocess cancellation: <https://pkg.go.dev/os/exec#CommandContext>
- Go HTTP streaming flush contract: <https://go.dev/src/net/http/server.go#L164>
- GitHub CLI project layout: <https://github.com/cli/cli/blob/trunk/docs/project-layout.md>
- GitHub CLI distribution and provenance: <https://github.com/cli/cli>
- Portable Go file-lock implementation: <https://github.com/gofrs/flock>
- Cobra command library: <https://github.com/spf13/cobra>
- Temporal contribution and test tiers: <https://github.com/temporalio/temporal/blob/main/CONTRIBUTING.md>
- Rust file lock behavior: <https://doc.rust-lang.org/std/fs/struct.File.html#method.try_lock>
- Cargo workspace boundaries: <https://doc.rust-lang.org/cargo/reference/workspaces.html>
- ripgrep build and distribution model: <https://github.com/BurntSushi/ripgrep>
- Cargo's warning about accidental public library APIs: <https://github.com/rust-lang/cargo>
- Deno standalone compilation and targets: <https://docs.deno.com/runtime/reference/cli/compile/>
- Deno executable size example: <https://docs.deno.com/examples/deno_compile/>
- Deno file-lock contract: <https://docs.deno.com/examples/file_locking/>
- Node single-executable status and constraints: <https://nodejs.org/download/release/v25.6.0/docs/api/single-executable-applications.html>
