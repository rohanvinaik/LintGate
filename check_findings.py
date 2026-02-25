import json
import subprocess

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
            for f in findings:
                fname = f.get("file", "")
                if any(name in fname for name in relevant_files):
                    print(
                        f"[{level.upper()}] {fname}:{f.get('line')} - {f.get('kind')}: {f.get('message')}"
                    )
    except Exception:
        pass
