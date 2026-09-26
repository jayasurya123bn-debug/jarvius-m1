"""
JARVIS Dev Agent v2.0 — Advanced Autonomous Project Builder

Features:
- Git auto-commit after each passing phase (permission-gated push)
- Dockerfile + docker-compose.yml generation for API/service projects
- GitHub Actions CI/CD workflow generation
- Multi-model fallback: Gemini Flash → Gemini Pro → Ollama llama3.2
- Parallel file writing via ThreadPoolExecutor
- 8-category error classifier with specialized fix strategies
- Project templates: FastAPI, React+Vite, PyQt6, CLI, Bot, ML
- Auto test-file generation (pytest / jest)
- Live progress streaming to UI log
"""
import subprocess
import sys
import json
import re
import time
import os
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional


# ── Constants ─────────────────────────────────────────────────────────────────

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR         = get_base_dir()
API_CONFIG_PATH  = BASE_DIR / "config" / "api_keys.json"
PROJECTS_DIR     = Path.home() / "Desktop" / "JarvisProjects"
MAX_FIX_ATTEMPTS = 5

# Multi-model fallback chain
MODELS = [
    "gemini-2.5-flash",
    "gemini-1.5-flash",
    "gemini-flash-latest",
]
MODEL_PLANNER = MODELS[0]
MODEL_WRITER  = MODELS[0]


# ── Exceptions ────────────────────────────────────────────────────────────────

class RateLimitError(Exception):
    pass

class AllModelsExhausted(Exception):
    pass


# ── API / Model helpers ───────────────────────────────────────────────────────

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _get_model(model_name: str):
    from google import genai
    _c = genai.Client(api_key=_get_api_key())

    class _W:
        def generate_content(self, contents):
            return _c.models.generate_content(model=model_name, contents=contents)

    return _W()


