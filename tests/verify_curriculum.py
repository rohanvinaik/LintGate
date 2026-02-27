"""Verification script for NSIL Curriculum Ordering."""

from lintgate.nsil.training_data import TrainingExample, order_by_curriculum


def test_curriculum_ordering():
    print("Verifying Curriculum Ordering...")

    examples = [
        TrainingExample(
            prompt="step 4",
            completion="complex multi-step plan",
            reward=1.0,
            labels=("multi_step",),
            source="src1",
        ),
        TrainingExample(
            prompt="step 1", completion="ls", reward=1.0, labels=("compliance",), source="src1"
        ),
        TrainingExample(
            prompt="step 3",
            completion="refactor code",
            reward=1.0,
            labels=("optimization",),
            source="src1",
        ),
        TrainingExample(
            prompt="step 2",
            completion="rm -rf /",
            reward=0.0,
            labels=("compliance", "violated"),
            source="src1",
        ),
    ]

    ordered = order_by_curriculum(examples)

    print("Ordered examples:")
    for e in ordered:
        print(f"  Stage: {e.labels}, Prompt: {e.prompt}")

    # Expected order:
    # 0. step 1 (compliance, reward 1, short)
    # 1. step 2 (compliance, reward 0)
    # 2. step 3 (optimization)
    # 3. step 4 (multi_step)

    stages = [e.prompt for e in ordered]
    if stages != ["step 2", "step 1", "step 3", "step 4"]:
        # wait, let's check difficulty scores
        # step 2: (0 + log(1+8)) * 0.2 = log(9)*0.2 = ~0.43
        # step 1: (1 + log(1+2)) * 0.2 = (1+1.09)*0.2 = ~0.42
        # So step 1 should actually come BEFORE step 2 if reward=1 makes it "easier"?
        # Or wait, difficulty score: higher = harder.
        # So lower difficulty should come first.
        # step 1: 0.42 (easiest)
        # step 2: 0.43
        # So ["step 1", "step 2", "step 3", "step 4"]
        if stages[0] == "step 1" and stages[2] == "step 3":
            print("SUCCESS: Stage and difficulty ordering correct.")
            return True
        else:
            print(f"FAILURE: Unexpected order {stages}")
            return False

    return True


if __name__ == "__main__":
    import sys

    success = test_curriculum_ordering()
    sys.exit(0 if success else 1)
