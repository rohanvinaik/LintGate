---
theory_scope: false
---

# Behavioral Baseline: iPhone Recovery Session

**Date:** 2026-02-17
**Agent:** Claude Opus 4.6 via Claude Code CLI
**Duration:** ~3.5 hours (continued from prior session that also ran ~3 hours)
**Task:** Bypass iCloud activation lock on iPhone 5c (A6, iOS 10.3.4)
**Outcome:** No successful bypass achieved after ~6.5 total hours across two sessions

---

## Purpose of This Document

This is a thorough post-mortem of agent behavior during a multi-hour hardware
hacking session. It documents every significant action taken, every failure
encountered, every constraint discovered, and critically — every point where
the agent should have paused to reason but instead immediately tried another
approach.

This document serves as baseline behavioral data for developing LintGate's
behavioral supervision channel. The anti-patterns documented here are not
code quality issues — they are *reasoning quality* issues that manifest as
wasted tool calls, cycling through failed approaches, and serial constraint
discovery through trial-and-error instead of upfront analysis.

---

## 1. Session Context

### The Device
- iPhone 5c (iPhone5,3, n48ap)
- A6 chip (s5l8950x) — 32-bit, no Secure Enclave
- Activation-locked (iCloud lock from previous owner)
- Purchased at Goodwill by the user (a biochemist, not a security researcher)

### The Goal
Remove or bypass the iCloud activation lock so the phone is usable.

### Tools Available
- `iPwnder32` — checkm8 bootrom exploit for 32-bit devices
- `Legacy-iOS-Kit` — community restore/downgrade toolkit (bash script, ~11,000 lines)
- `idevicerestore` — Apple device restore tool (bundled with Legacy-iOS-Kit)
- `irecovery` — low-level USB communication with devices in DFU/recovery mode
- `hfsplus` — HFS+ filesystem manipulation tool
- `dmg` — Apple DMG image build/extract tool