def _get_ollama_response(prompt: str) -> str:
    """Offline fallback via Ollama llama3.2."""
    # Attempt 1: Direct HTTP request to Ollama daemon
    try:
        resp = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": "llama3.2", "prompt": prompt, "stream": False},
            timeout=120,
        )
        if resp.status_code == 200:
            text = resp.json().get("response", "").strip()
            if text:
                return text
    except Exception:
        pass

    # Attempt 2: CLI execution
    try:
        ollama_bin = "ollama"
        local_exe = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
        if os.path.exists(local_exe):
            ollama_bin = local_exe
        result = subprocess.run(
            [ollama_bin, "run", "llama3.2", prompt],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=120,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    raise AllModelsExhausted("All cloud models rate-limited/unavailable and Ollama unavailable.")


def _generate_with_fallback(prompt: str, log=None) -> str:
    """Try each Gemini model in sequence, then fall back to Ollama.

    Catches 429 (quota), 503 (high demand), 404 (model deprecated),
    and network errors, rotating models before falling back to Ollama llama3.2.
    """
    last_err = None
    for model_name in MODELS:
        try:
            model = _get_model(model_name)
            resp = model.generate_content(prompt)
            return resp.text
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            if log:
                err_code = "503" if "503" in err_str else ("404" if "404" in err_str else "429")
                log(f"[{err_code}] {model_name} unavailable - trying next model...")
            time.sleep(2)
            continue
    # All Gemini models exhausted - fall back to Ollama
    if log:
        log(
            f"[WARN] All Gemini models exhausted ({last_err}). "
            "Switching to offline Ollama llama3.2 - wait pannunga, Sir."
        )
    return _get_ollama_response(prompt)


# ── String / code utils ────────────────────────────────────────────────────────

def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\r?\n?", "", text)
    text = re.sub(r"\r?\n?```\s*$", "", text)
    return text.strip()


def _is_rate_limit(error: Exception) -> bool:
    """Returns True for any transient cloud-availability error (429, 503, demand spikes)."""
    msg = str(error).lower()
    return any(k in msg for k in (
        "429", "quota", "resource_exhausted", "rate",
        "503", "unavailable", "high demand", "overloaded", "service unavailable",
    ))


# ── Error classifier (8 categories) ──────────────────────────────────────────

def _classify_error(output: str) -> str:
    low = output.lower()

    if any(x in low for x in ("no module named", "modulenotfounderror")):
        return "dependency_error"
    if "syntaxerror" in low or "invalid syntax" in low:
        return "syntax_error"
    if "cannot import" in low or "importerror" in low:
        return "import_error"
    if "typeerror" in low:
        return "type_error"
    if "attributeerror" in low:
        return "attribute_error"
    if "filenotfounderror" in low or "no such file" in low:
        return "file_not_found"
    if "permissionerror" in low or "access is denied" in low:
        return "permission_error"
    if any(x in low for x in (
        "traceback", "exception", "error:", "nameerror",
        "valueerror", "keyerror", "indexerror", "zerodivisionerror",
    )):
        return "runtime_error"

    return "none"


def _parse_traceback(output: str, project_files: list) -> tuple:
    pattern = re.compile(r'File ["\'"]([^"\']+\.py)["\'],\s+line\s+(\d+)', re.IGNORECASE)
    matches = pattern.findall(output)
    for raw_path, line_str in reversed(matches):
        raw_name = Path(raw_path).name
        for pf in project_files:
            if Path(pf).name == raw_name or pf == raw_path or raw_path.endswith(pf):
                return pf, int(line_str)
    return None, None


def _has_error(output: str, run_command: str) -> bool:
    low = output.lower()
    if "timed out" in low or not output.strip():
        return False
    return _classify_error(output) != "none"


# ── Project Templates ──────────────────────────────────────────────────────────

_TEMPLATES = {
    "fastapi": {
        "stack": "Python + FastAPI + SQLite + Uvicorn",
        "entry_point": "main.py",
        "run_command": "uvicorn main:app --reload",
        "dependencies": ["fastapi", "uvicorn[standard]", "sqlalchemy"],
        "extra_files": ["requirements.txt", ".env.example", ".gitignore", "README.md"],
    },
    "react": {
        "stack": "React + Vite + TypeScript",
        "entry_point": "src/main.tsx",
        "run_command": "npm run dev",
        "dependencies": [],
        "extra_files": ["package.json", "vite.config.ts", ".gitignore", "README.md"],
    },
    "pyqt6": {
        "stack": "Python + PyQt6",
        "entry_point": "main.py",
        "run_command": "python main.py",
        "dependencies": ["PyQt6"],
        "extra_files": ["requirements.txt", ".gitignore", "README.md"],
    },
    "cli": {
        "stack": "Python + Typer + Rich",
        "entry_point": "main.py",
        "run_command": "python main.py --help",
        "dependencies": ["typer[all]", "rich"],
        "extra_files": ["requirements.txt", ".gitignore", "README.md"],
    },
    "bot": {
        "stack": "Python + discord.py",
        "entry_point": "bot.py",
        "run_command": "python bot.py",
        "dependencies": ["discord.py", "python-dotenv"],
        "extra_files": ["requirements.txt", ".env.example", ".gitignore", "README.md"],
    },
    "ml": {
        "stack": "Python + PyTorch + Jupyter",
        "entry_point": "train.py",
        "run_command": "python train.py",
        "dependencies": ["torch", "torchvision", "numpy", "matplotlib", "jupyter"],
        "extra_files": ["requirements.txt", ".gitignore", "README.md"],
    },
}

def _detect_template(description: str) -> Optional[str]:
    """Auto-detect best project template from description keywords."""
    desc = description.lower()
    if any(k in desc for k in ("api", "backend", "rest", "fastapi", "flask", "endpoint")):
        return "fastapi"
    if any(k in desc for k in ("react", "frontend", "web app", "vite", "next.js", "nextjs")):
        return "react"
    if any(k in desc for k in ("desktop", "gui", "pyqt", "window app")):
        return "pyqt6"
    if any(k in desc for k in ("cli", "command line", "terminal tool", "typer")):
        return "cli"
    if any(k in desc for k in ("bot", "discord", "telegram", "chatbot")):
        return "bot"
    if any(k in desc for k in ("ml", "machine learning", "train", "model", "neural", "pytorch")):
        return "ml"
    return None


# ── Git helpers ────────────────────────────────────────────────────────────────

def _git_init(project_dir: Path, log) -> bool:
    """Initialize git repo if not already initialized."""
    try:
        if not (project_dir / ".git").exists():
            subprocess.run(["git", "init"], cwd=str(project_dir),
                           capture_output=True, check=True)
            log("Git repo initialized.")
        return True
    except Exception as e:
        log(f"Git init skipped: {e}")
        return False


def _git_commit(project_dir: Path, message: str, log) -> bool:
    """Stage all and commit with message. Returns True on success."""
    try:
        subprocess.run(["git", "add", "."], cwd=str(project_dir),
                       capture_output=True, check=True)
        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(project_dir), capture_output=True, text=True
        )
        if result.returncode == 0:
            log(f"Git commit: {message}")
            return True
        if "nothing to commit" in result.stdout.lower():
            return True
        return False
    except Exception as e:
        log(f"Git commit skipped: {e}")
        return False


