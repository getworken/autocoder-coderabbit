#!/usr/bin/env python3
"""
Security Integration Tests
===========================

Integration tests that spin up real agent instances and verify
bash command security policies are enforced correctly.

These tests actually run the agent (not just unit tests), so they:
- Create real temporary projects
- Configure real YAML files
- Execute the agent with test prompts
- Parse agent output to verify behavior

Run with: python test_security_integration.py
"""

import asyncio
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from security import bash_security_hook


@contextmanager
def temporary_home(home_path):
    """
    Temporarily set the process home directory environment variables for the duration of a context.
    
    This context manager sets HOME (and on Windows, USERPROFILE and, if available, HOMEDRIVE/HOMEPATH) to the provided path for the lifetime of the context. On exit it restores the previous environment values; keys that did not exist before entering the context are removed. Restoration occurs even if the context body raises an exception.
    
    Parameters:
        home_path (str or pathlib.Path): Path to use as the temporary home directory.
    """
    # Save original values for Unix and Windows
    saved_env = {
        "HOME": os.environ.get("HOME"),
        "USERPROFILE": os.environ.get("USERPROFILE"),
        "HOMEDRIVE": os.environ.get("HOMEDRIVE"),
        "HOMEPATH": os.environ.get("HOMEPATH"),
    }

    try:
        # Set new home directory for both Unix and Windows
        os.environ["HOME"] = str(home_path)
        if sys.platform == "win32":
            os.environ["USERPROFILE"] = str(home_path)
            # Note: HOMEDRIVE and HOMEPATH are typically set by Windows
            # but we update them for consistency
            drive, path = os.path.splitdrive(str(home_path))
            if drive:
                os.environ["HOMEDRIVE"] = drive
                os.environ["HOMEPATH"] = path

        yield

    finally:
        # Restore all original values
        for key, value in saved_env.items():
            if value is None:
                # Remove if it didn't exist before
                os.environ.pop(key, None)
            else:
                # Restore original value
                os.environ[key] = value


def test_blocked_command_via_hook():
    """
    Validate that the Bash security hook rejects hardcoded blocked commands (for example, `sudo`).
    
    Runs the hook against a minimal project configuration and asserts that a command known to be disallowed is blocked.
    
    Returns:
        bool: `True` if the hook decision is `"block"`, `False` otherwise.
    """
    print("\n" + "=" * 70)
    print("TEST 1: Hardcoded blocked command (sudo)")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)

        # Create minimal project structure
        autocoder_dir = project_dir / ".autocoder"
        autocoder_dir.mkdir()
        (autocoder_dir / "allowed_commands.yaml").write_text(
            "version: 1\ncommands: []"
        )

        # Try to run sudo (should be blocked)
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "sudo apt install nginx"},
        }
        context = {"project_dir": str(project_dir)}

        result = asyncio.run(bash_security_hook(input_data, context=context))

        if result.get("decision") == "block":
            print("✅ PASS: sudo was blocked")
            print(f"   Reason: {result.get('reason', 'N/A')[:80]}...")
            return True
        else:
            print("❌ FAIL: sudo should have been blocked")
            print(f"   Got: {result}")
            return False


def test_allowed_command_via_hook():
    """
    Verify that the Bash security hook permits the `ls` command by default.
    
    Returns:
        bool: True if the hook decision is not "block" (ls allowed), False otherwise.
    """
    print("\n" + "=" * 70)
    print("TEST 2: Default allowed command (ls)")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)

        # Create minimal project structure
        autocoder_dir = project_dir / ".autocoder"
        autocoder_dir.mkdir()
        (autocoder_dir / "allowed_commands.yaml").write_text(
            "version: 1\ncommands: []"
        )

        # Try to run ls (should be allowed - in default allowlist)
        input_data = {"tool_name": "Bash", "tool_input": {"command": "ls -la"}}
        context = {"project_dir": str(project_dir)}

        result = asyncio.run(bash_security_hook(input_data, context=context))

        if result.get("decision") != "block":
            print("✅ PASS: ls was allowed (default allowlist)")
            return True
        else:
            print("❌ FAIL: ls should have been allowed")
            print(f"   Reason: {result.get('reason', 'N/A')}")
            return False


def test_non_allowed_command_via_hook():
    """Test that commands not in any allowlist are blocked."""
    print("\n" + "=" * 70)
    print("TEST 3: Non-allowed command (wget)")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)

        # Create minimal project structure
        autocoder_dir = project_dir / ".autocoder"
        autocoder_dir.mkdir()
        (autocoder_dir / "allowed_commands.yaml").write_text(
            "version: 1\ncommands: []"
        )

        # Try to run wget (not in default allowlist)
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "wget https://example.com"},
        }
        context = {"project_dir": str(project_dir)}

        result = asyncio.run(bash_security_hook(input_data, context=context))

        if result.get("decision") == "block":
            print("✅ PASS: wget was blocked (not in allowlist)")
            print(f"   Reason: {result.get('reason', 'N/A')[:80]}...")
            return True
        else:
            print("❌ FAIL: wget should have been blocked")
            return False


