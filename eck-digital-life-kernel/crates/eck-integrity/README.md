# eck-integrity

Optional Rust verifier for ECK event exports. It reproduces the Python event
hash material in an independent implementation so integrity checks do not rely
only on the runtime that wrote the events.

Status in v0.1: **experimental and not required by the kernel**.

```bash
cargo test -p eck-integrity
cargo run -p eck-integrity -- events.jsonl
```

The main release is tested without Rust. CI should compile this crate on a
machine with a stable Rust toolchain.