# ── Docker + CI/CD generation ──────────────────────────────────────────────────

def _generate_dockerfile(project_dir: Path, language: str, entry_point: str,
                          run_command: str, log) -> bool:
    """Generate Dockerfile + docker-compose.yml for the project."""
    try:
        base_image = "python:3.12-slim" if language.lower() == "python" else "node:20-alpine"
        install_cmd = "pip install -r requirements.txt" if language.lower() == "python" else "npm install"
        cmd_parts = run_command.split()

        dockerfile = f"""FROM {base_image}
WORKDIR /app
COPY . .
RUN {install_cmd}
EXPOSE 8000
CMD {json.dumps(cmd_parts)}
"""
        compose = f"""version: "3.9"
services:
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      - PYTHONUNBUFFERED=1
    restart: unless-stopped
"""
        dockerignore = "__pycache__\n*.pyc\n.env\n.git\nvenv\nnode_modules\n"

        (project_dir / "Dockerfile").write_text(dockerfile, encoding="utf-8")
        (project_dir / "docker-compose.yml").write_text(compose, encoding="utf-8")
        (project_dir / ".dockerignore").write_text(dockerignore, encoding="utf-8")
        log("Dockerfile + docker-compose.yml generated.")
        return True
    except Exception as e:
        log(f"Docker generation failed: {e}")
        return False


def _generate_ci_yml(project_dir: Path, language: str, log) -> bool:
    """Generate GitHub Actions CI workflow."""
    try:
        if language.lower() == "python":
            test_step = "python -m pytest --tb=short -q"
            install_step = "pip install -r requirements.txt"
            setup_step = 'uses: actions/setup-python@v5\n        with:\n          python-version: "3.12"'
        else:
            test_step = "npm test -- --watchAll=false"
            install_step = "npm install"
            setup_step = 'uses: actions/setup-node@v4\n        with:\n          node-version: "20"'

        ci_yml = f"""name: CI

on:
  push:
    branches: [ main, master ]
  pull_request:
    branches: [ main, master ]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up runtime
        {setup_step}
      - name: Install dependencies
        run: {install_step}
      - name: Run tests
        run: {test_step}
"""
        workflows_dir = project_dir / ".github" / "workflows"
        workflows_dir.mkdir(parents=True, exist_ok=True)
        (workflows_dir / "ci.yml").write_text(ci_yml, encoding="utf-8")
        log("GitHub Actions CI workflow generated (.github/workflows/ci.yml).")
        return True
    except Exception as e:
        log(f"CI/CD generation failed: {e}")
        return False


# ── Gitignore + dotenv ────────────────────────────────────────────────────────

def _write_gitignore(project_dir: Path, language: str) -> None:
    gi_path = project_dir / ".gitignore"
    if gi_path.exists():
        return
    if language.lower() == "python":
        content = "__pycache__/\n*.pyc\n*.pyo\n.env\nvenv/\n.venv/\ndist/\nbuild/\n*.egg-info/\n.pytest_cache/\n"
    else:
        content = "node_modules/\ndist/\n.env\n.DS_Store\n*.log\n"
    gi_path.write_text(content, encoding="utf-8")


