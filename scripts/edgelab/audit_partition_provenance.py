#!/usr/bin/env python3
"""Read-only EdgeLab partition provenance census. No inferred historical forecasts."""
import argparse
import glob
import json
import os
from collections import Counter
from lib.edgelab import storage

ENTITIES = ("observations", "settlements", "model_evaluations")

def partition_dates(entity):
    paths = glob.glob(storage.partition_path(entity, "*")) + glob.glob(storage.partition_path(entity, "*", compressed=True))
    return sorted({os.path.basename(p).split(".jsonl")[0] for p in paths})

def census(start, end):
    dates = {entity: [d for d in partition_dates(entity) if start <= d <= end] for entity in ENTITIES}
    observation_dates = set(dates["observations"])
    result = {"schemaVersion": "1", "range": [start, end], "entities": {}, "observationDates": sorted(observation_dates)}
    for entity in ENTITIES:
        counts = {}
        for date in dates[entity]:
            # Fail closed if both suffixes coexist: read_partition would silently choose plain.
            plain = storage.partition_path(entity, date)
            gz = storage.partition_path(entity, date, compressed=True)
            if os.path.exists(plain) and os.path.exists(gz):
                raise ValueError(f"ambiguous dual partition: {entity}/{date}")
            counts[date] = sum(1 for _ in storage.read_partition(entity, date))
        result["entities"][entity] = {
            "partitionCount": len(counts), "recordCount": sum(counts.values()),
            "byPartitionDate": counts,
            "datesWithoutObservationPartition": {d: n for d, n in counts.items() if d not in observation_dates},
        }
    return result

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start-date", required=True)
    p.add_argument("--end-date", required=True)
    p.add_argument("--output")
    args = p.parse_args()
    if args.start_date > args.end_date:
        p.error("start-date must be <= end-date")
    report = census(args.start_date, args.end_date)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w") as f:
            f.write(payload)
    else:
        print(payload, end="")

if __name__ == "__main__":
    main()
