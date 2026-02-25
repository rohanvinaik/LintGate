import json
import subprocess
import sys

with open("cp_run_2.json") as f:
    data = json.load(f)

text = data["result"]["content"][0]["text"]
result = json.loads(text)
run_id = result["run_id"]

relevant_files = [
    "source_mapper.py",
    "types.py",
    "test_analyzer.py",
    "manifest.py",
    "test_channel.py",
    "test_effectiveness_tools.py",
]

for level in ["blocking", "warning"]:
    subprocess.run(
        [
            "./.venv/bin/python",
            "call_mcp.py",
            "controlplane_get_details",
            f'{{"run_id":"{run_id}","severity":"{level}"}}',
            "-o",
            f"details_{level}.json",
        ]
    )

    try:
        with open(f"details_{level}.json") as df:
            details = json.load(df)
            findings = json.loads(details["result"]["content"][0]["text"])
            for finding in findings:
                fname = finding.get("file", "")
                if any(name in fname for name in relevant_files):
                    print(
                        f"[{level.upper()}] {fname}:{finding.get('line')} - "
                        f"{finding.get('kind')}: {finding.get('message')}"
                    )
    except (
        FileNotFoundError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        print(f"[WARN] Could not parse details_{level}.json: {exc}", file=sys.stderr)