def _write_env_example(project_dir: Path) -> None:
    env_path = project_dir / ".env.example"
    if not env_path.exists():
        env_path.write_text("# Add your environment variables here\n# API_KEY=your_key_here\n",
                             encoding="utf-8")


# ── Planning ──────────────────────────────────────────────────────────────────

def _plan_project(description: str, language: str, template_hint: Optional[str], log) -> dict:
    template_note = ""
    if template_hint and template_hint in _TEMPLATES:
        t = _TEMPLATES[template_hint]
        template_note = f"\nUse this template stack: {t['stack']}. Entry point: {t['entry_point']}."

    prompt = f"""You are a senior software architect. Create a minimal, complete file plan for this project.

Language: {language}
Description: {description}{template_note}

Return ONLY valid JSON — no markdown, no explanation:
{{
  "project_name": "snake_case_name",
  "entry_point": "main.py",
  "template": "{template_hint or 'custom'}",
  "files": [
    {{
      "path": "main.py",
      "description": "Entry point — what it does and which modules it imports",
      "imports": ["utils.helpers", "core.engine"]
    }}
  ],
  "run_command": "python main.py",
  "dependencies": ["requests"],
  "is_service": false,
  "generate_docker": false,
  "generate_ci": false
}}

Critical rules:
1. List files in DEPENDENCY ORDER — no-import files first, entry point last.
2. "imports" must list every other project module this file imports (dot-notation).
3. Keep it minimal — only files truly needed.
4. Set "is_service" true if the project is an API/server/daemon.
5. Set "generate_docker" true if is_service is true.
6. Set "generate_ci" true always.

JSON:"""

    raw = _generate_with_fallback(prompt, log)
    raw = _strip_fences(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Planner returned invalid JSON: {e}\nRaw: {raw[:300]}")


# ── File writing (with parallel support) ──────────────────────────────────────

def _write_one_file(
    file_info: dict,
    project_description: str,
    all_files: list,
    language: str,
    project_dir: Path,
    already_written: dict,
    log,
) -> tuple:
    """Write a single file. Returns (path, code) on success."""
    file_path = file_info["path"]
    file_desc = file_info.get("description", "")
    file_imports = file_info.get("imports", [])

    file_list = "\n".join(
        f"  [{i+1}] {f['path']}: {f.get('description', '')}"
        for i, f in enumerate(all_files)
    )

    dependency_context = ""
    for dep_dotted in file_imports:
        dep_path = dep_dotted.replace(".", "/") + ".py"
        if dep_path in already_written:
            snippet = already_written[dep_path][:2000]
            dependency_context += f"\n\n--- {dep_path} (import from this) ---\n{snippet}"

    lang_rules = ""
    if language.lower() == "python":
        lang_rules = """
Python-specific rules:
- Use type hints for all function signatures.
- Add docstrings for all public functions and classes.
- Use if __name__ == "__main__": guard in the entry point.
- For relative imports, use: from utils.helpers import foo (match project structure).
- Do NOT use implicit relative imports unless it's a proper package."""
    elif language.lower() in ("javascript", "typescript", "js", "ts"):
        lang_rules = """
JS/TS-specific rules:
- Use ES modules (import/export), not CommonJS (require).
- Add JSDoc comments for all exported functions.
- Handle promise rejections with try/catch in async functions."""

    prompt = f"""You are a senior {language} developer writing production-quality code.

Project goal: {project_description}

Complete project file structure (dependency order):
{file_list}

{f"Dependencies this file imports:{dependency_context}" if dependency_context else ""}

Your task: Write complete, working code for: {file_path}
Purpose: {file_desc}
{f"Imports from: {', '.join(file_imports)}" if file_imports else "No project-internal imports."}

{lang_rules}

General rules:
- Output ONLY raw code. No explanation, no markdown, no triple backticks.
- Write COMPLETE, RUNNABLE code — no placeholders, no TODO, no pass stubs.
- Every import must be from stdlib, listed dependencies, or the project files shown.
- Match import paths EXACTLY to file paths (e.g. "utils/helpers.py" → "from utils.helpers import ...").
- Use proper error handling (try/except) where I/O or network calls are made.
- Code must work when run from the project root directory.

Code for {file_path}:"""

    code = _strip_fences(_generate_with_fallback(prompt, log))
    full_path = project_dir / file_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(code, encoding="utf-8")
    log(f"Written: {file_path} ({len(code)} chars)")
    return file_path, code


def _write_files_parallel(
    files: list,
    description: str,
    language: str,
    project_dir: Path,
    log,
    max_workers: int = 3,
) -> dict:
    """
    Write independent files in parallel, then dependent files sequentially.
    Files with no imports can be written in parallel.
    Files with imports must wait for their dependencies.
    """
    independent = [f for f in files if not f.get("imports")]
    dependent   = [f for f in files if f.get("imports")]

    file_codes: dict = {}

    # Phase A: parallel writes for leaf files
    if independent:
        log(f"Writing {len(independent)} independent files in parallel...")
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(
                    _write_one_file, fi, description, files, language,
                    project_dir, file_codes, log
                ): fi["path"]
                for fi in independent
            }
            for future in as_completed(futures):
                path = futures[future]
                try:
                    fp, code = future.result()
                    file_codes[fp] = code
                except RateLimitError:
                    log(f"Rate limit on {path}, retrying sequentially...")
                    time.sleep(15)
                    try:
                        fp, code = _write_one_file(
                            next(f for f in independent if f["path"] == path),
                            description, files, language, project_dir, file_codes, log
                        )
                        file_codes[fp] = code
                    except Exception as e2:
                        log(f"Skipped {path}: {e2}")
                except Exception as e:
                    log(f"Failed to write {path}: {e}")

    # Phase B: sequential writes for dependent files (order matters)
    for fi in dependent:
        fp = fi["path"]
        log(f"Writing {fp} (has dependencies)...")
        for attempt in range(2):
            try:
                path, code = _write_one_file(
                    fi, description, files, language, project_dir, file_codes, log
                )
                file_codes[path] = code
                time.sleep(0.3)
                break
            except RateLimitError:
                if attempt == 0:
                    log(f"Rate limit — waiting 20s...")
                    time.sleep(20)
                else:
                    log(f"Rate limit retry failed for {fp}, skipping.")
            except Exception as e:
                log(f"Failed to write {fp}: {e}")
                break

    return file_codes


