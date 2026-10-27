from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from hslegend.adapters.artifacts import ArtifactStoreError, FileArtifactStore
from hslegend.adapters.cards import CardCatalogError, JsonCardCatalog
from hslegend.adapters.config import ConfigCorruptionError, JsonConfigRepository
from hslegend.adapters.journal import JsonlRunJournal
from hslegend.application import (
    ArtifactStorePort,
    CardCatalogPort,
    ConfigRepositoryPort,
    ConfigSnapshot,
    ExecutionRecord,
    ExecutionStatus,
    JournalAppendResult,
    RunJournalPort,
)
from hslegend.domain import CardCapabilitySet, CardDefinition


SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(SRC_ROOT), existing) if part
    )
    return env


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def execution_record(index: int) -> ExecutionRecord:
    return ExecutionRecord(
        record_id=f"record-{index}",
        correlation_id="correlation-1",
        session_id="session-1",
        game_id="game-1",
        intent_id=f"intent-{index}",
        plan_id=f"plan-{index}",
        action_id=f"action-{index}",
        before_snapshot_id=f"before-{index}",
        after_snapshot_id=f"after-{index}",
        status=ExecutionStatus.EXECUTED_CONFIRMED,
        step_results=(f"step-{index}",),
        elapsed_seconds=0.25,
        evidence_ids=(f"evidence-{index}",),
    )


