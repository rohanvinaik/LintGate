window.BENCHMARK_DATA = {
  "lastUpdate": 1771832125681,
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
      },
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
          "id": "ef1c0415636fed5f280ea49d626b83748bb0bf48",
          "message": "Merge pull request #15 from rohanvinaik/fix/badge-push-logic\n\nFix badge push: save SVGs before branch switch",
          "timestamp": "2026-02-23T02:23:56-05:00",
          "tree_id": "068dacbc8d55f7c6f94c8fe19f7fe6810121db26",
          "url": "https://github.com/rohanvinaik/LintGate/commit/ef1c0415636fed5f280ea49d626b83748bb0bf48"
        },
        "date": 1771831471551,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/test_benchmarks.py::test_bench_lint_single_file",
            "value": 127.60016713537365,
            "unit": "iter/sec",
            "range": "stddev: 0.00018110009532786085",
            "extra": "mean: 7.836980330433889 msec\nrounds: 115"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_format_report",
            "value": 212704.3617512009,
            "unit": "iter/sec",
            "range": "stddev: 7.491619324473386e-7",
            "extra": "mean: 4.701361042937589 usec\nrounds: 54157"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_purity_analysis",
            "value": 5392.994177713304,
            "unit": "iter/sec",
            "range": "stddev: 0.00017431251215902177",
            "extra": "mean: 185.42575182679175 usec\nrounds: 3832"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_property_classification",
            "value": 13838.202897775165,
            "unit": "iter/sec",
            "range": "stddev: 0.000016079618761667544",
            "extra": "mean: 72.26371859027843 usec\nrounds: 9392"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_manifest_build",
            "value": 1669.2445113135,
            "unit": "iter/sec",
            "range": "stddev: 0.00024349218489917263",
            "extra": "mean: 599.0734090915879 usec\nrounds: 572"
          }
        ]
      }
    ],
    "Performance Benchmarks": [
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
          "id": "4c10ac1e8b1e06e906bc539dcbdd840ca20e6550",
          "message": "Merge pull request #16 from rohanvinaik/fix/repo-agnostic-workflows\n\nMake workflows and badges repo-agnostic",
          "timestamp": "2026-02-23T02:34:47-05:00",
          "tree_id": "c362769c9d59419fc8382f33bce04b0c51ecae80",
          "url": "https://github.com/rohanvinaik/LintGate/commit/4c10ac1e8b1e06e906bc539dcbdd840ca20e6550"
        },
        "date": 1771832124416,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/test_benchmarks.py::test_bench_lint_single_file",
            "value": 120.95479832257755,
            "unit": "iter/sec",
            "range": "stddev: 0.00041309638989927487",
            "extra": "mean: 8.26755129906524 msec\nrounds: 107"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_format_report",
            "value": 236219.82739460038,
            "unit": "iter/sec",
            "range": "stddev: 5.57477694036435e-7",
            "extra": "mean: 4.233344893312112 usec\nrounds: 52480"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_purity_analysis",
            "value": 6296.690066021328,
            "unit": "iter/sec",
            "range": "stddev: 0.00018375496057163984",
            "extra": "mean: 158.8135972256718 usec\nrounds: 4181"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_property_classification",
            "value": 16075.650468329062,
            "unit": "iter/sec",
            "range": "stddev: 0.000014672041060423373",
            "extra": "mean: 62.20588099810447 usec\nrounds: 10420"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_manifest_build",
            "value": 1914.7452004725935,
            "unit": "iter/sec",
            "range": "stddev: 0.00005766324375808683",
            "extra": "mean: 522.2627009344021 usec\nrounds: 642"
          }
        ]
      }
    ]
  }
}