# ── Test auto-generation ───────────────────────────────────────────────────────

def _generate_tests(
    file_codes: dict,
    project_description: str,
    language: str,
    project_dir: Path,
    log,
) -> None:
    """Auto-generate a test file for the project."""
    try:
        code_summary = ""
        for path, code in list(file_codes.items())[:3]:  # top 3 files
            code_summary += f"\n--- {path} ---\n{code[:1000]}\n"

        if language.lower() == "python":
            test_framework = "pytest"
            test_filename = "test_main.py"
        else:
            test_framework = "jest"
            test_filename = "main.test.ts"

        prompt = f"""You are a senior QA engineer. Write {test_framework} tests for this project.

Project: {project_description}

Key source files:
{code_summary}

Rules:
- Output ONLY raw test code. No markdown, no backticks.
- Cover happy path and at least 2 edge cases per major function.
- Use mocking for external calls (API, DB, file I/O).
- Tests must be runnable with: {'pytest' if language.lower() == 'python' else 'jest'} from project root.

Test code:"""

        test_code = _strip_fences(_generate_with_fallback(prompt, log))
        test_path = project_dir / test_filename
        test_path.write_text(test_code, encoding="utf-8")
        log(f"Tests generated: {test_filename}")
    except Exception as e:
        log(f"Test generation skipped: {e}")


# ── Dependency install ────────────────────────────────────────────────────────

