use std::env;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::process::ExitCode;

use eck_integrity::{verify_chain, EventEnvelope};

fn main() -> ExitCode {
    let Some(path) = env::args().nth(1) else {
        eprintln!("usage: eck-integrity <events.jsonl>");
        return ExitCode::from(2);
    };
    let file = match File::open(&path) {
        Ok(file) => file,
        Err(error) => {
            eprintln!("cannot open {path}: {error}");
            return ExitCode::from(2);
        }
    };
    let mut events = Vec::new();
    for (index, line) in BufReader::new(file).lines().enumerate() {
        let line = match line {
            Ok(line) => line,
            Err(error) => {
                eprintln!("cannot read line {}: {error}", index + 1);
                return ExitCode::from(2);
            }
        };
        match serde_json::from_str::<EventEnvelope>(&line) {
            Ok(event) => events.push(event),
            Err(error) => {
                eprintln!("invalid JSON at line {}: {error}", index + 1);
                return ExitCode::from(2);
            }
        }
    }
    match verify_chain(&events) {
        Ok(()) => {
            println!("valid event chain: {} events", events.len());
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("invalid event chain at sequence {}: {}", error.sequence, error.reason);
            ExitCode::from(1)
        }
    }
}

