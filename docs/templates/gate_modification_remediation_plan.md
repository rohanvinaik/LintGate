# Gate Modification Remediation Plan

Use this template in any PR that changes gate/CI infrastructure:
- `.github/workflows/**`
- `.githooks/pre-push`
- `scripts/ship_main.py`
- `gate_contract.yaml`
- `lintgate/channels/**`
- `lintgate/controlplane/**`

### Gate Modification Remediation Plan

#### Gate Graph Diff
- Local pre-push gates (before -> after):
- PR required checks (before -> after):
- Main-only checks (before -> after):
- Branch protection required checks (before -> after):
- Notes on check identity/name changes:

#### Dependency Impacts
- Components/workflows/tools touched:
- Contract/schema impacts:
- Known coupling and migration requirements:

#### Expected Check Outcomes
- Predicted post-change status for each required check:
- Preflight simulation command(s) and result:
- Residual risk and monitoring signal:

#### Rollback Strategy
- Immediate rollback command/path:
- Safe fallback behavior:
- Data/config cleanup required after rollback:

- [ ] I have evaluated the impact on ALL gate contracts
- [ ] I have ensured parity between local preflight and CI
- [ ] I have considered fallback behavior for legacy clients
