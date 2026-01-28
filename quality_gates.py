"""
Quality Gates Module
====================

Provides quality checking functionality for the Autocoder system.
Runs lint, type-check, and custom scripts before allowing features
to be marked as passing.

Supports:
- ESLint/Biome for JavaScript/TypeScript
- ruff/flake8 for Python
- Custom scripts via .autocoder/quality-checks.sh
"""

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TypedDict


class QualityCheckResult(TypedDict):
    """Result of a single quality check."""
    name: str
    passed: bool
    output: str
    duration_ms: int


class QualityGateResult(TypedDict):
    """Result of all quality checks combined."""
    passed: bool
    timestamp: str
    checks: dict[str, QualityCheckResult]
    summary: str


def _run_command(cmd: list[str], cwd: Path, timeout: int = 60) -> tuple[int, str, int]:
    """
    Execute a shell command in the given working directory and return its result and duration.
    
    Parameters:
        cmd (list[str]): Command and arguments to execute.
        cwd (Path): Working directory where the command runs.
        timeout (int): Maximum time in seconds to allow the command to run.
    
    Returns:
        tuple[int, str, int]: (exit_code, combined_output, duration_ms)
            - exit_code: process exit code; `124` if the command timed out, `127` if the executable was not found, `1` for other internal errors.
            - combined_output: stdout and stderr concatenated and trimmed of surrounding whitespace.
            - duration_ms: elapsed time in milliseconds measuring command execution (or until timeout/exception).
    """
    import time
    start = time.time()

    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration_ms = int((time.time() - start) * 1000)
        output = result.stdout + result.stderr
        return result.returncode, output.strip(), duration_ms
    except subprocess.TimeoutExpired:
        duration_ms = int((time.time() - start) * 1000)
        return 124, f"Command timed out after {timeout}s", duration_ms
    except FileNotFoundError:
        return 127, f"Command not found: {cmd[0]}", 0
    except Exception as e:
        return 1, str(e), 0


def _detect_js_linter(project_dir: Path) -> tuple[str, list[str]] | None:
    """
    Detect the JavaScript/TypeScript linter to use.

    Returns:
        (name, command) tuple, or None if no linter detected
    """
    # Check for ESLint
    if (project_dir / "node_modules/.bin/eslint").exists():
        return ("eslint", ["node_modules/.bin/eslint", ".", "--max-warnings=0"])

    # Check for Biome
    if (project_dir / "node_modules/.bin/biome").exists():
        return ("biome", ["node_modules/.bin/biome", "lint", "."])

    # Check for package.json lint script
    package_json = project_dir / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text())
            scripts = data.get("scripts", {})
            if "lint" in scripts:
                return ("npm_lint", ["npm", "run", "lint"])
        except (json.JSONDecodeError, OSError):
            pass

    return None


def _detect_python_linter(project_dir: Path) -> tuple[str, list[str]] | None:
    """
    Detects an available Python linter for the given project.
    
    Checks in this order and returns the first match:
    1. `ruff` available on PATH -> ("ruff", ["ruff", "check", "."])
    2. `flake8` available on PATH -> ("flake8", ["flake8", "."])
    3. `venv/bin/ruff` inside the project directory -> ("ruff", [venv_path, "check", "."])
    4. `venv/bin/flake8` inside the project directory -> ("flake8", [venv_path, "."])
    
    Parameters:
        project_dir (Path): Path to the project root where a virtual environment may exist.
    
    Returns:
        tuple[str, list[str]] | None: A (name, command) tuple where `name` is the linter identifier
        and `command` is the argument list to run it, or `None` if no linter is detected.
    """
    # Check for ruff
    if shutil.which("ruff"):
        return ("ruff", ["ruff", "check", "."])

    # Check for flake8
    if shutil.which("flake8"):
        return ("flake8", ["flake8", "."])

    # Check in virtual environment
    venv_ruff = project_dir / "venv/bin/ruff"
    if venv_ruff.exists():
        return ("ruff", [str(venv_ruff), "check", "."])

    venv_flake8 = project_dir / "venv/bin/flake8"
    if venv_flake8.exists():
        return ("flake8", [str(venv_flake8), "."])

    return None


