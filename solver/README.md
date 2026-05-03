# postflop-solver CLI Wrapper

`solver/postflop_cli` is a resident CLI wrapper for
[`postflop-solver`](https://github.com/b-inary/postflop-solver), which is
licensed under AGPL-v3. Python code starts this binary as a long-lived process
and communicates with it using newline-delimited JSON.

## Protocol

- stdin: one JSON request per line. Empty lines are ignored.
- stdout: one JSON response per request line.
- stderr: prints `ready` at startup, then error logs only.

The JSON request and response schemas follow `SPEC.md` section 5.8.

## Build Prerequisites

- Rust stable toolchain
- Local clone of `postflop-solver`

## Build Steps

1. Clone `postflop-solver` next to this wrapper path:

   ```powershell
   git clone https://github.com/b-inary/postflop-solver.git postflop-solver-local
   ```

2. Edit `postflop-solver-local/Cargo.toml` and set the default features to
   exclude `bincode`:

   ```toml
   default = ["rayon"]
   ```

3. Add the following crate-level allowance at the top of
   `postflop-solver-local/src/lib.rs`:

   ```rust
   #![allow(dangerous_implicit_autorefs, mismatched_lifetime_syntaxes)]
   ```

4. Build the wrapper:

   ```powershell
   cd solver/postflop_cli
   cargo build --release
   ```

5. Copy the built binary to the path used by Python:

   ```powershell
   Copy-Item target/release/postflop_cli.exe ../../bin/postflop_cli.exe
   ```

## Test Command Example

Send one JSON request line to the resident process:

```powershell
$json = '{"board":"QsJh2h","turn":null,"river":null' +
    ',"range_oop":"66+,A8s+,AJo+","range_ip":"66+,A8s+,AJo+"' +
    ',"starting_pot":200,"effective_stack":900' +
    ',"flop_bet_sizes_oop":"60%,a","flop_bet_sizes_ip":"60%,a"' +
    ',"flop_raise_sizes_oop":"2.5x","flop_raise_sizes_ip":"2.5x"' +
    ',"turn_bet_sizes_oop":"60%,a","turn_bet_sizes_ip":"60%,a"' +
    ',"turn_raise_sizes_oop":"2.5x","turn_raise_sizes_ip":"2.5x"' +
    ',"river_bet_sizes_oop":"60%,a","river_bet_sizes_ip":"60%,a"' +
    ',"river_raise_sizes_oop":"2.5x","river_raise_sizes_ip":"2.5x"' +
    ',"rake_rate":0.05,"rake_cap":3.0' +
    ',"add_allin_threshold":0.67,"force_allin_threshold":0.15' +
    ',"merging_threshold":0.1,"max_iterations":100' +
    ',"target_exploitability_pct":1.0,"timeout_ms":5000,"bunching":null}'
$json | .\solver\bin\postflop_cli.exe
```

The process prints `ready` to stderr when it starts, then emits one JSON
response line to stdout for each request.

## License

This wrapper links to `postflop-solver` and is licensed as
AGPL-3.0-or-later.
