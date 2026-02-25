# Mutation Calibration Report

**Repository Average Survival Rate:** 50.0%
**Functions Profiled:** 11

## Active Calibrated Thresholds
- **Warning (MUT001):** 50.0% (Functions exceeding this rate will trigger warnings)
- **Blocking (MUT002):** 80.0% (Functions exceeding this rate on exhaustive profile will block)

## Entanglement Statistics
Functions requiring decomposition (MUTCH007 candidate thresholds):
- **Highly Entangled Functions:** 0 (Survival >= 50% across 3+ operators)
- **Healthy Functions:** 11

## Calibration Mechanism
Thresholds are dynamically adjusted based on the `avg_survival` rate of the repository, ensuring that the enforcement floor naturally raises as overall test quality improves.