def _detect_type_checker(project_dir: Path) -> tuple[str, list[str]] | None:
    """
    Select an appropriate TypeScript or Python type checker for the given project and provide the command to invoke it.
    
    Returns:
        `(name, command)` tuple where `name` is the detected checker ('tsc' or 'mypy') and `command` is the CLI invocation as a list of strings, or `None` if no checker is found.
    """
    # TypeScript
    if (project_dir / "tsconfig.json").exists():
        if (project_dir / "node_modules/.bin/tsc").exists():
            return ("tsc", ["node_modules/.bin/tsc", "--noEmit"])
        if shutil.which("npx"):
            return ("tsc", ["npx", "tsc", "--noEmit"])

    # Python (mypy)
    if (project_dir / "pyproject.toml").exists() or (project_dir / "setup.py").exists():
        if shutil.which("mypy"):
            return ("mypy", ["mypy", "."])
        venv_mypy = project_dir / "venv/bin/mypy"
        if venv_mypy.exists():
            return ("mypy", [str(venv_mypy), "."])

    return None


def run_lint_check(project_dir: Path) -> QualityCheckResult:
    """
    Detects a JavaScript/TypeScript or Python linter for the given project, runs it, and returns a structured lint result.
    
    Detection tries JS/TS linters first, then Python linters. If no linter is found the check is skipped and reported as passed. Linter output is truncated to 5000 characters with a "\n... (truncated)" suffix when longer.
    
    Parameters:
        project_dir (Path): Path to the project root used to detect and execute the linter.
    
    Returns:
        QualityCheckResult: A mapping with:
            - name: descriptive name of the check (e.g., "lint (eslint)" or "lint"),
            - passed: `true` if the linter exited with code 0 or the check was skipped,
            - output: the linter output, "No issues found" when empty, or a skip message,
            - duration_ms: execution duration in milliseconds (0 when skipped).
    """
    # Try JS/TS linter first
    linter = _detect_js_linter(project_dir)
    if linter is None:
        # Try Python linter
        linter = _detect_python_linter(project_dir)

    if linter is None:
        return {
            "name": "lint",
            "passed": True,
            "output": "No linter detected, skipping lint check",
            "duration_ms": 0,
        }

    name, cmd = linter
    exit_code, output, duration_ms = _run_command(cmd, project_dir)

    # Truncate output if too long
    if len(output) > 5000:
        output = output[:5000] + "\n... (truncated)"

    return {
        "name": f"lint ({name})",
        "passed": exit_code == 0,
        "output": output if output else "No issues found",
        "duration_ms": duration_ms,
    }


def run_type_check(project_dir: Path) -> QualityCheckResult:
    """
    Run a type checker for the given project and return its result.
    
    Detects an appropriate type checker for the project; if none is found the check is skipped.
    
    Parameters:
        project_dir (Path): Project root directory in which to detect and run the type checker.
    
    Returns:
        QualityCheckResult: A dict with fields:
            - name (str): Identifier for the check (e.g. "type_check (mypy)").
            - passed (bool): `true` if the checker exited with code 0, `false` otherwise.
            - output (str): Combined stdout/stderr from the checker, or a message such as
              "No type checker detected, skipping type check" or "No type errors found".
            - duration_ms (int): Execution duration in milliseconds (0 for skipped checks).
    """
    checker = _detect_type_checker(project_dir)

    if checker is None:
        return {
            "name": "type_check",
            "passed": True,
            "output": "No type checker detected, skipping type check",
            "duration_ms": 0,
        }

    name, cmd = checker
    exit_code, output, duration_ms = _run_command(cmd, project_dir, timeout=120)

    # Truncate output if too long
    if len(output) > 5000:
        output = output[:5000] + "\n... (truncated)"

    return {
        "name": f"type_check ({name})",
        "passed": exit_code == 0,
        "output": output if output else "No type errors found",
        "duration_ms": duration_ms,
    }