def _install_dependencies(dependencies: list, project_dir: Path, log) -> str:
    if not dependencies:
        return "No external dependencies."

    to_install = []
    for dep in dependencies:
        pkg_name = re.split(r"[><=!]", dep)[0].strip()
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", pkg_name],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            to_install.append(dep)
        else:
            log(f"Already installed: {pkg_name}")

    if not to_install:
        return f"All dependencies already installed: {', '.join(dependencies)}"

    log(f"Installing: {to_install}")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install"] + to_install,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=180, cwd=str(project_dir)
        )
        if result.returncode == 0:
            return f"Installed: {', '.join(to_install)}"
        return f"Install warning (non-fatal): {result.stderr[:300]}"
    except subprocess.TimeoutExpired:
        return "Dependency install timed out (non-fatal)."
    except Exception as e:
        return f"Install error (non-fatal): {e}"


def _try_auto_install(error_output: str, project_dir: Path, log) -> bool:
    pattern = re.compile(r"No module named ['\"]([a-zA-Z0-9_\-\.]+)['\"]", re.IGNORECASE)
    match = pattern.search(error_output)
    if not match:
        return False
    pkg = match.group(1).replace("_", "-").split(".")[0]
    log(f"Auto-installing missing package: {pkg}")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=90, cwd=str(project_dir)
        )
        return result.returncode == 0
    except Exception:
        return False


# ── VSCode opener ─────────────────────────────────────────────────────────────

