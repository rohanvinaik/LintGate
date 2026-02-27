"""Verification script for NSIL StreamingGuard."""

from lintgate.nsil.streaming import StreamingGuard


def test_streaming_violation():
    print("Verifying StreamingGuard...")

    # Active constraint to block rm -rf
    guard = StreamingGuard(active_constraints=["no-rm-rf"])

    # Mock stream emitting 'rm -rf /' slowly
    def mock_dangerous_stream():
        payload = "Sure, I can help with that. Running: rm -rf /"
        for i in range(len(payload)):
            yield payload[i]

    print("Running dangerous stream (rm -rf /)...")
    output = ""
    violation_seen = False

    for chunk in guard.guard_stream(mock_dangerous_stream()):
        output += chunk
        if "NSIL VIOLATION" in chunk:
            violation_seen = True
            break

    print(f"Final output received: {repr(output)}")

    if violation_seen:
        print("SUCCESS: Violation detected and stream terminated.")
    else:
        print("FAILURE: Dangerous payload escaped!")
        return False

    # Verify that the last part of 'rm -rf /' was NOT leaked (due to 64-char window)
    # The 'rm -rf /' is only 8 chars. With a 64 char window, it should be held back
    # and then blocked when the boundary or pattern is check.
    if "rm -rf /" in output:
        print("FAILURE: Partial dangerous payload leaked in output!")
        return False
    else:
        print("SUCCESS: Dangerous payload held back by sliding window.")

    return True


if __name__ == "__main__":
    import sys

    success = test_streaming_violation()
    sys.exit(0 if success else 1)