def run_custom_script(
    project_dir: Path,
    script_path: str | None = None,
    explicit_config: bool = False,
) -> QualityCheckResult | None:
    """
    Run a project-specific custom quality-check shell script and return its result.
    
    If `script_path` is omitted, the default ".autocoder/quality-checks.sh" is used. If the resolved script does not exist:
    - returns `None` when the default script is missing and the script was not explicitly configured;
    - returns a failed `QualityCheckResult` when the user explicitly provided or enabled a script and it is missing.
    
    The function attempts to make the script executable, runs it via `bash` with a 300-second timeout, truncates output longer than 10000 characters, and treats an empty output as a successful completion message.
    
    Parameters:
        project_dir (Path): Path to the project root containing the script.
        script_path (str | None): Relative path to the custom script within the project. Defaults to ".autocoder/quality-checks.sh".
        explicit_config (bool): When True, a missing script is considered a configuration error and returns a failing result.
    
    Returns:
        QualityCheckResult | None: A result dictionary with keys `name`, `passed`, `output`, and `duration_ms`, or `None` if the default script was absent and not explicitly configured.
    """
    user_configured = script_path is not None or explicit_config

    if script_path is None:
        script_path = ".autocoder/quality-checks.sh"

    script_full_path = project_dir / script_path

    if not script_full_path.exists():
        if user_configured:
            # User explicitly configured a script that doesn't exist - return error
            return {
                "name": "custom_script",
                "passed": False,
                "output": f"Configured script not found: {script_path}",
                "duration_ms": 0,
            }
        # Default script doesn't exist - that's OK, skip silently
        return None

    # Make sure it's executable
    try:
        script_full_path.chmod(0o755)
    except OSError:
        pass

    exit_code, output, duration_ms = _run_command(
        ["bash", str(script_full_path)],
        project_dir,
        timeout=300,  # 5 minutes for custom scripts
    )

    # Truncate output if too long
    if len(output) > 10000:
        output = output[:10000] + "\n... (truncated)"

    return {
        "name": "custom_script",
        "passed": exit_code == 0,
        "output": output if output else "Script completed successfully",
        "duration_ms": duration_ms,
    }


def verify_quality(
    project_dir: Path,
    run_lint: bool = True,
    run_type_check: bool = True,
    run_custom: bool = True,
    custom_script_path: str | None = None,
) -> QualityGateResult:
    """
    Run the enabled quality checks for a project and return an aggregated result.
    
    Parameters:
        project_dir (Path): Path to the project directory to run checks in.
        run_lint (bool): If True, run the lint check.
        run_type_check (bool): If True, run the type checker.
        run_custom (bool): If True, run the custom script check.
        custom_script_path (str | None): Path to a custom quality script; if provided, the script is treated as explicitly configured (missing script is treated as a failure).
    
    Returns:
        QualityGateResult: Aggregated result containing:
            - passed: `true` if all executed checks passed, `false` otherwise.
            - timestamp: UTC ISO-formatted time when checks completed.
            - checks: mapping of individual check names to their QualityCheckResult.
            - summary: a short human-readable summary of passed/failed checks.
    """
    checks: dict[str, QualityCheckResult] = {}
    all_passed = True

    if run_lint:
        lint_result = run_lint_check(project_dir)
        checks["lint"] = lint_result
        if not lint_result["passed"]:
            all_passed = False

    if run_type_check:
        type_result = run_type_check(project_dir)
        checks["type_check"] = type_result
        if not type_result["passed"]:
            all_passed = False

    if run_custom:
        custom_result = run_custom_script(
            project_dir,
            custom_script_path,
            explicit_config=custom_script_path is not None,
        )
        if custom_result is not None:
            checks["custom_script"] = custom_result
            if not custom_result["passed"]:
                all_passed = False

    # Build summary
    passed_count = sum(1 for c in checks.values() if c["passed"])
    total_count = len(checks)
    failed_names = [name for name, c in checks.items() if not c["passed"]]

    if all_passed:
        summary = f"All {total_count} quality checks passed"
    else:
        summary = f"{passed_count}/{total_count} checks passed. Failed: {', '.join(failed_names)}"

    return {
        "passed": all_passed,
        "timestamp": datetime.utcnow().isoformat(),
        "checks": checks,
        "summary": summary,
    }


def load_quality_config(project_dir: Path) -> dict:
    """
    Load and merge quality gates configuration from .autocoder/config.json with sensible defaults.
    
    If the file is missing or cannot be read/parsed, the default configuration is returned.
    
    Parameters:
        project_dir (Path): Project root directory used to locate `.autocoder/config.json`.
    
    Returns:
        dict: Configuration with keys:
            - "enabled" (bool)
            - "strict_mode" (bool)
            - "checks" (dict) mapping check names ("lint", "type_check", "unit_tests", "custom_script") to their configured values or defaults.
    """
    defaults = {
        "enabled": True,
        "strict_mode": True,
        "checks": {
            "lint": True,
            "type_check": True,
            "unit_tests": False,
            "custom_script": None,
        },
    }

    config_path = project_dir / ".autocoder" / "config.json"
    if not config_path.exists():
        return defaults

    try:
        data = json.loads(config_path.read_text())
        quality_config = data.get("quality_gates", {})

        # Merge with defaults
        result = defaults.copy()
        for key in ["enabled", "strict_mode"]:
            if key in quality_config:
                result[key] = quality_config[key]

        if "checks" in quality_config:
            result["checks"] = {**defaults["checks"], **quality_config["checks"]}

        return result
    except (json.JSONDecodeError, OSError):
        return defaults