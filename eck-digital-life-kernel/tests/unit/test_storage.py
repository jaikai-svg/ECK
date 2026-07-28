from __future__ import annotations

import sqlite3

from eck.domain.enums import KernelPhase


def test_event_chain_detects_tampering(application) -> None:
    store = application.store
    store.append_event("A", "kernel", {"value": 1})
    store.append_event("B", "kernel", {"value": 2})
    assert store.verify_event_chain() == (True, None)

    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE events SET payload_json = '{}' WHERE sequence = 1")
    valid, failed_sequence = store.verify_event_chain()
    assert not valid
    assert failed_sequence == 1


def test_unclean_state_is_detected_on_next_boot(application) -> None:
    store = application.store
    boot, recovered = store.begin_boot("identity")
    assert boot == 1
    assert not recovered
    store.update_kernel_state("identity", KernelPhase.RUNNING, clean_shutdown=False)

    boot, recovered = store.begin_boot("identity")
    assert boot == 2
    assert recovered


def test_event_export_preserves_hash_material(application) -> None:
    application.store.append_event("Example", "aggregate", {"value": 1})
    exported = application.store.export_events_jsonl()
    assert '"payload_json":"{\\"value\\":1}"' in exported
    assert '"event_hash":"' in exported
