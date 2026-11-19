import json
import gzip
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from albion_data_extractor.ci_processor import (
    SERVERS_PRIORITY,
    get_data_root,
    get_git_push_target,
    get_max_files_per_run,
    get_max_worker_count,
    get_runtime_buffer_seconds,
    iter_sql_records,
    flush_item_batches,
    load_processed_files,
    normalize_processed_file_key,
    process_market_history_files,
    resolve_target_servers,
    restore_file_item_snapshots,
    should_handle_git_commit,
    should_stop_for_deadline,
)


class ProcessedFilesTests(unittest.TestCase):
    def test_market_workflow_uses_full_history_and_current_actions(self):
        workflow_path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "market-history-all.yml"
        workflow = workflow_path.read_text(encoding="utf-8")

        self.assertIn("uses: actions/checkout@v5", workflow)
        self.assertIn("uses: actions/setup-python@v6", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("git fetch origin main --prune --no-tags --force", workflow)
        self.assertNotIn("git fetch origin main --depth=1", workflow)

    def test_iter_sql_records_handles_one_insert_per_line(self):
        with TemporaryDirectory() as tmp_dir:
            sql_file = Path(tmp_dir) / "market_history_2024_01.sql"
            sql_file.write_text(
                "INSERT INTO `market_history` VALUES\n"
                "(1,12,603,'T1_MEAL_SOUP',3002,1,'2024-04-24 12:00:00.000000',6),\n"
                "(2,11890,2105849,'T3_FIBER',3008,1,'2024-04-24 12:00:00.000000',6);\n",
                encoding="utf-8",
            )

            records = list(iter_sql_records(sql_file))

            self.assertEqual(len(records), 2)
            self.assertEqual(records[0][3], "T1_MEAL_SOUP")
            self.assertEqual(records[1][3], "T3_FIBER")

    def test_iter_sql_records_handles_gzip_and_many_tuples_on_one_line(self):
        with TemporaryDirectory() as tmp_dir:
            sql_file = Path(tmp_dir) / "market_history_2024_01.sql.gz"
            with gzip.open(sql_file, "wt", encoding="utf-8") as f:
                f.write(
                    "INSERT INTO `market_history` VALUES "
                    "(1,12,603,'ITEM_A',3002,1,'2024-04-24 12:00:00.000000',6),"
                    "(2,13,604,'ITEM_B',3008,1,'2024-04-24 12:00:00.000000',6);\n"
                )

            records = list(iter_sql_records(sql_file))

            self.assertEqual(len(records), 2)
            self.assertEqual([record[3] for record in records], ["ITEM_A", "ITEM_B"])

    def test_flush_item_batches_merges_and_deduplicates_item_records(self):
        with TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir) / "formatted"
            output_dir.mkdir(parents=True)
            item_path = output_dir / "ITEM.json"
            item_path.write_text(
                json.dumps([{
                    "timestamp": "2024-01-01T00:00:00Z",
                    "city": "bridgewatch",
                    "quality": 1,
                    "server": "west",
                    "sellPrice": 10,
                }]),
                encoding="utf-8",
            )
            file_items = {
                "ITEM": [
                    {
                        "timestamp": "2024-01-01T00:00:00Z",
                        "city": "bridgewatch",
                        "quality": 1,
                        "server": "west",
                        "sellPrice": 10,
                    },
                    {
                        "timestamp": "2024-01-02T00:00:00Z",
                        "city": "bridgewatch",
                        "quality": 1,
                        "server": "west",
                        "sellPrice": 12,
                    },
                ]
            }

            with patch(
                "albion_data_extractor.ci_processor.should_stop_for_deadline",
                return_value=False,
            ):
                flushed = flush_item_batches(file_items, {"output": output_dir}, deadline_time=0)

            self.assertTrue(flushed)
            records = json.loads(item_path.read_text(encoding="utf-8"))
            self.assertEqual(len(records), 2)
            self.assertEqual(records[-1]["sellPrice"], 12)

    def test_load_processed_files_resets_stale_cache_when_no_json_output_exists(self):
        with TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir) / "formatted"
            output_dir.mkdir(parents=True, exist_ok=True)
            processed_file = output_dir / ".processed.txt"
            processed_file.write_text("market_history_2024_01.sql.gz\nmarket_history_2024_02.sql.gz\n", encoding="utf-8")

            processed = load_processed_files({"output": output_dir})

            self.assertEqual(processed, set())
            self.assertFalse(processed_file.exists())

    def test_load_processed_files_keeps_cache_when_json_output_exists(self):
        with TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir) / "formatted"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "ITEM.json").write_text("[]", encoding="utf-8")
            processed_file = output_dir / ".processed.txt"
            processed_file.write_text("market_history_2024_01.sql.gz\nmarket_history_2024_02.sql.gz\n", encoding="utf-8")

            processed = load_processed_files({"output": output_dir})

            self.assertEqual(processed, {"market_history_2024_01", "market_history_2024_02"})
            self.assertTrue(processed_file.exists())

    def test_load_processed_files_normalizes_legacy_sql_names(self):
        with TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir) / "formatted"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "ITEM.json").write_text("[]", encoding="utf-8")
            processed_file = output_dir / ".processed.txt"
            processed_file.write_text("market_history_2024_01.sql\nmarket_history_2024_02.sql.gz\n", encoding="utf-8")

            processed = load_processed_files({"output": output_dir})

            self.assertEqual(processed, {"market_history_2024_01", "market_history_2024_02"})

    def test_normalize_processed_file_key_handles_sql_and_gz_variants(self):
        self.assertEqual(normalize_processed_file_key("market_history_2024_01.sql.gz"), "market_history_2024_01")
        self.assertEqual(normalize_processed_file_key("market_history_2024_01.sql"), "market_history_2024_01")
        self.assertEqual(
            normalize_processed_file_key("/tmp/market_history_2024_01.sql.gz"),
            "market_history_2024_01",
        )

    def test_get_git_push_target_uses_github_ref_name(self):
        with patch.dict('os.environ', {'GITHUB_REF_NAME': 'release/2024'}, clear=True):
            self.assertEqual(get_git_push_target(), 'HEAD:release/2024')

    def test_should_handle_git_commit_disables_automatic_push_in_ci(self):
        with patch.dict('os.environ', {'GITHUB_ACTIONS': 'true'}, clear=True):
            self.assertFalse(should_handle_git_commit())
        with patch.dict('os.environ', {}, clear=True):
            self.assertTrue(should_handle_git_commit())

    def test_get_data_root_uses_override_when_set(self):
        with patch.dict('os.environ', {'ALBION_DATA_ROOT': 'data'}, clear=True):
            self.assertEqual(str(get_data_root()), str(Path(__file__).resolve().parents[1] / 'data'))

    def test_restore_file_item_snapshots_discards_current_file_changes(self):
        with TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir) / "formatted"
            output_dir.mkdir(parents=True, exist_ok=True)

            item_path = output_dir / "ITEM.json"
            original = [{"timestamp": "old", "city": "bridgewatch", "quality": 1, "server": "west"}]
            new_data = [{"timestamp": "new", "city": "bridgewatch", "quality": 1, "server": "west"}]
            item_path.write_text(json.dumps(new_data), encoding="utf-8")

            restore_file_item_snapshots({"output": output_dir}, {"ITEM": original})

            self.assertEqual(json.loads(item_path.read_text(encoding="utf-8")), original)

    def test_process_market_history_files_discards_current_file_when_deadline_hits(self):
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_root = root / "repo"
            history_dir = project_root / "albion_data_dumps" / "west" / "extracted"
            history_dir.mkdir(parents=True, exist_ok=True)
            output_dir = project_root / "albion_data_dumps" / "west" / "formatted"
            output_dir.mkdir(parents=True, exist_ok=True)

            sql_file = history_dir / "market_history_2024_01.sql"
            sql_file.write_text("INSERT INTO market_history VALUES (1, 1, 10, 'ITEM', 3003, 1, '2024-01-01 00:00:00', 1);\n", encoding="utf-8")
            original = [{"timestamp": "old", "city": "bridgewatch", "quality": 1, "server": "west"}]
            (output_dir / "ITEM.json").write_text(json.dumps(original), encoding="utf-8")

            with patch('albion_data_extractor.ci_processor.PROJECT_ROOT', project_root), \
                 patch('albion_data_extractor.ci_processor.iter_sql_records', return_value=[(1, 1, 10, 'ITEM', 3003, 1, '2024-01-01 00:00:00', 1)]), \
                 patch('albion_data_extractor.ci_processor.transform_record', return_value={
                     'timestamp': '2024-01-01T00:00:00Z',
                     'city': 'bridgewatch',
                     'quality': 1,
                     'server': 'west',
                     'sellPrice': 10,
                     'buyPrice': 10,
                     'quantity': 1,
                 }), \
                 patch('albion_data_extractor.ci_processor.should_stop_for_deadline', side_effect=[False, True]):
                processed_count = process_market_history_files('west', deadline_time=0)

            self.assertEqual(processed_count, 0)
            self.assertEqual(json.loads((output_dir / 'ITEM.json').read_text(encoding='utf-8')), original)
            self.assertFalse((output_dir / '.processed.txt').exists())

    def test_should_stop_for_deadline_uses_runtime_buffer_when_none(self):
        with patch('albion_data_extractor.ci_processor.get_runtime_buffer_seconds', return_value=30):
            with patch('albion_data_extractor.ci_processor.time.time', return_value=100):
                self.assertTrue(should_stop_for_deadline(130, reserve_seconds=None))
                self.assertFalse(should_stop_for_deadline(120, reserve_seconds=0))

    def test_get_runtime_buffer_seconds_defaults_to_zero(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(get_runtime_buffer_seconds(), 0)

    def test_get_max_files_per_run_defaults_to_unlimited(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(get_max_files_per_run(), 0)

    def test_get_max_worker_count_uses_auto_cpu_count_when_unset(self):
        with patch.dict('os.environ', {}, clear=True):
            with patch('albion_data_extractor.ci_processor.os.cpu_count', return_value=8):
                self.assertEqual(get_max_worker_count(), 8)

    def test_resolve_target_servers_uses_override_when_set(self):
        with patch.dict('os.environ', {'ALBION_TARGET_SERVER': 'east'}, clear=True):
            self.assertEqual(resolve_target_servers(), ['east'])

    def test_resolve_target_servers_defaults_to_all_servers(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(resolve_target_servers(), SERVERS_PRIORITY)


if __name__ == "__main__":
    unittest.main()