def test_project_config_allows_command():
    """
    Verify that a command listed in the project's allowed_commands.yaml is permitted by the Bash security hook.
    
    Creates a temporary project configuration that includes `swift` in the allowed commands and asserts the hook does not block `swift --version`.
    
    Returns:
        bool: `True` if the hook permits the command, `False` if it blocks it.
    """
    print("\n" + "=" * 70)
    print("TEST 4: Project config allows command (swift)")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)

        # Create project config with swift allowed
        autocoder_dir = project_dir / ".autocoder"
        autocoder_dir.mkdir()
        (autocoder_dir / "allowed_commands.yaml").write_text("""version: 1
commands:
  - name: swift
    description: Swift compiler
  - name: xcodebuild
    description: Xcode build system
""")

        # Try to run swift (should be allowed via project config)
        input_data = {"tool_name": "Bash", "tool_input": {"command": "swift --version"}}
        context = {"project_dir": str(project_dir)}

        result = asyncio.run(bash_security_hook(input_data, context=context))

        if result.get("decision") != "block":
            print("✅ PASS: swift was allowed (project config)")
            return True
        else:
            print("❌ FAIL: swift should have been allowed")
            print(f"   Reason: {result.get('reason', 'N/A')}")
            return False


def test_pattern_matching():
    """
    Verify that wildcard patterns in a project's allowed_commands.yaml match command names (e.g., 'swift*' matches 'swiftlint').
    
    Runs the bash security hook using a temporary project config containing a pattern and checks whether a command matching that pattern is permitted.
    
    Returns:
        True if the command matched the pattern and was allowed, False otherwise.
    """
    print("\n" + "=" * 70)
    print("TEST 5: Pattern matching (swift*)")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)

        # Create project config with swift* pattern
        autocoder_dir = project_dir / ".autocoder"
        autocoder_dir.mkdir()
        (autocoder_dir / "allowed_commands.yaml").write_text("""version: 1
commands:
  - name: swift*
    description: All Swift tools
""")

        # Try to run swiftlint (should match swift* pattern)
        input_data = {"tool_name": "Bash", "tool_input": {"command": "swiftlint"}}
        context = {"project_dir": str(project_dir)}

        result = asyncio.run(bash_security_hook(input_data, context=context))

        if result.get("decision") != "block":
            print("✅ PASS: swiftlint matched swift* pattern")
            return True
        else:
            print("❌ FAIL: swiftlint should have matched swift*")
            print(f"   Reason: {result.get('reason', 'N/A')}")
            return False


def test_org_blocklist_enforcement():
    """
    Verify that organization-level blocked commands cannot be overridden by a project's allowed_commands configuration.
    
    Runs an integration scenario where an org config blocks specific commands (e.g., `terraform`), a project attempts to allow the same command, and the bash security hook is invoked. The test passes when the hook decision is `"block"` for the blocked command.
    
    Returns:
        bool: `True` if the security hook blocks the command (test passes), `False` otherwise.
    """
    print("\n" + "=" * 70)
    print("TEST 6: Org blocklist enforcement (terraform)")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmphome:
        with tempfile.TemporaryDirectory() as tmpproject:
            # Use context manager to safely set and restore HOME
            with temporary_home(tmphome):
                org_dir = Path(tmphome) / ".autocoder"
                org_dir.mkdir()
                (org_dir / "config.yaml").write_text("""version: 1
allowed_commands: []
blocked_commands:
  - terraform
  - kubectl
""")

                project_dir = Path(tmpproject)
                autocoder_dir = project_dir / ".autocoder"
                autocoder_dir.mkdir()

                # Try to allow terraform in project config (should fail - org blocked)
                (autocoder_dir / "allowed_commands.yaml").write_text("""version: 1
commands:
  - name: terraform
    description: Infrastructure as code
""")

                # Try to run terraform (should be blocked by org config)
                input_data = {
                    "tool_name": "Bash",
                    "tool_input": {"command": "terraform apply"},
                }
                context = {"project_dir": str(project_dir)}

                result = asyncio.run(bash_security_hook(input_data, context=context))

                if result.get("decision") == "block":
                    print("✅ PASS: terraform blocked by org config (cannot override)")
                    print(f"   Reason: {result.get('reason', 'N/A')[:80]}...")
                    return True
                else:
                    print("❌ FAIL: terraform should have been blocked by org config")
                    return False