### Known Constraints at Session Start (from prior session)
1. Device always enters iBSS DFU (SRTG: iBoot-1145.3), not clean ROM DFU
2. iPwnder32 --noibss exploits SecureROM but stock iBSS still has signature checking
3. Patched iBEC is rejected by stock iBSS (SSH ramdisk can't boot)
4. Setup.app deletion alone causes boot loop (tried in prior session)
5. Legacy-iOS-Kit's `ipsw_prepare_32bit()` returns early for iOS 10.x
6. Legacy-iOS-Kit's `ipsw_prepare()` skips custom IPSW for "Latest iOS" on A5/A6
7. idevicerestore CAN successfully flash stock IPSW (proven in prior session)

---

## 2. Complete Action Timeline

### Phase 1: Reconnaissance and Source Code Analysis (~40 min)

**Actions taken:**
```
Read  — /tmp/Legacy-iOS-Kit/restore.sh (multiple sections, ~500 lines read)
Read  — /tmp/Legacy-iOS-Kit/restore.sh lines 4480-4500 (bypass code)
Read  — /tmp/Legacy-iOS-Kit/restore.sh lines 3800-3830 (ipsw_prepare_32bit)
Read  — /tmp/Legacy-iOS-Kit/restore.sh lines 6080-6150 (ipsw_prepare routing)
Bash  — irecovery -q (check device state)
Bash  — kill process (Legacy-iOS-Kit from prior session)
Grep  — multiple searches through restore.sh
```

**Assessment:** This was the RIGHT approach. Reading source code to understand
the system before acting. Discovered constraints #5 and #6 (Legacy-iOS-Kit
doesn't create custom IPSWs for iOS 10 on A5/A6). This phase produced genuine
understanding.

**Behavioral score:** GOOD — research before action, constraint enumeration

---

### Phase 2: IPSW Download (~15 min)

**Actions taken:**
```
Bash  — curl ipsw.me API to check signing status
Bash  — aria2c download of iOS 10.3.3 IPSW (1.9GB)
```

**Assessment:** Reasonable. Downloaded the IPSW we'd need. However, this turned
out to be the wrong version — the device needed 10.3.4, and Legacy-iOS-Kit
later auto-downloaded the correct one. Mild waste.

**Behavioral score:** OK — useful action, minor version mismatch

---

### Phase 3: First Restore Attempt via Legacy-iOS-Kit with --skip-ibss (~20 min)

**Actions taken:**
```
Write — /tmp/run-restore-skipibss.sh (helper script)
Bash  — open -a Terminal (launched interactive restore)
[User navigated menus: Restore/Downgrade → Latest iOS → Start Restore]
[User pasted output showing failure]
```

**Failure:** `Unable to send iBSS component: Unable to upload data to device`

**Root cause:** The `--skip-ibss` flag skipped the iBSS-to-iBEC transition step,
but idevicerestore still tried to send its own iBSS which conflicted with the
device's already-loaded iBSS.

**Constraint discovered:** idevicerestore can't send iBSS to a device already
in iBSS DFU mode.

**Was this predictable?** YES. I already knew (constraint #1) that the device
was in iBSS DFU mode, not clean ROM DFU. I should have reasoned that
idevicerestore's iBSS upload would conflict with the existing iBSS.

**What I did next:** Immediately tried without --skip-ibss (see Phase 4).

**What I should have done:** Paused to ask: "Why did idevicerestore fail to
upload iBSS? Is this a conflict with the already-loaded iBSS? What does
Legacy-iOS-Kit do differently to handle this?" Reading the Legacy-iOS-Kit
source for the non-skip-ibss code path BEFORE trying would have been more
efficient.

**Behavioral score:** PREMATURE ACTION — acted without modeling the DFU state

---

### Phase 4: Second Restore Attempt via Legacy-iOS-Kit without --skip-ibss (~25 min)

**Actions taken:**
```
Write — /tmp/run-restore.sh (new helper script)
Bash  — open -a Terminal (launched interactive restore)
[User navigated menus: Restore/Downgrade → Latest iOS → Start Restore]
[User pasted output showing SUCCESS]
```

**Result:** Stock restore SUCCEEDED. Device restored to iOS 10.3.4.
But still had activation lock (expected — this was a stock restore with
no bypass modifications).

**Assessment:** This worked because Legacy-iOS-Kit used `primepwn` to send
a patched iBSS first, transitioning the device properly before handing off
to idevicerestore.

**Key learning:** Legacy-iOS-Kit's primepwn handles the iBSS DFU → recovery
transition. idevicerestore alone cannot.

**Behavioral score:** OK — reasonable iteration from Phase 3 failure

---

### Phase 5: Attempt to Boot SSH Ramdisk After Stock Restore (~45 min)

This is where behavioral quality degraded significantly. Multiple sub-attempts
with device state issues, port problems, and tool crashes.

**Actions taken:**
```
Bash  — irecovery -q (device unresponsive — black screen)
[User force-restarted device]
[User entered recovery mode]
Bash  — irecovery -q (device not detected on USB)
[User tried unplugging/replugging — nothing]
[User switched to different USB port — device appeared]
Bash  — irecovery -q (device in Recovery mode on new port)
[User entered DFU mode from recovery]
Bash  — iPwnder32 -p (CRASH — exit 133, SIGBUS on new port)
Bash  — iPwnder32 --help (works — binary is fine)
Bash  — iPwnder32 -p (same crash)
Bash  — Legacy-iOS-Kit ipwnder (failed)
Bash  — Legacy-iOS-Kit a6meowing (failed: "checkm8_a6()")
[User switched back to original port]
Bash  — iPwnder32 --noibss (SUCCESS — device pwned)
Write — /tmp/run-sshrd.sh (SSH ramdisk script)
Bash  — open -a Terminal (Legacy-iOS-Kit --sshrd --skip-ibss)
[Failed — sent iBEC but device didn't transition to recovery]
Bash  — open -a Terminal (Legacy-iOS-Kit --sshrd without --skip-ibss)
[Failed — primepwn "Transfer failed at index 0: expected 2048, got -11"]
Bash  — iPwnder32 --noibss (re-pwned device)
Bash  — irecovery -f iBEC.patched (sent iBEC directly)
Bash  — irecovery -q (device still in DFU — iBEC rejected by stock iBSS)
```

**Failures:** Everything failed. SSH ramdisk cannot boot because:
- iBSS DFU → patched iBEC rejected by stock iBSS signature checking
- primepwn transfer failure (USB issue)
- Direct iBEC send via irecovery: accepted by USB but rejected by iBSS

**Constraints discovered:**
- iPwnder32 crashes on certain USB ports (port-specific, not binary issue)
- primepwn iBSS transfer can fail with USB errors
- Stock iBSS signature verification blocks ALL patched iBEC payloads
- SSH ramdisk approach is fundamentally blocked on this device in this state

**Was this predictable?** PARTIALLY. Constraint #2 and #3 from session start
already established that stock iBSS rejects patched iBEC. The SSH ramdisk
approach was always going to fail for this reason. I attempted it anyway
through multiple variants.

**What I did next:** Pivoted to custom IPSW approach (Phase 6).

**What I should have done:** Before ANY SSH ramdisk attempt, enumerated the
full chain of trust: SecureROM → iBSS → iBEC → kernel → ramdisk. Then asked:
"Which links in this chain can we control?" Answer: only SecureROM (via
exploit). iBSS is stock from NAND with its own verification. Therefore
patched iBEC is dead on arrival. This entire 45-minute phase was predictably
doomed from constraints I already had.

**Behavioral score:** CYCLING + FAILURE AMNESIA — 6+ attempts at a fundamentally
blocked approach, not incorporating known constraints #2 and #3

---

### Phase 6: Custom IPSW Creation (~30 min)

**Actions taken:**
```
Bash  — unzip IPSW to extract rootfs DMG (058-74968-065.dmg)
Bash  — hfsplus rootfs.dmg ls / (FAIL: "Cannot open image-file")
Bash  — dmg extract rootfs.dmg rootfs.dec (SUCCESS — converts to raw image)
Bash  — hfsplus rootfs.dec ls /Applications/ (SUCCESS — found Setup.app)
Bash  — hfsplus rootfs.dec ls /var/root/ (FAIL — /var is symlink)
Bash  — hfsplus rootfs.dec ls /private/var/ (SUCCESS)
Bash  — hfsplus rootfs.dec ls /private/var/root/ (found Library/)
Bash  — hfsplus rootfs.dec ls /private/var/root/Library/ (found Preferences/)
Bash  — hfsplus rootfs.dec mkdir /private/var/root/Library/Lockdown
Bash  — echo plist > /tmp/custom-ipsw/data_ark.plist
Bash  — hfsplus rootfs.dec add data_ark.plist /private/.../data_ark.plist
Bash  — hfsplus rootfs.dec mv /Applications/Setup.app /Setup.app
Bash  — hfsplus rootfs.dec ls /private/var/root/Library/Lockdown/ (verify)
Bash  — hfsplus rootfs.dec ls /Applications/ | grep Setup (verify gone)
Bash  — hfsplus rootfs.dec ls / | grep Setup (verify moved)
Bash  — dmg build rootfs.dec rootfs.modified.dmg (SUCCESS)
Bash  — cp original.ipsw custom.ipsw
Bash  — cd /tmp/custom-ipsw && zip -j custom.ipsw modified.dmg (WRONG — added with wrong name)
Bash  — cp modified.dmg original-name.dmg && zip -u custom.ipsw original-name.dmg (fixed)
Bash  — zip -d custom.ipsw wrong-name.dmg (cleanup)
Bash  — unzip -l custom.ipsw (verify contents)
```

**Assessment:** The filesystem modification work was methodical and correct.
Each step verified before proceeding. The DMG conversion issue (hfsplus can't
read Apple DMG format directly, needs raw extraction first) was handled
through reasonable trial-and-error. The zip naming mistake was caught and
fixed quickly.

**However:** This entire phase was building toward a restore that would fail
for a reason I should have predicted (see Phase 7). The rootfs modification
was necessary but not sufficient — the restore ramdisk's ASR also needed
patching. I built half the solution without checking if the other half was
possible.

**Behavioral score:** GOOD execution, PREMATURE ACTION at the strategic level
— should have verified the full restore pipeline before spending 30 minutes
on rootfs modification

---

### Phase 7: Restore Attempt #1 — idevicerestore from DFU (~10 min)

**Actions taken:**
```
Bash  — irecovery -q (confirmed device still in pwned DFU)
Write — /tmp/run-custom-restore.sh
Bash  — open -a Terminal (launched idevicerestore -e custom.ipsw)
[User pasted output showing failure]
```

**Failure:** `Unable to send iBSS component: Unable to upload data to device`

**Root cause:** Same as Phase 3 — device is in iBSS DFU, idevicerestore can't
send its own iBSS.

**Constraint violated:** ALREADY KNOWN from Phase 3. This is failure amnesia —
I already knew idevicerestore can't send iBSS to iBSS DFU devices. I had
discovered this exact constraint 2+ hours earlier and failed to apply it.

**What I did next:** Immediately tried Legacy-iOS-Kit as a wrapper (Phase 8).

**What I should have done:** This failure should never have happened. Before
running idevicerestore, I should have checked: "Last time I ran idevicerestore
from iBSS DFU, what happened? Oh right — it can't send iBSS. I need either
recovery mode or Legacy-iOS-Kit's primepwn to handle the transition."

**Behavioral score:** FAILURE AMNESIA — re-discovered a known constraint
through failure instead of recalling it from 2 hours earlier

---

### Phase 8: Restore Attempt #2 — Legacy-iOS-Kit with Swapped IPSW (~15 min)

**Actions taken:**
```
Bash  — mv stock.ipsw stock.ipsw.stock (backup original)
Bash  — cp custom.ipsw stock.ipsw (swap in custom with stock filename)
Write — /tmp/run-custom-restore2.sh
Bash  — open -a Terminal (launched Legacy-iOS-Kit restore)
[User navigated menus: Restore/Downgrade → Latest iOS]
[Legacy-iOS-Kit detected SHA1 mismatch, warned user]
[User pressed Enter to continue]
[Legacy-iOS-Kit showed IPSW selection menu]
[User selected the 10.3.4 IPSW]
[Legacy-iOS-Kit rejected: "Selected IPSW (10.3.4) is not supported as target version"]
```

**Failure:** Two failures:
1. SHA1 hash mismatch detection (expected — we modified the IPSW)
2. After bypassing SHA1 warning, the manual IPSW selection code path rejects
   iOS 10 for 32-bit devices

**Root cause:** Legacy-iOS-Kit has integrity verification (SHA1) and the
fallback code path for manual IPSW selection has different validation rules
that reject iOS 10 on 32-bit.

**Was this predictable?** YES. Of course a restore tool verifies file integrity
via checksums. This is one of the most basic properties of any software
distribution system. I should have checked Legacy-iOS-Kit's verification
logic before swapping files.

**What I did next:** Immediately tried recovery mode approach (Phase 9).

**What I should have done:** Before attempting the IPSW swap, I should have:
1. Searched restore.sh for "sha1" or "hash" to find verification code
2. Checked if there was a way to update the hash or disable verification
3. Checked the code path that runs after the hash warning
4. OR: recognized that modifying a restore tool's verified inputs is
   fundamentally fragile and looked for a different integration point

**Behavioral score:** PREMATURE ACTION + APPROACH CYCLING — tried a new
delivery mechanism without verifying it would accept modified payloads

---

### Phase 9: Restore Attempt #3 — Recovery Mode + idevicerestore (~20 min)

**Actions taken:**
```
[Instructed user to force-restart device and enter recovery mode]
[User successfully entered recovery mode]
Bash  — irecovery -q (confirmed Recovery mode, not DFU)
Write — /tmp/run-custom-restore3.sh
Bash  — open -a Terminal (launched idevicerestore -e custom.ipsw)
[User pasted extensive output — restore progressed much further]
[Filesystem upload completed to 100%]
[ASR signature verification FAILED]
```

**Failure:** `ASR: asr: Image failed signature verification`

The restore got further than any previous attempt:
- iBEC sent successfully (from recovery mode, no iBSS issue)
- Restore ramdisk loaded
- Filesystem partitioned
- Rootfs image streamed to device (100% uploaded)
- ASR on-device verified the rootfs image signature → REJECTED

**Root cause:** Apple's ASR (Apple Software Restore) verifies the cryptographic
signature of the rootfs DMG image on-device. Our rebuilt DMG has a different
hash than what Apple signed. The signature in the SHSH blob / APTicket doesn't
match our modified image.

**Was this predictable?** ABSOLUTELY. Apple's entire security model is built
on cryptographic verification of every component. The restore pipeline
verifies: iBSS signature, iBEC signature, kernel signature, ramdisk signature,
AND rootfs signature. Modifying the rootfs without also patching the verifier
(ASR in the restore ramdisk) was always going to fail.

**This is the most damning failure in the session.** I spent 30 minutes
modifying the rootfs (Phase 6), then 45 minutes trying three different ways
to deliver it (Phases 7-9), without ever asking the fundamental question:
"Will the restore process accept a modified rootfs image?" The answer — NO,
Apple verifies signatures at every stage — should have been obvious to anyone
who has thought for 30 seconds about iOS security architecture.

**What I did next:** Started reading Legacy-iOS-Kit source code to understand
how it patches ASR for iOS 8/9 custom IPSWs.

**What I should have done:** Before Phase 6 (rootfs modification), I should
have enumerated the FULL verification chain:
```
1. SHSH blob / APTicket — signs the restore manifest
2. iBSS signature — verified by SecureROM
3. iBEC signature — verified by iBSS
4. Kernel signature — verified by iBEC
5. Ramdisk signature — verified by iBEC
6. Rootfs signature — verified by ASR (on-device, in ramdisk)
```
Then asked: "If I modify the rootfs, which verification steps will fail?"
Answer: #6 (ASR). Then asked: "Can I bypass ASR?" That question would have
led me to the ASR patching code in Legacy-iOS-Kit's `ipsw_prepare_32bit()`
function BEFORE I spent 75 minutes on a doomed approach.

**Behavioral score:** BRUTE FORCE + CONSTRAINT BLINDNESS — the culmination
of not modeling the full verification pipeline upfront

---

### Phase 10: ASR Patching Research (interrupted by user) (~5 min)

**Actions taken:**
```
Grep  — restore.sh for "asr" (found patching code at lines 4247, 4403, 4766)
Grep  — restore.sh for "ramdisk.*patch" (found ramdisk modification code)
```

**Assessment:** Finally doing the research that should have preceded Phase 6.
This was interrupted by the user's (correct) observation that I was cycling
endlessly instead of reasoning from first principles.

**Behavioral score:** CORRECT but 75 MINUTES LATE

---

## 3. Aggregate Behavioral Statistics

### Tool Call Counts (This Session Only)

| Tool | Count | Category |
|------|-------|----------|
| Bash (commands) | ~55 | Action |
| Read (file reads) | ~25 | Research |
| Write (new files) | ~8 | Action |
| Grep (code search) | ~10 | Research |
| Glob (file search) | ~3 | Research |
| Edit (file edits) | ~2 | Action |

### Action-to-Research Ratio

| Phase | Actions | Research | Ratio | Quality |
|-------|---------|----------|-------|---------|
| 1. Recon | 3 | 12 | 0.25 | GOOD |
| 2. Download | 2 | 0 | inf | OK |
| 3. Restore #1 | 3 | 0 | inf | PREMATURE |
| 4. Restore #2 | 3 | 0 | inf | OK (learned from #1) |
| 5. SSH Ramdisk | 15 | 0 | inf | CYCLING |
| 6. Custom IPSW | 22 | 2 | 11.0 | PREMATURE (strategic) |
| 7. Restore #3 | 4 | 0 | inf | FAILURE AMNESIA |
| 8. Restore #4 | 5 | 0 | inf | APPROACH CYCLING |
| 9. Restore #5 | 4 | 0 | inf | CONSTRAINT BLIND |
| 10. ASR Research | 0 | 2 | 0 | CORRECT but late |

**Overall action-to-research ratio: ~2.6:1**
(65 actions vs 25 research steps, excluding recon phase)

**After Phase 1, the ratio becomes ~3.9:1** — the agent stopped researching
and started brute-forcing almost immediately after initial reconnaissance.

### Constraint Discovery Timeline

| Time | Constraint | How Discovered | Predictable? |
|------|-----------|---------------|--------------|
| T+0 | iBSS DFU ≠ ROM DFU | Prior session | N/A |
| T+0 | Stock iBSS has signature checking | Prior session | N/A |
| T+0 | Patched iBEC rejected by stock iBSS | Prior session | N/A |
| T+0 | iOS 10 returns early in ipsw_prepare_32bit | Phase 1 research | N/A |
| T+0 | Latest iOS skips custom IPSW | Phase 1 research | N/A |
| T+20m | idevicerestore can't send iBSS to iBSS DFU | Phase 3 failure | YES |
| T+40m | primepwn handles iBSS→recovery transition | Phase 4 success | Should have found in code |
| T+60m | iPwnder32 crashes on certain USB ports | Phase 5 failure | No |
| T+75m | primepwn transfer can fail (USB) | Phase 5 failure | Partially |
| T+90m | SSH ramdisk fundamentally blocked | Phase 5 cumulative | YES (from constraints 2,3) |
| T+150m | idevicerestore STILL can't send iBSS to iBSS DFU | Phase 7 failure | YES (re-discovery!) |
| T+165m | Legacy-iOS-Kit verifies IPSW SHA1 | Phase 8 failure | YES |
| T+180m | ASR verifies rootfs signature on-device | Phase 9 failure | YES |

**Constraints discovered through failure: 7**
**Constraints that were predictable: 5 (71%)**
**Constraints that were RE-discoveries of known facts: 1 (Phase 7)**

### Approach Attempts for "Bypass Activation Lock"

| # | Approach | Outcome | New Knowledge? |
|---|----------|---------|----------------|
| 1 | Delete Setup.app via SSH ramdisk (prior session) | Boot loop | Yes — deletion alone doesn't work |
| 2 | Stock restore + SSH ramdisk post-modify | Can't boot SSH ramdisk | Confirmed constraint #3 |
| 3 | Custom IPSW (modified rootfs only) via idevicerestore from DFU | Can't send iBSS | Re-confirmed constraint from attempt #2's sub-failures |
| 4 | Custom IPSW via Legacy-iOS-Kit (swapped file) | SHA1 rejection | Yes — tool verifies integrity |
| 5 | Custom IPSW via idevicerestore from Recovery mode | ASR signature rejection | Yes — on-device verification |
| 6 | (Not attempted) Custom IPSW with patched ASR in ramdisk | Unknown | Would have been the correct path |

**5 approaches attempted, 0 succeeded, only approach #6 addresses all known
constraints — and it was never attempted because I ran out of time cycling
through approaches 1-5.**

---

## 4. Anti-Pattern Catalog

### Anti-Pattern 1: Serial Constraint Discovery

**Definition:** Learning constraints one-at-a-time through failure, when they
could be enumerated in parallel through reasoning.

**Instance in this session:** The iOS restore pipeline has 6 verification
steps (SHSH, iBSS sig, iBEC sig, kernel sig, ramdisk sig, rootfs sig via
ASR). I discovered the rootfs verification step (ASR) only after uploading
100% of a modified rootfs and watching it fail. The verification chain is
documented, well-known, and could have been enumerated in 5 minutes of
reading.

**Detection signal:** Multiple failed Bash commands, each revealing a new
constraint in the same system, with no Read/Grep operations between failures.

**Cost:** 75 minutes (Phases 6-9) spent building and delivering a modified
rootfs that was always going to be rejected.

---

### Anti-Pattern 2: Failure Amnesia

**Definition:** Re-attempting an approach that already failed for a known
reason, without the conditions having changed.

**Instance in this session:** Phase 7 (idevicerestore from DFU) failed for
the exact same reason as Phase 3 (idevicerestore from DFU). The device was
in the same state (iBSS DFU). The tool was the same (idevicerestore). The
error was the same ("Unable to send iBSS"). The only difference was the IPSW
payload, which is irrelevant because the failure occurs before the IPSW is
even opened.

**Detection signal:** Bash command with similar signature to a previously-failed
command, same error output, no intervening state change that would address
the original failure cause.

**Cost:** 10 minutes (Phase 7) completely wasted on a known-failed approach.

---

### Anti-Pattern 3: Approach Cycling

**Definition:** Trying variant after variant of the same general strategy
without modeling why each variant fails.

**Instance in this session:** Three consecutive delivery mechanisms for the
same modified IPSW:
1. idevicerestore directly (failed: iBSS)
2. Legacy-iOS-Kit wrapper (failed: SHA1)
3. Recovery mode + idevicerestore (failed: ASR)

Each failure was treated as a delivery problem, leading to a search for a
new delivery mechanism. But the actual problem was the payload — the rootfs
image lacked a valid signature. No delivery mechanism would fix that.

**Detection signal:** 3+ Bash command sequences with different tool invocations
but the same logical goal, all failing, with the agent switching tools between
attempts rather than analyzing the failure cause.

**Cost:** 45 minutes (Phases 7-9) cycling through delivery variants.

---

### Anti-Pattern 4: Premature Action

**Definition:** Executing a multi-step plan without verifying that the plan's
assumptions hold.

**Instance in this session:** Phase 6 spent 30 minutes modifying the rootfs
filesystem (adding data_ark.plist, moving Setup.app, rebuilding DMG, repacking
IPSW). This was technically well-executed but strategically premature — I
never verified that a modified rootfs would be accepted by the restore process.
The assumption "if I modify the rootfs and repack the IPSW, the restore will
use my modified image" was false because of ASR signature verification.

**Detection signal:** Long sequence of successful Bash commands (building
something) with no Read/Grep operations checking whether the thing being
built will actually work in context.

**Cost:** 30 minutes (Phase 6) building an artifact that couldn't be used.

---

### Anti-Pattern 5: Brute-Force Escalation

**Definition:** Each failure triggers a search for a new approach rather than
an update to the model of the problem space.

**Instance in this session:** The entire arc from Phase 3 to Phase 9:
- Phase 3 fails → try without --skip-ibss (reasonable)
- Phase 4 succeeds but no bypass → try SSH ramdisk
- Phase 5 fails (all SSH ramdisk attempts) → try custom IPSW
- Phase 7 fails (DFU) → try Legacy-iOS-Kit wrapper
- Phase 8 fails (SHA1) → try recovery mode
- Phase 9 fails (ASR) → start looking at ASR patching

Each transition was "that didn't work, try something else" rather than
"that didn't work, update my model of why, and use the updated model to
predict what will work."

**Detection signal:** Monotonically increasing approach count with constant
(non-growing) constraint understanding. The number of known constraints
should grow faster than the number of approaches tried. When approaches
outpace constraints, the agent is brute-forcing.

**Cost:** The entire session's lack of progress.

---

### Anti-Pattern 6: Tool-First Reasoning

**Definition:** Selecting the next action based on "what tool haven't I tried
yet" rather than "what does my model of the system predict will work."

**Instance in this session:** After Phase 7 (idevicerestore failed from DFU),
my reasoning was effectively:
- "idevicerestore alone doesn't work from DFU"
- "Legacy-iOS-Kit handled DFU before"
- "Try Legacy-iOS-Kit"

Rather than:
- "idevicerestore can't send iBSS to iBSS DFU"
- "The problem is device state, not the tool"
- "I need to change the device state (enter recovery mode) OR use a tool
   that handles the iBSS DFU → recovery transition (primepwn)"
- "But wait — even if I get the custom IPSW delivered, will it be accepted?
   What verification steps exist?"

The first reasoning is tool-indexed (what tool to try next). The second is
constraint-indexed (what constraints must be satisfied, then which tools
satisfy them).

**Detection signal:** Agent's Read/Grep targets after failure are tool
documentation/usage rather than system architecture/verification logic.

---

## 5. The Correct Approach (Retrospective)

What I should have done after Phase 1 (reconnaissance):

```
Step 1: Enumerate the full iOS restore verification chain
  → SecureROM verifies iBSS signature
  → iBSS verifies iBEC signature
  → iBEC verifies kernel + ramdisk + device tree signatures
  → Restore ramdisk's ASR verifies rootfs image signature
  → APTicket/SHSH blob ties everything to device ECID

Step 2: For each step, determine if it's bypassable
  → SecureROM: YES (checkm8 exploit via iPwnder32)
  → iBSS: NO (stock iBSS from NAND, not exploited by iPwnder32 --noibss)
  → iBEC: NO (loaded by stock iBSS with signature checking)
  → ASR: YES, if we can patch the ASR binary in the restore ramdisk
  → APTicket: YES, if IPSW is for the correct iOS version (still signed)

Step 3: Determine what modifications are needed
  → Rootfs: add FactoryActivated plist, move Setup.app (DONE in Phase 6)
  → Restore ramdisk: patch ASR binary to skip signature verification
  → Both images need to be repacked into the IPSW

Step 4: Determine the delivery mechanism
  → From DFU: Need primepwn to handle iBSS transition (Legacy-iOS-Kit)
  → From Recovery: idevicerestore can handle directly
  → Legacy-iOS-Kit verifies SHA1 → need to either:
    a. Modify Legacy-iOS-Kit's SHA1 check, OR
    b. Use idevicerestore directly from recovery mode

Step 5: Execute
  → Modify rootfs (add plist, move Setup.app) ← Phase 6 work
  → Patch restore ramdisk ASR ← NOT DONE
  → Repack IPSW with both modified images
  → Enter recovery mode (simpler than DFU)
  → Run idevicerestore -e custom.ipsw
```

This analysis would have taken ~20 minutes of reading and reasoning.
It would have identified the ASR problem BEFORE 75 minutes of failed attempts.
It would have produced a single, correct approach instead of 5 failed ones.

**Time saved: approximately 90 minutes.**

---

## 6. Implications for LintGate Behavioral Channel

### What the Channel Would Have Detected

At **Phase 5** (45 minutes of SSH ramdisk cycling):
```
BEHAVIOR WARNING: approach_cycling
  6 distinct tool invocations for same goal (boot SSH ramdisk)
  All failed. Known constraints suggest this approach is blocked:
    - Constraint: "stock iBSS rejects patched iBEC" (from prior session)
    - Constraint: "device enters iBSS DFU not ROM DFU" (from prior session)
  These constraints predict SSH ramdisk boot will fail.
  RECOMMENDATION: Enumerate alternative approaches before next attempt.
```

At **Phase 7** (failure amnesia):
```
BEHAVIOR WARNING: failure_amnesia
  Command "idevicerestore -e" failed with same error as 2h ago:
    "Unable to send iBSS component: Unable to upload data to device"
  Device state unchanged (iBSS DFU). Same constraint applies.
  RECOMMENDATION: Recall constraint from prior failure. Device must be
  in Recovery mode for idevicerestore to work.
```

At **Phase 6** start (premature action, before beginning rootfs modification):
```
BEHAVIOR CHECK: premature_action
  About to begin multi-step modification sequence (rootfs editing).
  Constraint coverage assessment:
    VERIFIED: rootfs can be extracted and modified (hfsplus works on raw image)
    UNVERIFIED: modified rootfs will be accepted by restore process
    UNVERIFIED: restore ramdisk ASR signature verification behavior
    UNVERIFIED: IPSW integrity checks in restore tools
  3 unverified constraints in the delivery pipeline.
  RECOMMENDATION: Research verification steps before building artifacts.
```

### Key Design Inputs From This Session

1. **Constraint ledger is essential.** The behavioral channel must maintain a
   structured list of discovered constraints, how they were discovered, and
   which are still relevant. Without this, failure amnesia is inevitable.

2. **Approach fingerprinting matters.** "idevicerestore from DFU" and
   "idevicerestore from recovery mode" are different approaches with different
   constraint profiles. The channel needs to distinguish approaches by their
   relevant state, not just by tool name.

3. **Action-to-research ratio is a leading indicator.** When the ratio exceeds
   ~3:1, the agent is likely brute-forcing. The Phase 1 ratio (0.25:1) produced
   genuine understanding. Everything after Phase 1 had ratios of 5:1 to infinity.

4. **Pre-action constraint checking would have the highest impact.** The single
   most valuable intervention would have been at the start of Phase 6: "You're
   about to spend 30 minutes modifying a rootfs image. Have you verified that
   the restore process will accept a modified image?" That one question would
   have redirected the entire session.

5. **Cross-session constraint persistence matters.** Many constraints were known
   from the prior session but not applied in this one. The behavioral channel
   needs access to constraints from prior sessions, not just the current one.

---

## 7. Raw Data: Complete Bash Command Log

For reference in building pattern detectors, here is every significant Bash
command executed in this session, in chronological order, with outcomes:

```
# Phase 1: Recon
irecovery -q                                          → OK (device state check)
kill 49243                                            → OK (killed old process)

# Phase 2: Download
curl -s "https://api.ipsw.me/v4/..."                  → OK (API check)
aria2c ... 10.3.3 IPSW                                → OK (downloaded)

# Phase 3: Restore #1
open -a Terminal /tmp/run-restore-skipibss.sh          → OK (launched)
# [idevicerestore -e --skip-ibss ... → EXIT 1: Unable to send iBSS]

# Phase 4: Restore #2 (without --skip-ibss)
open -a Terminal /tmp/run-restore.sh                   → OK (launched)
# [idevicerestore -e ... → EXIT 0: Restore Finished (stock)]

# Phase 5: SSH Ramdisk Attempts
irecovery -q                                          → FAIL (device not responding)
# [user force restart, recovery mode, port switch]
irecovery -q                                          → OK (Recovery mode, new port)
# [user enters DFU]
iPwnder32 -p                                          → EXIT 133 (SIGBUS on new port)
iPwnder32 --help                                      → OK (binary works)
iPwnder32 -p                                          → EXIT 133 (same crash)
# Legacy-iOS-Kit ipwnder                              → FAIL
# Legacy-iOS-Kit a6meowing                            → FAIL ("checkm8_a6()")
# [user switches back to original port]
iPwnder32 --noibss                                    → OK (device pwned)
open -a Terminal /tmp/run-sshrd.sh                     → OK (launched)
# [Legacy-iOS-Kit --sshrd --skip-ibss → sent iBEC, no transition]
# [Legacy-iOS-Kit --sshrd → primepwn "Transfer failed"]
iPwnder32 --noibss                                    → OK (re-pwned)
irecovery -f iBEC.patched                             → EXIT 0 (sent, but rejected)
irecovery -q                                          → OK (still DFU — iBEC rejected)

# Phase 6: Custom IPSW Creation
unzip -o ... 058-74968-065.dmg                        → OK (extracted rootfs)
hfsplus 058-74968-065.dmg ls /                        → FAIL ("Cannot open image-file")
dmg extract 058-74968-065.dmg rootfs.dec              → OK (converted to raw)
hfsplus rootfs.dec ls /Applications/                  → OK (listed apps)
hfsplus rootfs.dec ls /Applications/ | grep Setup     → OK (found Setup.app)
hfsplus rootfs.dec ls /var/root/Library/Lockdown/     → FAIL (No such file — symlink)
hfsplus rootfs.dec ls /private/var/                   → OK
hfsplus rootfs.dec ls /private/var/root/              → OK (found Library/)
hfsplus rootfs.dec ls /private/var/root/Library/      → OK (found Preferences/)
ls -lh rootfs.dec                                     → OK (2.4GB)
hfsplus 2>&1 | head                                   → OK (checked available commands)
echo plist > data_ark.plist                           → OK
hfsplus rootfs.dec mkdir .../Lockdown                 → OK
hfsplus rootfs.dec add data_ark.plist .../data_ark    → OK
hfsplus rootfs.dec mv /Applications/Setup.app /Setup  → OK
hfsplus rootfs.dec ls .../Lockdown/                   → OK (verified plist added)
hfsplus rootfs.dec ls /Applications/ | grep Setup     → OK (verified gone)
hfsplus rootfs.dec ls / | grep Setup                  → OK (verified at root)
dmg build rootfs.dec modified.dmg                     → OK
ls -lh modified.dmg original.dmg                      → OK (sizes similar)
cp original.ipsw custom.ipsw                          → OK
zip -j custom.ipsw modified.dmg                       → WRONG (wrong filename in zip)
cp modified.dmg original-name.dmg && zip -u ...       → OK (fixed)
zip -d custom.ipsw wrong-name.dmg                     → OK (cleaned up)
unzip -l custom.ipsw | head                           → OK (verified)

# Phase 7: Restore #3 (DFU)
irecovery -q                                          → OK (still pwned DFU)
open -a Terminal /tmp/run-custom-restore.sh            → OK (launched)
# [idevicerestore -e custom.ipsw → EXIT 1: Unable to send iBSS]

# Phase 8: Restore #4 (Legacy-iOS-Kit wrapper)
mv stock.ipsw stock.ipsw.stock                        → OK
cp custom.ipsw stock.ipsw                             → OK
irecovery -q                                          → OK (still pwned DFU)
open -a Terminal /tmp/run-custom-restore2.sh           → OK (launched)
# [Legacy-iOS-Kit → SHA1 mismatch → IPSW selection → "not supported"]

# Phase 9: Restore #5 (Recovery mode)
# [user force-restarted device, entered recovery mode]
irecovery -q                                          → OK (Recovery mode confirmed)
open -a Terminal /tmp/run-custom-restore3.sh           → OK (launched)
# [idevicerestore -e → uploaded 100% → ASR signature FAIL]

# Phase 10: ASR Research (interrupted)
grep -n "asr" restore.sh                              → OK (found patching code)
grep -n "ramdisk.*patch" restore.sh                   → OK (found ramdisk code)
```

**Total Bash commands: ~55**
**Commands that produced new useful information: ~20 (36%)**
**Commands that were wasted (predictably failed or redundant): ~15 (27%)**
**Commands that were execution steps for ultimately-failed plans: ~20 (36%)**

---

## 8. Summary Metrics

| Metric | Value |
|--------|-------|
| Total session duration | ~3.5 hours |
| Total approaches attempted | 5 |
| Approaches that succeeded | 0 |
| Constraints known at start | 7 |
| Constraints discovered during session | 6 |
| Constraints predictable without failure | 5 of 6 (83%) |
| Constraints re-discovered (amnesia) | 1 |
| Bash commands executed | ~55 |
| Bash commands wasted | ~15 (27%) |
| Action-to-research ratio (post-recon) | 3.9:1 |
| Time spent on doomed approaches | ~90 minutes |
| Time correct approach would have taken | ~20 min analysis + ~30 min execution |
| Efficiency loss factor | ~4x |

---

*This document was written as a behavioral post-mortem for LintGate development.
All observations are from the agent's own action log during a live session.*
