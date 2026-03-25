# Language Selection

## Candidates

The project involves these technical concerns:
- Periodic HTTP requests to a local modem (simple REST/form-based API)
- JSON parsing
- Time-series data persistence
- Optional: Prometheus metrics exposition
- Deployment: home server, NAS, or Raspberry Pi (low-resource)

The four languages considered were **Python**, **LabVIEW**, **C++**, and **Rust**.

---

## Evaluation

### Python

**Verdict: Recommended for PoC and a perfectly valid production choice.**

Python is the natural first choice for this type of project:

- `requests` / `httpx` make HTTP trivially easy.
- `prometheus_client` has first-class Prometheus exporter support.
- Virtually every piece of community code for ZTE modem scraping is already in Python.
- Short PoC turnaround — iterate quickly before committing to a full architecture.
- Runs on any platform including Raspberry Pi without extra tooling.

Downsides:
- Requires a Python runtime and a virtual environment on the target machine.
- Higher idle memory footprint than a compiled binary.
- No static typing by default (though `mypy` + type hints help).

### LabVIEW

**Verdict: Not suitable for this project.**

LabVIEW is a graphical dataflow programming environment from National Instruments primarily designed for:

- Laboratory instrument control (GPIB, serial, USB-TMC)
- Data acquisition hardware (DAQ cards)
- Test and measurement automation

It has HTTP client capability, but it is heavyweight, proprietary, requires an expensive licence, and is not a sensible fit for a home, open-source monitoring tool. LabVIEW would not be used here.

### C++

**Verdict: Not suitable as a primary language; no meaningful benefit over Rust or Python for this task.**

C++ could be used (e.g., with `libcurl` + `nlohmann/json`), but:

- No safety guarantees vs Rust.
- Far more boilerplate for HTTP + JSON than Python.
- Build system complexity (CMake, dependency management with conan/vcpkg).
- No ergonomic async story for this use case.

C++ would make sense if the tool needed to be integrated into an existing C++ codebase or embedded firmware, neither of which applies here.

### Rust

**Verdict: Recommended for the production implementation — and an excellent learning project.**

Rust is a compelling choice for this project, and the concerns about the learning curve are valid but manageable given the project's small scope:

**Why Rust fits well:**

- The `reqwest` crate (async HTTP) + `serde_json` + `tokio` runtime covers all needs.
- The `prometheus` crate exposes a Prometheus metrics endpoint trivially.
- Compiles to a **single static binary** — drop it on a Raspberry Pi or NAS, no runtime dependencies, no virtual environments.
- Very low memory footprint at idle (< 5 MB vs ~50–100 MB for a Python + runtime process).
- `cargo` simplifies dependency management and cross-compilation.
- This project is a great Rust learning vehicle: async I/O, error handling, serialisation — all core Rust patterns — without the complexity of lifetimes in a large codebase.

**Rust learning curve considerations for this project:**

- The async model (`tokio`) adds initial complexity but is well-documented.
- Error handling is explicit (no exceptions) — initially unfamiliar but it forces thinking about failure modes, which is valuable for a monitoring tool.
- Strong compiler error messages make the learning process guided rather than trial-and-error.

---

## Recommendation

| Phase | Language |
| --- | --- |
| Phase 0 — PoC | **Python** — fastest way to validate the modem API |
| Phase 1+ — Production | **Rust** — single binary, low resource use, learning opportunity |

If production Rust becomes too slow to develop in practice, Python with proper typing and packaging is entirely sufficient and should not be considered a retreat.

---

## Why Not Just Stay in Python?

Python is fine. The Rust recommendation is driven by:

1. Deployment simplicity (single binary, no Python on the Pi).
2. The expressed interest in learning Rust.
3. The project's small, well-defined scope makes it a low-risk Rust learning environment.

If deployment is on a machine that already has Python and keeping things in one language is preferred, stick with Python — the tool will work just as well.