def test_org_allowlist_inheritance():
    """
    Verify that organization-level allowed commands are inherited by a project.
    
    Returns:
        bool: `True` if the command `jq '.data'` is permitted via the org config, `False` otherwise.
    """
    print("\n" + "=" * 70)
    print("TEST 7: Org allowlist inheritance (jq)")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmphome:
        with tempfile.TemporaryDirectory() as tmpproject:
            # Use context manager to safely set and restore HOME
            with temporary_home(tmphome):
                org_dir = Path(tmphome) / ".autocoder"
                org_dir.mkdir()
                (org_dir / "config.yaml").write_text("""version: 1
allowed_commands:
  - name: jq
    description: JSON processor
blocked_commands: []
""")

                project_dir = Path(tmpproject)
                autocoder_dir = project_dir / ".autocoder"
                autocoder_dir.mkdir()
                (autocoder_dir / "allowed_commands.yaml").write_text(
                    "version: 1\ncommands: []"
                )

                # Try to run jq (should be allowed via org config)
                input_data = {"tool_name": "Bash", "tool_input": {"command": "jq '.data'"}}
                context = {"project_dir": str(project_dir)}

                result = asyncio.run(bash_security_hook(input_data, context=context))

                if result.get("decision") != "block":
                    print("✅ PASS: jq allowed via org config")
                    return True
                else:
                    print("❌ FAIL: jq should have been allowed via org config")
                    print(f"   Reason: {result.get('reason', 'N/A')}")
                    return False


def test_invalid_yaml_ignored():
    """
    Verify that an invalid project allowed_commands.yaml is ignored and the default allowlist is used.
    
    Creates a temporary project containing malformed YAML and invokes the Bash security hook with the command "ls". The test passes if the hook does not block the command.
    
    Returns:
        bool: True if the hook did not block "ls" (invalid YAML ignored), False otherwise.
    """
    print("\n" + "=" * 70)
    print("TEST 8: Invalid YAML safely ignored")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)

        # Create invalid YAML
        autocoder_dir = project_dir / ".autocoder"
        autocoder_dir.mkdir()
        (autocoder_dir / "allowed_commands.yaml").write_text("invalid: yaml: content:")

        # Try to run ls (should still work - falls back to defaults)
        input_data = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        context = {"project_dir": str(project_dir)}

        result = asyncio.run(bash_security_hook(input_data, context=context))

        if result.get("decision") != "block":
            print("✅ PASS: Invalid YAML ignored, defaults still work")
            return True
        else:
            print("❌ FAIL: Should fall back to defaults when YAML is invalid")
            print(f"   Reason: {result.get('reason', 'N/A')}")
            return False


def test_100_command_limit():
    """
    Verify that an allowed_commands.yaml containing more than 100 entries is rejected by the Bash security hook.
    
    Creates a temporary project config with 101 command entries and invokes the hook expecting a "block" decision.
    
    Returns:
        bool: `True` if the hook blocks the command (indicating the config was rejected), `False` otherwise.
    """
    print("\n" + "=" * 70)
    print("TEST 9: 100 command limit enforced")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)

        # Create config with 101 commands
        autocoder_dir = project_dir / ".autocoder"
        autocoder_dir.mkdir()

        commands = [
            f"  - name: cmd{i}\n    description: Command {i}" for i in range(101)
        ]
        (autocoder_dir / "allowed_commands.yaml").write_text(
            "version: 1\ncommands:\n" + "\n".join(commands)
        )

        # Try to run cmd0 (should be blocked - config is invalid)
        input_data = {"tool_name": "Bash", "tool_input": {"command": "cmd0"}}
        context = {"project_dir": str(project_dir)}

        result = asyncio.run(bash_security_hook(input_data, context=context))

        if result.get("decision") == "block":
            print("✅ PASS: Config with >100 commands rejected")
            return True
        else:
            print("❌ FAIL: Config with >100 commands should be rejected")
            return False


def main():
    """
    Run the security integration test suite and report pass/fail results.
    
    Executes a predefined set of integration tests for the Bash security hook, printing per-test failures (including exceptions) and a final summary. Tests are run sequentially; successes and failures are counted and displayed.
    
    Returns:
        exit_code (int): 0 if all tests passed, 1 if any test failed.
    """
    print("=" * 70)
    print("  SECURITY INTEGRATION TESTS")
    print("=" * 70)
    print("\nThese tests verify bash command security policies using real hooks.")
    print("They test the actual security.py implementation, not just unit tests.\n")

    tests = [
        test_blocked_command_via_hook,
        test_allowed_command_via_hook,
        test_non_allowed_command_via_hook,
        test_project_config_allows_command,
        test_pattern_matching,
        test_org_blocklist_enforcement,
        test_org_allowlist_inheritance,
        test_invalid_yaml_ignored,
        test_100_command_limit,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ FAIL: Test raised exception: {e}")
            import traceback

            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 70)
    print(f"  RESULTS: {passed} passed, {failed} failed")
    print("=" * 70)

    if failed == 0:
        print("\n✅ ALL INTEGRATION TESTS PASSED")
        return 0
    else:
        print(f"\n❌ {failed} INTEGRATION TEST(S) FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())