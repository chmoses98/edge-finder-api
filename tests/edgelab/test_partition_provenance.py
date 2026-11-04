"""Independent partition-provenance regression fixtures."""
import gzip
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from scripts.edgelab.audit_partition_provenance import census
from lib.edgelab import storage

class PartitionProvenanceTests(unittest.TestCase):
    def test_cross_date_settlement_and_evaluation_are_visible_without_observation_partition(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(storage, "EDGELAB_ROOT", root):
                for entity, date, zipped in [
                    ("observations", "2026-08-01", True),
                    ("settlements", "2026-08-02", False),
                    ("model_evaluations", "2026-08-03", True),
                ]:
                    path = storage.partition_path(entity, date, compressed=zipped)
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    opener = gzip.open if zipped else open
                    with opener(path, "wt") as f:
                        f.write(json.dumps({"marketTicker": "TEST"}) + "\n")
                report = census("2026-08-01", "2026-08-03")
                self.assertEqual(report["entities"]["observations"]["recordCount"], 1)
                self.assertEqual(report["entities"]["settlements"]["datesWithoutObservationPartition"], {"2026-08-02": 1})
                self.assertEqual(report["entities"]["model_evaluations"]["datesWithoutObservationPartition"], {"2026-08-03": 1})

    def test_duplicate_suffix_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(storage, "EDGELAB_ROOT", root):
                for zipped in (False, True):
                    path = storage.partition_path("observations", "2026-08-01", compressed=zipped)
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    opener = gzip.open if zipped else open
                    with opener(path, "wt") as f:
                        f.write("{}\n")
                with self.assertRaisesRegex(ValueError, "ambiguous dual partition"):
                    census("2026-08-01", "2026-08-01")

if __name__ == "__main__":
    unittest.main()
