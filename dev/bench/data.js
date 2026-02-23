window.BENCHMARK_DATA = {
  "lastUpdate": 1771831359393,
  "repoUrl": "https://github.com/rohanvinaik/LintGate",
  "entries": {
    "LintGate Hot Path Benchmarks": [
      {
        "commit": {
          "author": {
            "email": "107649273+rohanvinaik@users.noreply.github.com",
            "name": "Rohan Vinaik",
            "username": "rohanvinaik"
          },
          "committer": {
            "email": "noreply@github.com",
            "name": "GitHub",
            "username": "web-flow"
          },
          "distinct": true,
          "id": "d58f8237d5b3f158be17e3a0f39dddea89213ce8",
          "message": "Merge pull request #14 from rohanvinaik/fix/badge-branch-protection\n\nFix badge workflows: push to unprotected badges branch",
          "timestamp": "2026-02-23T02:21:37-05:00",
          "tree_id": "66e67488e1cb004989cc5676fe2a4d4772d84714",
          "url": "https://github.com/rohanvinaik/LintGate/commit/d58f8237d5b3f158be17e3a0f39dddea89213ce8"
        },
        "date": 1771831358264,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/test_benchmarks.py::test_bench_lint_single_file",
            "value": 120.89702535846124,
            "unit": "iter/sec",
            "range": "stddev: 0.00029753188095744026",
            "extra": "mean: 8.271502107144382 msec\nrounds: 112"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_format_report",
            "value": 212187.8818903143,
            "unit": "iter/sec",
            "range": "stddev: 7.478168411913828e-7",
            "extra": "mean: 4.712804478235601 usec\nrounds: 54843"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_purity_analysis",
            "value": 5296.0018231477825,
            "unit": "iter/sec",
            "range": "stddev: 0.00021979496560238103",
            "extra": "mean: 188.82168726400295 usec\nrounds: 3706"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_property_classification",
            "value": 13484.720050215928,
            "unit": "iter/sec",
            "range": "stddev: 0.000016976105041574202",
            "extra": "mean: 74.15800967881326 usec\nrounds: 8782"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_manifest_build",
            "value": 1295.2081248699872,
            "unit": "iter/sec",
            "range": "stddev: 0.00045103515471656234",
            "extra": "mean: 772.0766885247727 usec\nrounds: 610"
          }
        ]
      }
    ]
  }
}