class JsonConfigRepositoryTests(unittest.TestCase):
    def test_missing_file_loads_empty_snapshot_without_creating_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            repository = JsonConfigRepository(path)

            snapshot = repository.load()

            self.assertIsInstance(snapshot, ConfigSnapshot)
            self.assertEqual((), snapshot.values)
            self.assertFalse(path.exists())

    def test_load_returns_typed_snapshot_and_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            write_json(
                path,
                {
                    "schema_version": 7,
                    "deck": {"id": "deck-1"},
                    "unknown": {"keep": True},
                },
            )

            snapshot = JsonConfigRepository(path).load()

            self.assertEqual(7, snapshot.schema_version)
            self.assertEqual({"id": "deck-1"}, dict(snapshot.get("deck")))
            self.assertIsNone(snapshot.get("schema_version"))
            self.assertEqual((("keep", True),), snapshot.get("unknown"))

    def test_corrupt_config_is_backed_up_before_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"deck":', encoding="utf-8")

            with self.assertRaises(ConfigCorruptionError):
                JsonConfigRepository(path).load()

            self.assertFalse(path.exists())
            backups = list(path.parent.glob("config.json.corrupt-*"))
            self.assertEqual(1, len(backups))
            self.assertEqual('{"deck":', backups[0].read_text(encoding="utf-8"))

    def test_save_deep_merges_and_preserves_unknown_keys_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            write_json(
                path,
                {
                    "schema_version": 1,
                    "deck": {"id": "deck-1", "options": {"theme": "dark"}},
                    "unknown": {"keep": True},
                },
            )
            repository = JsonConfigRepository(path)

            snapshot = repository.save({"deck": {"options": {"difficulty": "hard"}}})

            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("deck-1", raw["deck"]["id"])
            self.assertEqual(
                {"theme": "dark", "difficulty": "hard"},
                raw["deck"]["options"],
            )
            self.assertEqual({"keep": True}, raw["unknown"])
            self.assertEqual(snapshot, repository.load())
            self.assertEqual([], list(path.parent.glob("*.tmp")))

    def test_non_finite_save_fails_without_replacing_existing_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            original = '{"deck":' + ' {"id": "deck-1"}' + '}'
            path.write_text(original, encoding="utf-8")

            with self.assertRaises(ValueError):
                JsonConfigRepository(path).save({"bad": math.nan})

            self.assertEqual(original, path.read_text(encoding="utf-8"))

    def test_concurrent_updates_are_serialized_and_preserve_all_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            write_json(path, {"schema_version": 1})
            barrier = threading.Barrier(12)

            def update(index: int) -> None:
                barrier.wait()
                JsonConfigRepository(path).save(
                    {f"worker_{index}": {"index": index}}
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
                list(pool.map(update, range(12)))

            raw = json.loads(path.read_text(encoding="utf-8"))
            for index in range(12):
                self.assertEqual(index, raw[f"worker_{index}"]["index"])

    def test_load_quarantines_semantic_schema_failures(self) -> None:
        for schema_version in (-1, "1", True):
            with self.subTest(schema_version=schema_version):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "config.json"
                    path.write_text(
                        json.dumps({"schema_version": schema_version}),
                        encoding="utf-8",
                    )

                    with self.assertRaises(ConfigCorruptionError):
                        JsonConfigRepository(path).load()

                    self.assertFalse(path.exists())
                    self.assertEqual(
                        1,
                        len(list(path.parent.glob("config.json.corrupt-*"))),
                    )

    def test_save_validates_semantics_before_atomic_replace(self) -> None:
        original = '{"schema_version": 1, "deck": {"id": "deck-1"}}'
        for schema_version in (-1, "1", True):
            with self.subTest(schema_version=schema_version):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "config.json"
                    path.write_text(original, encoding="utf-8")

                    with self.assertRaises(ConfigCorruptionError):
                        JsonConfigRepository(path).save(
                            {"schema_version": schema_version}
                        )

                    self.assertEqual(original, path.read_text(encoding="utf-8"))
                    self.assertEqual([], list(path.parent.glob("*.tmp")))

    def test_concurrent_processes_preserve_all_updates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            write_json(path, {"schema_version": 1})
            script = (
                "import sys\n"
                "from hslegend.adapters.config import JsonConfigRepository\n"
                "index = int(sys.argv[2])\n"
                "JsonConfigRepository(sys.argv[1]).save("
                "{f'worker_{index}': {'index': index}})\n"
            )
            processes = [
                subprocess.Popen(
                    [sys.executable, "-c", script, str(path), str(index)],
                    cwd=SRC_ROOT.parent,
                    env=subprocess_env(),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                for index in range(8)
            ]
            for process in processes:
                stdout, stderr = process.communicate(timeout=30)
                self.assertEqual(
                    0,
                    process.returncode,
                    msg=f"stdout={stdout!r} stderr={stderr!r}",
                )

            raw = json.loads(path.read_text(encoding="utf-8"))
            for index in range(8):
                self.assertEqual(index, raw[f"worker_{index}"]["index"])


class JsonCardCatalogTests(unittest.TestCase):
    def test_maps_cards_to_definitions_and_normalizes_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cards.json"
            write_json(
                path,
                [
                    {
                        "id": "CARD_1",
                        "name": "Targeted Discover",
                        "cost": 3,
                        "type": "SPELL",
                        "text": "Discover a spell. Deal 2 damage to a minion.",
                        "targetingArrowText": "Choose a minion",
                        "mechanics": ["DISCOVER"],
                    },
                    {
                        "id": "CARD_2",
                        "name": "Flexible Location",
                        "cost": 2,
                        "type": "LOCATION",
                        "text": "Choose One - Trade or launch a Starship.",
                        "mechanics": ["CHOOSE_ONE", "TRADEABLE", "STARSHIP"],
                    },
                ],
            )

            catalog = JsonCardCatalog(path)
            first = catalog.get_card("CARD_1")
            second = catalog.get_card("CARD_2")

            self.assertEqual(
                CardDefinition(
                    card_id="CARD_1",
                    name="Targeted Discover",
                    mana_cost=3,
                    card_type="SPELL",
                    text="Discover a spell. Deal 2 damage to a minion.",
                    capabilities=CardCapabilitySet(
                        targetable=True,
                        board_target=True,
                        discover=True,
                    ),
                ),
                first,
            )
            self.assertEqual(
                CardCapabilitySet(
                    choose_one=True,
                    starship=True,
                    location=True,
                    trade=True,
                ),
                second.capabilities,
            )
            self.assertIs(first, catalog.get_card("CARD_1"))
            self.assertIsNone(catalog.get_card("missing"))
            self.assertRegex(catalog.version_hash, r"^[0-9a-f]{64}$")
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                catalog.version_hash,
            )

    def test_catalog_loads_once_per_process_and_never_writes_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cards.json"
            write_json(path, [{"id": "CARD_1", "name": "Alpha", "cost": 1}])
            original = path.read_bytes()
            original_stat = path.stat()

            first = JsonCardCatalog(path)
            card = first.get_card("CARD_1")
            self.assertEqual(original, path.read_bytes())
            path.write_text("not-json", encoding="utf-8")
            second = JsonCardCatalog(path)

            self.assertIs(card, second.get_card("CARD_1"))
            self.assertEqual(first.version_hash, second.version_hash)
            self.assertEqual("not-json", path.read_text(encoding="utf-8"))
            self.assertNotEqual(original_stat.st_mtime_ns, path.stat().st_mtime_ns)

    def test_concurrent_first_construction_reads_source_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cards.json"
            write_json(path, [{"id": "CARD_1", "name": "Alpha", "cost": 1}])
            start = threading.Barrier(16)
            read_barrier = threading.Barrier(2)
            read_lock = threading.Lock()
            read_count = 0
            original_read_bytes = Path.read_bytes

            def counted_read_bytes(card_path: Path) -> bytes:
                nonlocal read_count
                with read_lock:
                    read_count += 1
                try:
                    read_barrier.wait(timeout=0.25)
                except threading.BrokenBarrierError:
                    pass
                return original_read_bytes(card_path)

            def construct(_: int) -> CardDefinition:
                start.wait(timeout=5)
                card = JsonCardCatalog(path).get_card("CARD_1")
                self.assertIsNotNone(card)
                return card

            with patch.object(Path, "read_bytes", counted_read_bytes):
                with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                    cards = list(pool.map(construct, range(16)))

            self.assertEqual(1, read_count)
            self.assertTrue(all(card is cards[0] for card in cards))

    def test_invalid_or_duplicate_card_data_fails_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text("not-json", encoding="utf-8")
            with self.assertRaises(CardCatalogError):
                JsonCardCatalog(invalid).get_card("anything")

            duplicate = Path(directory) / "duplicate.json"
            write_json(duplicate, [{"id": "CARD_1"}, {"id": "CARD_1"}])
            with self.assertRaises(CardCatalogError):
                JsonCardCatalog(duplicate).get_card("CARD_1")

    def test_rejects_malformed_card_field_types_and_mechanics(self) -> None:
        malformed_cards = (
            {"id": 1},
            {"id": "CARD_1", "name": 1},
            {"id": "CARD_1", "name": None},
            {"id": "CARD_1", "cost": "3"},
            {"id": "CARD_1", "cost": True},
            {"id": "CARD_1", "mechanics": "DISCOVER"},
            {"id": "CARD_1", "mechanics": [1]},
            {"id": "CARD_1", "mechanics": {"DISCOVER": True}},
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, card in enumerate(malformed_cards):
                with self.subTest(card=card):
                    path = Path(directory) / f"invalid-{index}.json"
                    write_json(path, [card])
                    with self.assertRaises(CardCatalogError):
                        JsonCardCatalog(path)


class JsonlRunJournalTests(unittest.TestCase):
    def test_append_writes_one_valid_json_object_per_line_and_creates_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "journal.jsonl"
            journal = JsonlRunJournal(path)
            first = execution_record(1)
            second = execution_record(2)

            first_result = journal.append(first)
            second_result = journal.append(second)

            self.assertIsInstance(first_result, JournalAppendResult)
            self.assertEqual("record-1", first_result.record_id)
            self.assertTrue(first_result.durable)
            self.assertFalse(first_result.duplicate)
            self.assertEqual(second.evidence_ids, second_result.evidence_ids)
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(2, len(lines))
            self.assertEqual(first.to_primitive(), json.loads(lines[0]))
            self.assertEqual(second.to_primitive(), json.loads(lines[1]))

    def test_records_are_recoverable_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.jsonl"
            records = (execution_record(1), execution_record(2))
            journal = JsonlRunJournal(path)
            for record in records:
                journal.append(record)

            restored = JsonlRunJournal(path).read_records()

            self.assertEqual(records, restored)
            self.assertIs(ExecutionStatus.EXECUTED_CONFIRMED, restored[0].status)

    def test_concurrent_appends_keep_lines_whole(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.jsonl"
            barrier = threading.Barrier(16)

            def append(index: int) -> None:
                barrier.wait()
                JsonlRunJournal(path).append(execution_record(index))

            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                list(pool.map(append, range(16)))

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(16, len(lines))
            ids = {json.loads(line)["record_id"] for line in lines}
            self.assertEqual({f"record-{index}" for index in range(16)}, ids)
            self.assertEqual([], list(path.parent.glob("*.tmp")))

    def test_concurrent_processes_preserve_complete_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.jsonl"
            script = (
                "import sys\n"
                "from hslegend.adapters.journal import JsonlRunJournal\n"
                "from hslegend.application import ExecutionRecord, ExecutionStatus\n"
                "index = int(sys.argv[2])\n"
                "record = ExecutionRecord(\n"
                "    record_id=f'record-{index}', correlation_id='correlation-1',\n"
                "    session_id='session-1', game_id='game-1',\n"
                "    intent_id=f'intent-{index}', plan_id=f'plan-{index}',\n"
                "    action_id=f'action-{index}',\n"
                "    before_snapshot_id=f'before-{index}',\n"
                "    after_snapshot_id=f'after-{index}',\n"
                "    status=ExecutionStatus.EXECUTED_CONFIRMED,\n"
                "    evidence_ids=(f'evidence-{index}',),\n"
                ")\n"
                "JsonlRunJournal(sys.argv[1]).append(record)\n"
            )
            processes = [
                subprocess.Popen(
                    [sys.executable, "-c", script, str(path), str(index)],
                    cwd=SRC_ROOT.parent,
                    env=subprocess_env(),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                for index in range(8)
            ]
            for process in processes:
                stdout, stderr = process.communicate(timeout=30)
                self.assertEqual(
                    0,
                    process.returncode,
                    msg=f"stdout={stdout!r} stderr={stderr!r}",
                )

            records = JsonlRunJournal(path).read_records()
            self.assertEqual(8, len(records))
            self.assertEqual(
                {f"record-{index}" for index in range(8)},
                {record.record_id for record in records},
            )


class FileArtifactStoreTests(unittest.TestCase):
    def test_store_copies_a_constrained_source_to_a_durable_relative_ref(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "artifacts"
            source_root = base / "source"
            source_root.mkdir()
            source = source_root / "frame.png"
            source.write_bytes(b"image-bytes")
            store = FileArtifactStore(root, source_root=source_root)

            reference = store.store("frame.png", kind="evidence")

            self.assertEqual("evidence", Path(reference).parts[0])
            self.assertEqual(b"image-bytes", store.read_bytes(reference))
            self.assertTrue(store.resolve(reference).is_relative_to(root.resolve()))
            self.assertEqual(b"image-bytes", source.read_bytes())

    def test_save_is_atomic_and_returns_a_safe_relative_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "artifacts"
            store = FileArtifactStore(root)

            reference = store.save("screens/frame.txt", b"payload")

            self.assertEqual("screens/frame.txt", reference)
            self.assertEqual(b"payload", store.read_bytes(reference))
            self.assertEqual([], list(root.rglob("*.tmp")))

    def test_absolute_and_parent_traversal_references_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "artifacts"
            source_root = base / "source"
            source_root.mkdir()
            (source_root / "frame.txt").write_text("data", encoding="utf-8")
            store = FileArtifactStore(root, source_root=source_root)

            unsafe_refs = (
                str(root / "absolute.txt"),
                "../outside.txt",
                "screens/../../outside.txt",
            )
            for reference in unsafe_refs:
                with self.subTest(reference=reference):
                    with self.assertRaises(ArtifactStoreError):
                        store.save(reference, b"data")
                    with self.assertRaises(ArtifactStoreError):
                        store.resolve(reference)
                    with self.assertRaises(ArtifactStoreError):
                        store.read_bytes(reference)
            for source_ref in ("../frame.txt", str(source_root / "frame.txt")):
                with self.subTest(source_ref=source_ref):
                    with self.assertRaises(ArtifactStoreError):
                        store.store(source_ref)
            with self.assertRaises(ArtifactStoreError):
                store.store("frame.txt", kind="../outside")


class DataAdapterPortConformanceTests(unittest.TestCase):
    def test_adapters_satisfy_application_ports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cards = root / "cards.json"
            write_json(cards, [{"id": "CARD_1"}])
            adapters = (
                (ConfigRepositoryPort, JsonConfigRepository(root / "config.json")),
                (CardCatalogPort, JsonCardCatalog(cards)),
                (RunJournalPort, JsonlRunJournal(root / "journal.jsonl")),
                (ArtifactStorePort, FileArtifactStore(root / "artifacts")),
            )

            for port, adapter in adapters:
                with self.subTest(port=port.__name__):
                    self.assertIsInstance(adapter, port)


if __name__ == "__main__":
    unittest.main()