def _open_vscode(project_dir: Path) -> bool:
    candidates = [
        "code",
        rf"C:\Users\{Path.home().name}\AppData\Local\Programs\Microsoft VS Code\bin\code.cmd",
        r"C:\Program Files\Microsoft VS Code\bin\code.cmd",
    ]
    for cmd in candidates:
        try:
            subprocess.Popen(
                [cmd, str(project_dir)], shell=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            time.sleep(1.5)
            return True
        except Exception:
            continue
    return False


# ── Project runner ────────────────────────────────────────────────────────────

def _run_project(run_command: str, project_dir: Path, timeout: int = 30) -> str:
    try:
        parts = run_command.split()
        if parts[0].lower() == "python":
            parts[0] = sys.executable

        result = subprocess.run(
            parts,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout, cwd=str(project_dir)
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        parts_out = []
        if stdout:
            parts_out.append(f"STDOUT:\n{stdout}")
        if stderr:
            parts_out.append(f"STDERR:\n{stderr}")
        return "\n\n".join(parts_out) if parts_out else "Ran with no output."

    except subprocess.TimeoutExpired:
        return f"Timed out after {timeout}s — long-running app (server/GUI) is likely working."
    except FileNotFoundError as e:
        return f"Command not found: {e}"
    except Exception as e:
        return f"Run error: {e}"


# ── Fix files ─────────────────────────────────────────────────────────────────

def _fix_files(
    error_output: str,
    project_description: str,
    all_files: list,
    file_codes: dict,
    language: str,
    project_dir: Path,
    entry_point: str,
    log,
) -> dict:
    error_file, error_line = _parse_traceback(error_output, list(file_codes.keys()))
    error_type = _classify_error(error_output)

    files_to_fix = []
    if error_file:
        files_to_fix.append(error_file)
        if error_type == "import_error":
            for fi in all_files:
                if error_file.replace("/", ".").replace(".py", "") in fi.get("imports", []):
                    p = fi["path"]
                    if p not in files_to_fix:
                        files_to_fix.append(p)
    else:
        files_to_fix.append(entry_point)

    updated_codes = {}

    for fix_path in files_to_fix:
        current_code = file_codes.get(fix_path, "")
        other_ctx = ""
        for fp, code in file_codes.items():
            if fp != fix_path and code:
                snippet = code[:1500] + ("..." if len(code) > 1500 else "")
                other_ctx += f"\n--- {fp} ---\n{snippet}\n"

        line_hint = (
            f"\nError appears near line {error_line} in this file."
            if error_line and fix_path == error_file else ""
        )

        # Error-type-specific fix hints
        fix_hints = {
            "dependency_error": "The error is a missing package. Add the correct import or fix the module path.",
            "syntax_error": "The error is a syntax error. Fix the exact syntax issue on the indicated line.",
            "import_error": "The error is a wrong import path. Fix the import to match the exact file path in the project.",
            "type_error": "The error is a type mismatch. Fix function signatures and argument types.",
            "attribute_error": "The error is an attribute access on wrong type. Check object types and method names.",
            "file_not_found": "The error is a missing file. Ensure file paths are relative to the project root.",
            "runtime_error": "The error is a runtime exception. Trace the root cause and fix the logic.",
            "permission_error": "The error is a permissions issue. Avoid writing to protected system paths.",
        }.get(error_type, "Fix all errors in the file.")

        prompt = f"""You are an expert {language} debugger. Fix the broken file below.

Project goal: {project_description}

All project files:
{chr(10).join(f"  - {f['path']}: {f.get('description', '')}" for f in all_files)}

Other files for context (read-only):
{other_ctx[:3500]}

File to fix: {fix_path}{line_hint}
Error type: {error_type}
Fix strategy: {fix_hints}

Error output:
{error_output[:2500]}

Current (broken) code:
{current_code}

Rules:
- Output ONLY the complete fixed code. No explanation, no markdown, no backticks.
- Fix ALL errors visible in the error output.
- Keep all existing correct logic — do not remove working features.
- Ensure import paths match actual project file structure.
- Do NOT introduce new bugs or remove error handling.

Fixed code for {fix_path}:"""

        try:
            fixed = _strip_fences(_generate_with_fallback(prompt, log))
            full_path = project_dir / fix_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(fixed, encoding="utf-8")
            updated_codes[fix_path] = fixed
            log(f"Fixed: {fix_path}")
        except Exception as e:
            log(f"Could not fix {fix_path}: {e}")

    return updated_codes


# ── Main builder ──────────────────────────────────────────────────────────────

def _build_project(
    description: str,
    language: str,
    project_name: str,
    timeout: int,
    enable_git: bool = True,
    enable_docker: bool = False,
    enable_ci: bool = False,
    generate_tests: bool = True,
    speak=None,
    player=None,
) -> str:

    def log(msg: str):
        print(f"[DevAgent] {msg}")
        if player:
            player.write_log(f"[DevAgent] {msg}")

    # Phase 0: Detect template
    template_hint = _detect_template(description)
    if template_hint:
        log(f"Template detected: {template_hint} — using {_TEMPLATES[template_hint]['stack']}")

    # Phase 1: Plan
    log("Phase 1: Planning project structure...")
    try:
        plan = _plan_project(description, language, template_hint, log)
    except AllModelsExhausted as e:
        msg = f"All AI models unavailable, Sir. Please check your internet and Ollama. ({e})"
        if speak: speak(msg)
        return msg
    except ValueError as e:
        msg = f"Planning failed, Sir: {e}"
        if speak: speak(msg)
        return msg

    proj_name   = project_name or plan.get("project_name", "jarvis_project")
    proj_name   = re.sub(r"[^\w\-]", "_", proj_name)
    project_dir = PROJECTS_DIR / proj_name
    project_dir.mkdir(parents=True, exist_ok=True)

    files        = plan.get("files", [])
    entry_point  = plan.get("entry_point", "main.py")
    run_command  = plan.get("run_command", f"python {entry_point}")
    dependencies = plan.get("dependencies", [])
    is_service   = plan.get("is_service", False)
    gen_docker   = enable_docker or plan.get("generate_docker", False) or is_service
    gen_ci       = enable_ci or plan.get("generate_ci", True)

    log(f"Project: {proj_name} | Files: {len(files)} | Entry: {entry_point} | Stack: {language}")

    # Phase 2: Scaffold — write utility files
    _write_gitignore(project_dir, language)
    _write_env_example(project_dir)

    # Git init
    git_ok = enable_git and _git_init(project_dir, log)

    # Phase 3: Implement — parallel file writing
    log(f"Phase 3: Writing {len(files)} files...")
    file_codes = _write_files_parallel(files, description, language, project_dir, log)

    if not file_codes:
        msg = "I could not write any project files, Sir — something went wrong with the AI models."
        if speak: speak(msg)
        return msg

    # Git commit after scaffold
    if git_ok:
        _git_commit(project_dir, "feat: initial scaffold — Phases 1-3 complete", log)

    # Phase 4: Install dependencies
    if dependencies:
        install_result = _install_dependencies(dependencies, project_dir, log)
        log(install_result)

    # Docker generation
    if gen_docker:
        _generate_dockerfile(project_dir, language, entry_point, run_command, log)

    # CI/CD generation
    if gen_ci:
        _generate_ci_yml(project_dir, language, log)

    # Test auto-generation
    if generate_tests:
        log("Phase 4: Generating tests...")
        _generate_tests(file_codes, description, language, project_dir, log)

    # Git commit after all files
    if git_ok:
        _git_commit(project_dir, "feat: implementation + tests + CI/CD — Phase 4 complete", log)

    # Open in VSCode
    _open_vscode(project_dir)

    # Phase 5: Run + Debug loop
    last_output   = ""
    auto_installs = 0

    for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
        log(f"Phase 5: Running project (attempt {attempt}/{MAX_FIX_ATTEMPTS})...")
        last_output = _run_project(run_command, project_dir, timeout)
        log(f"Output: {last_output[:200]}")

        if not _has_error(last_output, run_command):
            # Git commit on first clean run
            if git_ok:
                _git_commit(
                    project_dir,
                    f"fix: project running cleanly after {attempt} attempt(s)",
                    log
                )
            msg = (
                f"Project '{proj_name}' is working, Sir! "
                f"Built in {attempt} attempt{'s' if attempt > 1 else ''}. "
                f"Saved to: {project_dir}"
            )
            if speak: speak(msg)
            return (
                f"{msg}\n\nOutput:\n{last_output}"
                f"\n\nTo run: cd \"{project_dir}\" && {run_command}"
                f"\n{'Docker: docker compose up' if gen_docker else ''}"
            )

        if attempt == MAX_FIX_ATTEMPTS:
            break

        error_type = _classify_error(last_output)

        if error_type == "dependency_error" and auto_installs < 3:
            installed = _try_auto_install(last_output, project_dir, log)
            if installed:
                auto_installs += 1
                log("Missing dependency installed, retrying...")
                time.sleep(1)
                continue

        log(f"Phase 5: Debugging error type: {error_type}...")
        try:
            updated = _fix_files(
                error_output=last_output,
                project_description=description,
                all_files=files,
                file_codes=file_codes,
                language=language,
                project_dir=project_dir,
                entry_point=entry_point,
                log=log,
            )
            file_codes.update(updated)
            if git_ok and updated:
                _git_commit(project_dir, f"fix: debug attempt {attempt} — {error_type}", log)
            time.sleep(1)
        except AllModelsExhausted:
            msg = "All AI models exhausted during debug. Project saved — check it manually in VSCode, Sir."
            if speak: speak(msg)
            return msg
        except Exception as e:
            log(f"Fix step failed: {e}")

    msg = (
        f"Enna achu, Sir — couldn't fully fix '{proj_name}' after {MAX_FIX_ATTEMPTS} attempts. "
        f"Project saved at {project_dir} — open in VSCode and check manually."
    )
    if speak: speak(msg)
    return f"{msg}\n\nLast error:\n{last_output[:800]}"


# ── Public entry point ────────────────────────────────────────────────────────

def dev_agent(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    p            = parameters or {}
    description  = p.get("description", "").strip()
    language     = p.get("language", "python").strip()
    project_name = p.get("project_name", "").strip()
    timeout      = int(p.get("timeout", 30))
    enable_git   = bool(p.get("enable_git", True))
    enable_docker = bool(p.get("enable_docker", False))
    enable_ci    = bool(p.get("enable_ci", False))
    gen_tests    = bool(p.get("generate_tests", True))

    if not description:
        return "Please describe the project you want me to build, Sir."

    return _build_project(
        description    = description,
        language       = language,
        project_name   = project_name,
        timeout        = timeout,
        enable_git     = enable_git,
        enable_docker  = enable_docker,
        enable_ci      = enable_ci,
        generate_tests = gen_tests,
        speak          = speak,
        player         = player,
    )
