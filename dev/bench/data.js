window.BENCHMARK_DATA = {
  "lastUpdate": 1772065582137,
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
          "id": "a2871ff2548af5519094ca9458baca9b3e9bf26f",
          "message": "chore: ship codex/ship-20260224-182822 to main (#47)\n\n* feat: complete issue #43 mutation guard and provider schema hardening\n\n* feat: add ship preflight parity path and controlplane parity signal\n\n* test: cover ship preflight and parity helper branches for symbol gate\n\n* fix: unblock ship pipeline checks and coverage parity",
          "timestamp": "2026-02-24T14:43:41-05:00",
          "tree_id": "705bf750eb302072c666d5c5a19b3fefa8e49e92",
          "url": "https://github.com/rohanvinaik/LintGate/commit/a2871ff2548af5519094ca9458baca9b3e9bf26f"
        },
        "date": 1771962257228,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/test_benchmarks.py::test_bench_lint_single_file",
            "value": 128.40090365385183,
            "unit": "iter/sec",
            "range": "stddev: 0.00016717313276650705",
            "extra": "mean: 7.788107182608614 msec\nrounds: 115"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_format_report",
            "value": 212793.70590668265,
            "unit": "iter/sec",
            "range": "stddev: 7.527757892714768e-7",
            "extra": "mean: 4.6993871164522805 usec\nrounds: 55761"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_purity_analysis",
            "value": 5580.879896500342,
            "unit": "iter/sec",
            "range": "stddev: 0.00017087915660366791",
            "extra": "mean: 179.18321457286336 usec\nrounds: 3980"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_property_classification",
            "value": 14446.07301260543,
            "unit": "iter/sec",
            "range": "stddev: 0.00001566994613594073",
            "extra": "mean: 69.22296454734894 usec\nrounds: 8462"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_manifest_build",
            "value": 1446.6650606996368,
            "unit": "iter/sec",
            "range": "stddev: 0.0001452344413353655",
            "extra": "mean: 691.2450069931043 usec\nrounds: 572"
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
          "id": "af418fbfa58579172b4f6c3e05f296997ddcfd8e",
          "message": "ship: enable GitHub auto-merge mode in ship_main (#49)",
          "timestamp": "2026-02-24T21:43:20Z",
          "tree_id": "d4fe73eed5f49eff8b44052a33afc659fea3617d",
          "url": "https://github.com/rohanvinaik/LintGate/commit/af418fbfa58579172b4f6c3e05f296997ddcfd8e"
        },
        "date": 1771969433851,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/test_benchmarks.py::test_bench_lint_single_file",
            "value": 128.86003623859023,
            "unit": "iter/sec",
            "range": "stddev: 0.00021814533528691332",
            "extra": "mean: 7.760357898304906 msec\nrounds: 118"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_format_report",
            "value": 216962.67830235336,
            "unit": "iter/sec",
            "range": "stddev: 6.905554180011534e-7",
            "extra": "mean: 4.609087645048458 usec\nrounds: 53865"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_purity_analysis",
            "value": 5604.4881700501355,
            "unit": "iter/sec",
            "range": "stddev: 0.00022481447413669794",
            "extra": "mean: 178.42842551509113 usec\nrounds: 3786"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_property_classification",
            "value": 14711.440483892175,
            "unit": "iter/sec",
            "range": "stddev: 0.00001544776973600804",
            "extra": "mean: 67.97430891250373 usec\nrounds: 9582"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_manifest_build",
            "value": 1534.9333631614152,
            "unit": "iter/sec",
            "range": "stddev: 0.00017827123969486465",
            "extra": "mean: 651.4940804598558 usec\nrounds: 609"
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
          "id": "94abdd6e23a29fe79cd4e9a5cfc1fccf81d78a2c",
          "message": "fix: harden MCP command resolution for desktop startup (#50)\n\n* fix: resolve MCP server command paths for desktop agents\n\n* test: cover admin command-resolution failure branches",
          "timestamp": "2026-02-24T22:10:17Z",
          "tree_id": "d3f9ffaa292a570532c6c4305b6c8aec17241be7",
          "url": "https://github.com/rohanvinaik/LintGate/commit/94abdd6e23a29fe79cd4e9a5cfc1fccf81d78a2c"
        },
        "date": 1771971055024,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/test_benchmarks.py::test_bench_lint_single_file",
            "value": 129.04061805555764,
            "unit": "iter/sec",
            "range": "stddev: 0.00016132974568517673",
            "extra": "mean: 7.749497910568409 msec\nrounds: 123"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_format_report",
            "value": 210459.41862395356,
            "unit": "iter/sec",
            "range": "stddev: 7.452754395870433e-7",
            "extra": "mean: 4.751509847068371 usec\nrounds: 52757"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_purity_analysis",
            "value": 5632.426840057042,
            "unit": "iter/sec",
            "range": "stddev: 0.00019606685820653923",
            "extra": "mean: 177.54336246112211 usec\nrounds: 3868"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_property_classification",
            "value": 14606.82534622474,
            "unit": "iter/sec",
            "range": "stddev: 0.00001602646592438299",
            "extra": "mean: 68.4611458203311 usec\nrounds: 9594"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_manifest_build",
            "value": 1629.2282297713998,
            "unit": "iter/sec",
            "range": "stddev: 0.0000662412871854512",
            "extra": "mean: 613.7875478258267 usec\nrounds: 575"
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
          "id": "dd96d0b68456d83b792c080454d57ea3865a1930",
          "message": "merge: reconcile local and remote main (#51)\n\n* feat: complete issue #43 mutation guard and provider schema hardening\n\n* fix(ci): uniquify test artifacts across os matrix\n\n* fix(ci): align test check names with branch protection",
          "timestamp": "2026-02-24T22:46:28Z",
          "tree_id": "4be5b12107bea14eadfacfe06566727daacad164",
          "url": "https://github.com/rohanvinaik/LintGate/commit/dd96d0b68456d83b792c080454d57ea3865a1930"
        },
        "date": 1771973221097,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/test_benchmarks.py::test_bench_lint_single_file",
            "value": 126.67399991188522,
            "unit": "iter/sec",
            "range": "stddev: 0.00045230782972965287",
            "extra": "mean: 7.894279810344686 msec\nrounds: 116"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_format_report",
            "value": 197673.07829131107,
            "unit": "iter/sec",
            "range": "stddev: 0.000002352710557661271",
            "extra": "mean: 5.058857830535217 usec\nrounds: 54843"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_purity_analysis",
            "value": 5488.504133826516,
            "unit": "iter/sec",
            "range": "stddev: 0.0001978564648376255",
            "extra": "mean: 182.19900643544062 usec\nrounds: 3574"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_property_classification",
            "value": 14397.476225944913,
            "unit": "iter/sec",
            "range": "stddev: 0.000016199629683275454",
            "extra": "mean: 69.45661755620434 usec\nrounds: 9387"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_manifest_build",
            "value": 1545.8026547882323,
            "unit": "iter/sec",
            "range": "stddev: 0.00007047402575004671",
            "extra": "mean: 646.9131081528614 usec\nrounds: 601"
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
          "id": "5baaf29e664cf1337d5d12cfd194abf22ad6a7c4",
          "message": "Add Contributor Covenant Code of Conduct (#92)\n\nAdded Contributor Covenant Code of Conduct to promote a respectful and inclusive community.",
          "timestamp": "2026-02-24T23:18:36-05:00",
          "tree_id": "c31abfb494abb142f4d84862ff9c0fbf100555e4",
          "url": "https://github.com/rohanvinaik/LintGate/commit/5baaf29e664cf1337d5d12cfd194abf22ad6a7c4"
        },
        "date": 1771993152413,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/test_benchmarks.py::test_bench_lint_single_file",
            "value": 119.97365769187024,
            "unit": "iter/sec",
            "range": "stddev: 0.00022191135896926649",
            "extra": "mean: 8.335163061947412 msec\nrounds: 113"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_format_report",
            "value": 215699.9337832065,
            "unit": "iter/sec",
            "range": "stddev: 7.840392663547506e-7",
            "extra": "mean: 4.636070037022217 usec\nrounds: 54157"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_purity_analysis",
            "value": 5481.120114879551,
            "unit": "iter/sec",
            "range": "stddev: 0.00021830108787302253",
            "extra": "mean: 182.44446008130865 usec\nrounds: 3695"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_property_classification",
            "value": 14393.46368135551,
            "unit": "iter/sec",
            "range": "stddev: 0.00001634353149760454",
            "extra": "mean: 69.47598035734403 usec\nrounds: 9011"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_manifest_build",
            "value": 1543.6532813875965,
            "unit": "iter/sec",
            "range": "stddev: 0.00025082775183807705",
            "extra": "mean: 647.8138660134197 usec\nrounds: 612"
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
          "id": "c0e79449fd1794a47718a9fd6782d0192cc55a0a",
          "message": "chore: ship codex/ship-20260225-052955 to main (#95)\n\n* fix: resolve versioning regressions and modularize complex handlers\n\n- Fix KeyError in versioning helpers by ensuring requirement keys exist\n- Enable venv-aware detection in VersionChecker linter\n- Decompose monolithic _inspect_test_assertions_impl into focused helpers\n- Further refactor _compute_base_coherence to meet complexity targets\n- Clean up unused imports and style residue in refactored modules\n\n* chore: finalize mapper refactor and audit script annotation\n\n* chore: fix ship pre-push lint and security blockers\n\n* chore: resolve remaining schema mock naming gate\n\n* chore: whitelist sonar curl false-positive in gitleaks\n\n* fix: restore legacy helper exports after module splits\n\n* style: fix onboarding_tools import ordering\n\n* fix: re-export remaining onboarding helper functions\n\n* fix: include hypothesis in dev dependency extra\n\n* chore: clean local artifacts and relax PR symbol gate strictness\n\n* chore: remove local audit artifacts from repo root",
          "timestamp": "2026-02-25T06:20:20Z",
          "tree_id": "ca3644a767d45efedf81e8e871a175b6b7b39b05",
          "url": "https://github.com/rohanvinaik/LintGate/commit/c0e79449fd1794a47718a9fd6782d0192cc55a0a"
        },
        "date": 1772000477075,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/test_benchmarks.py::test_bench_lint_single_file",
            "value": 122.65852963466006,
            "unit": "iter/sec",
            "range": "stddev: 0.00023988844149975397",
            "extra": "mean: 8.152714719298464 msec\nrounds: 114"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_format_report",
            "value": 214710.0248123931,
            "unit": "iter/sec",
            "range": "stddev: 7.615795937532287e-7",
            "extra": "mean: 4.657444387488515 usec\nrounds: 54574"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_purity_analysis",
            "value": 5551.223060223511,
            "unit": "iter/sec",
            "range": "stddev: 0.000210961445763832",
            "extra": "mean: 180.14048240384287 usec\nrounds: 3694"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_property_classification",
            "value": 14497.678517999213,
            "unit": "iter/sec",
            "range": "stddev: 0.000015710264321324126",
            "extra": "mean: 68.97656054094979 usec\nrounds: 9316"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_manifest_build",
            "value": 1607.80539672997,
            "unit": "iter/sec",
            "range": "stddev: 0.00013162317321944599",
            "extra": "mean: 621.9658187700122 usec\nrounds: 618"
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
          "id": "489f914a1d891c43fbf94c7333e1fab8bfe66eee",
          "message": "ci: make symbol coverage gate advisory on main pushes (#98)",
          "timestamp": "2026-02-25T06:44:12Z",
          "tree_id": "db74257666e3889cfb5aeb62db444920762f7ad9",
          "url": "https://github.com/rohanvinaik/LintGate/commit/489f914a1d891c43fbf94c7333e1fab8bfe66eee"
        },
        "date": 1772001990978,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/test_benchmarks.py::test_bench_lint_single_file",
            "value": 120.16745539305069,
            "unit": "iter/sec",
            "range": "stddev: 0.0001909558464890345",
            "extra": "mean: 8.321720691589432 msec\nrounds: 107"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_format_report",
            "value": 213186.42290627974,
            "unit": "iter/sec",
            "range": "stddev: 8.114412525392585e-7",
            "extra": "mean: 4.690730236791938 usec\nrounds: 55115"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_purity_analysis",
            "value": 5524.313348635122,
            "unit": "iter/sec",
            "range": "stddev: 0.0002060747593272716",
            "extra": "mean: 181.01797216971184 usec\nrounds: 3701"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_property_classification",
            "value": 14208.789128183267,
            "unit": "iter/sec",
            "range": "stddev: 0.000019398615933389334",
            "extra": "mean: 70.37897395609106 usec\nrounds: 8908"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_manifest_build",
            "value": 1556.6705461321942,
            "unit": "iter/sec",
            "range": "stddev: 0.00012359437332882172",
            "extra": "mean: 642.3966859812859 usec\nrounds: 535"
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
          "id": "9e1c09dd2112e3bfd54f65cdc711e90684d6a0ea",
          "message": "chore: ship codex/ship-main-symbol-gate-advisory-20260225 to main (#103)\n\n* ci: make symbol coverage gate advisory on main pushes\n\n* feat: ship issue 102 mutation system and harden ship gates\n\n* chore: add symbol-gate waivers for issue 102 surfaces\n\n* fix: make mutation category mapping resilient without mutmut deps\n\n* chore: scope Sonar coverage during mutation rollout",
          "timestamp": "2026-02-25T16:35:39Z",
          "tree_id": "66b46fd41d4ae58009a24c0346706ee5c770ca52",
          "url": "https://github.com/rohanvinaik/LintGate/commit/9e1c09dd2112e3bfd54f65cdc711e90684d6a0ea"
        },
        "date": 1772037375445,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/test_benchmarks.py::test_bench_lint_single_file",
            "value": 113.92247092055364,
            "unit": "iter/sec",
            "range": "stddev: 0.0005628714420323648",
            "extra": "mean: 8.777899495327592 msec\nrounds: 107"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_format_report",
            "value": 213878.01432448707,
            "unit": "iter/sec",
            "range": "stddev: 7.056620318927901e-7",
            "extra": "mean: 4.675562390825457 usec\nrounds: 53237"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_purity_analysis",
            "value": 5498.2132330779905,
            "unit": "iter/sec",
            "range": "stddev: 0.00028152817365484123",
            "extra": "mean: 181.877267688322 usec\nrounds: 3067"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_property_classification",
            "value": 14364.891802636033,
            "unit": "iter/sec",
            "range": "stddev: 0.000016030301105002853",
            "extra": "mean: 69.61416860908726 usec\nrounds: 9181"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_manifest_build",
            "value": 1550.7941464222185,
            "unit": "iter/sec",
            "range": "stddev: 0.000170879706832875",
            "extra": "mean: 644.8309095743391 usec\nrounds: 564"
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
          "id": "2d9da7e61b489ea1df2725a21efc9587a6d6a82f",
          "message": "Merge pull request #171 from rohanvinaik/codex/ship-main-symbol-gate-advisory-20260225\n\nchore: ship codex/ship-main-symbol-gate-advisory-20260225 to main",
          "timestamp": "2026-02-25T19:25:45-05:00",
          "tree_id": "2a212c2ba2af2fb53ecd534901da9537e6378c82",
          "url": "https://github.com/rohanvinaik/LintGate/commit/2d9da7e61b489ea1df2725a21efc9587a6d6a82f"
        },
        "date": 1772065581374,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/test_benchmarks.py::test_bench_lint_single_file",
            "value": 121.71816758839823,
            "unit": "iter/sec",
            "range": "stddev: 0.0003058194978667286",
            "extra": "mean: 8.215700415254336 msec\nrounds: 118"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_format_report",
            "value": 213710.92675649357,
            "unit": "iter/sec",
            "range": "stddev: 8.437366921149981e-7",
            "extra": "mean: 4.679217928521828 usec\nrounds: 52040"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_purity_analysis",
            "value": 5434.599343534252,
            "unit": "iter/sec",
            "range": "stddev: 0.000267019390068938",
            "extra": "mean: 184.0062048345363 usec\nrounds: 3144"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_property_classification",
            "value": 14180.028377971763,
            "unit": "iter/sec",
            "range": "stddev: 0.000017102422109586772",
            "extra": "mean: 70.52172064433024 usec\nrounds: 9436"
          },
          {
            "name": "tests/test_benchmarks.py::test_bench_manifest_build",
            "value": 1414.8911063423986,
            "unit": "iter/sec",
            "range": "stddev: 0.00020446680483756318",
            "extra": "mean: 706.7681714284544 usec\nrounds: 560"
          }
        ]
      }
    ]
  }
}