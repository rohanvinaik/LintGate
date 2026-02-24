window.BENCHMARK_DATA = {
  "lastUpdate": 1771952411149,
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
          "id": "35805ea8c0e7ecdbaefd9714e8e95bd6b4b15600",
          "message": "Merge pull request #17 from rohanvinaik/fix/badge-svg-cleanup\n\nFix badge push: clean SVGs before branch switch",
          "timestamp": "2026-02-23T03:00:27-05:00",
          "tree_id": "72952ab5548bee53db26efd33d1ddcb104c36725",
          "url": "https://github.com/rohanvinaik/LintGate/commit/35805ea8c0e7ecdbaefd9714e8e95bd6b4b15600"
        },
        "date": 1771833663606,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/test_benchmarks.py::test_bench_lint_single_file",
            "value": 119.26059618711119,
            "unit": "iter/sec",
            "range": "stddev: 0.0008141741556840404",
            "extra": "mean: 8.384999169642526 msec\nrounds: 112"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_format_report",
            "value": 208961.54167276324,
            "unit": "iter/sec",
            "range": "stddev: 0.0000012764359691037223",
            "extra": "mean: 4.785569593308296 usec\nrounds: 41333"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_purity_analysis",
            "value": 5586.288455451057,
            "unit": "iter/sec",
            "range": "stddev: 0.00019840193009869296",
            "extra": "mean: 179.00973212799414 usec\nrounds: 3595"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_property_classification",
            "value": 14505.190823568366,
            "unit": "iter/sec",
            "range": "stddev: 0.00001545785345275301",
            "extra": "mean: 68.9408372604914 usec\nrounds: 7724"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_manifest_build",
            "value": 1272.5690777251612,
            "unit": "iter/sec",
            "range": "stddev: 0.002127258632632327",
            "extra": "mean: 785.8119590549816 usec\nrounds: 635"
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
          "id": "a39027b70b71f5674924d004b78d4d7f8e8a3b3c",
          "message": "Merge pull request #19 from rohanvinaik/feat/performance-engineering-small-model\n\nPerformance engineering, test effectiveness, CI hardening",
          "timestamp": "2026-02-23T09:59:27-05:00",
          "tree_id": "fd29929621dfacd5188f162a07f1c76259bb8e71",
          "url": "https://github.com/rohanvinaik/LintGate/commit/a39027b70b71f5674924d004b78d4d7f8e8a3b3c"
        },
        "date": 1771858803635,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/test_benchmarks.py::test_bench_lint_single_file",
            "value": 109.49486276450928,
            "unit": "iter/sec",
            "range": "stddev: 0.007139544286753861",
            "extra": "mean: 9.132848562500152 msec\nrounds: 112"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_format_report",
            "value": 213940.36192612472,
            "unit": "iter/sec",
            "range": "stddev: 8.20042756216047e-7",
            "extra": "mean: 4.674199814363724 usec\nrounds: 53865"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_purity_analysis",
            "value": 5554.440766598185,
            "unit": "iter/sec",
            "range": "stddev: 0.00018828918107657706",
            "extra": "mean: 180.0361264114172 usec\nrounds: 3631"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_property_classification",
            "value": 14566.484651250094,
            "unit": "iter/sec",
            "range": "stddev: 0.00001599400328172116",
            "extra": "mean: 68.65074339773393 usec\nrounds: 7611"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_manifest_build",
            "value": 1508.0535685456098,
            "unit": "iter/sec",
            "range": "stddev: 0.00035199776135677466",
            "extra": "mean: 663.1064180063679 usec\nrounds: 622"
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
          "id": "2d01fc8ac6fcc5f9b4242150927c4fd9001604e1",
          "message": "chore: unify strict gate contract and close split-brain pipeline (#41)\n\n* Centralize strict ship pipeline and gate contract parity\n\n* Stabilize qlty pre-push inputs for local secrets\n\n* Align script coverage with symbol gate and add ship_main tests\n\n* Make branch-protection drift strict in pre-push and best-effort in CI\n\n* Expand quality infra tests for branch-protection drift helpers",
          "timestamp": "2026-02-24T16:59:34Z",
          "tree_id": "782f5083f4bd0f3d44671a969b26ef951339a049",
          "url": "https://github.com/rohanvinaik/LintGate/commit/2d01fc8ac6fcc5f9b4242150927c4fd9001604e1"
        },
        "date": 1771952410294,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/test_benchmarks.py::test_bench_lint_single_file",
            "value": 123.15233477212567,
            "unit": "iter/sec",
            "range": "stddev: 0.00017821292007164495",
            "extra": "mean: 8.120024698275882 msec\nrounds: 116"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_format_report",
            "value": 218463.6125643104,
            "unit": "iter/sec",
            "range": "stddev: 8.034636044851968e-7",
            "extra": "mean: 4.577421330088205 usec\nrounds: 50839"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_purity_analysis",
            "value": 6115.1074427376425,
            "unit": "iter/sec",
            "range": "stddev: 0.00022152323734194772",
            "extra": "mean: 163.529424358293 usec\nrounds: 3662"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_property_classification",
            "value": 15227.937092080845,
            "unit": "iter/sec",
            "range": "stddev: 0.000015968575962558416",
            "extra": "mean: 65.66877666706682 usec\nrounds: 9703"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_manifest_build",
            "value": 1615.5802760456186,
            "unit": "iter/sec",
            "range": "stddev: 0.00004398930784755579",
            "extra": "mean: 618.9726470588349 usec\nrounds: 578"
          }
        ]
      }
    ]
  }
}