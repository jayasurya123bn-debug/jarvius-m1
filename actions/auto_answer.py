"""
actions/auto_answer.py
═══════════════════════════════════════════════════════════════
AUTO-ANSWER & PLAYWRIGHT FORM/QUIZ SOLVER FOR JARVIS
═══════════════════════════════════════════════════════════════
Autonomous capability to:
1. Scan page / form / quiz and read question text and fields
2. Search scratchpad / local knowledge / web
3. Compose the best answer
4. Fill fields (text, textarea, dropdowns, radios, checkboxes, file uploads)
5. Submit only if user explicitly approves (Permission Rule)
6. Log all Q&A to scratchpad with timestamp & verification screenshots
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from playwright.async_api import Page, Locator
except ImportError:
    Page = Any
    Locator = Any

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent
AUTOMATION_DIR = BASE_DIR / "automation"
SCREENSHOTS_DIR = AUTOMATION_DIR / "screenshots"
SCRATCHPAD_PATH = AUTOMATION_DIR / "scratchpad.md"
ROOT_SCRATCHPAD_PATH = BASE_DIR.parent / "scratchpad.md"


def _ensure_dirs() -> None:
    AUTOMATION_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def _append_to_scratchpad(entry_md: str) -> None:
    """Appends an entry to both automation/scratchpad.md and workspace root scratchpad.md."""
    _ensure_dirs()
    for path in (SCRATCHPAD_PATH, ROOT_SCRATCHPAD_PATH):
        try:
            if not path.exists():
                header = (
                    "# 📝 JARVIS Automation Scratchpad & Q/A Log\n"
                    "=====================================================\n"
                    "*Autonomous Execution & Q/A Logging Subsystem*\n\n"
                    "## Q/A Knowledge Store & Extraction History\n\n"
                )
                path.write_text(header, encoding="utf-8")
            
            with open(path, "a", encoding="utf-8") as f:
                f.write(entry_md + "\n")
        except Exception as e:
            print(f"[AutoAnswer] Error appending to scratchpad ({path}): {e}")


def search_scratchpad_knowledge(query: str) -> Optional[str]:
    """Search scratchpad and local knowledge for relevant answers."""
    query_lower = query.lower().strip()
    words = [w for w in re.split(r"\W+", query_lower) if len(w) > 2]
    
    for path in (SCRATCHPAD_PATH, ROOT_SCRATCHPAD_PATH):
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            # Look for Q&A patterns in markdown
            blocks = re.findall(r"(?:###|####|\*\*Q:|\*\*Question:)(.+?)(?=(?:###|####|\*\*Q:|\*\*Question:|\Z))", content, re.DOTALL | re.IGNORECASE)
            for block in blocks:
                score = sum(1 for w in words if w in block.lower())
                if score >= max(1, len(words) // 2):
                    # Extract answer part
                    ans_match = re.search(r"(?:Answer|A|Result):\s*([^\n]+)", block, re.IGNORECASE)
                    if ans_match:
                        return ans_match.group(1).strip()
                    return block.strip()
        except Exception:
            pass
    return None


def search_web_knowledge(query: str) -> Optional[str]:
    """Fallback search using JARVIS web_search."""
    try:
        from actions.web_search import web_search
        res = web_search({"query": query, "mode": "search"}, player=None)
        if res and not res.startswith("No results") and not res.startswith("Search failed"):
            return res.strip()
    except Exception as e:
        print(f"[AutoAnswer] Web search fallback note: {e}")
    return None


def compose_best_answer(question: str, options: Optional[List[str]] = None, context: str = "") -> str:
    """Compose the best answer using local scratchpad, web fallback, or deduction."""
    question_clean = question.strip()
    
    # 1. Search scratchpad first
    local_hit = search_scratchpad_knowledge(question_clean)
    if local_hit:
        if options:
            for opt in options:
                if opt.lower() in local_hit.lower() or local_hit.lower() in opt.lower():
                    return opt
        return local_hit

    # 2. Search web if available
    web_hit = search_web_knowledge(question_clean)
    if web_hit:
        if options:
            for opt in options:
                if opt.lower() in web_hit.lower():
                    return opt
            return options[0]
        # Return summary sentence
        first_sentence = web_hit.split(".")[0]
        return first_sentence.strip()

    # 3. Multiple choice fallback heuristic
    if options and len(options) > 0:
        return options[0]

    return "Affirmative / Auto-generated answer."


async def extract_form_elements(page: Page) -> List[Dict[str, Any]]:
    """Scan and detect all input, select, textarea, radio, checkbox, and upload fields on the page."""
    elements = []
    
    # JS evaluation for deep extraction of form fields with human labels and types
    js_extract = """
    () => {
        const results = [];
        const inputs = Array.from(document.querySelectorAll('input, textarea, select'));
        
        inputs.forEach((el, index) => {
            if (el.type === 'hidden') return;
            
            // Find label
            let label = '';
            if (el.id) {
                const l = document.querySelector(`label[for="${el.id}"]`);
                if (l) label = l.innerText.trim();
            }
            if (!label && el.closest('label')) {
                label = el.closest('label').innerText.trim();
            }
            if (!label) {
                label = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || el.id || '';
            }
            
            // Surrounding question context
            let questionContext = '';
            const container = el.closest('.question, .quiz-question, fieldset, tr, .form-group, div');
            if (container) {
                const header = container.querySelector('h1, h2, h3, h4, h5, legend, p, strong');
                if (header) questionContext = header.innerText.trim();
            }
            
            const fieldType = el.tagName.toLowerCase() === 'textarea' ? 'textarea' :
                              el.tagName.toLowerCase() === 'select' ? 'select' :
                              (el.type || 'text').toLowerCase();
            
            let options = [];
            if (fieldType === 'select') {
                options = Array.from(el.querySelectorAll('option')).map(o => ({
                    text: o.innerText.trim(),
                    value: o.value
                }));
            }
            
            results.push({
                index: index,
                tag: el.tagName.toLowerCase(),
                type: fieldType,
                name: el.name || '',
                id: el.id || '',
                label: label,
                questionContext: questionContext,
                placeholder: el.placeholder || '',
                value: el.value || '',
                required: el.required || false,
                options: options
            });
        });
        return results;
    }
    """
    try:
        raw_elements = await page.evaluate(js_extract)
        return raw_elements
    except Exception as e:
        print(f"[AutoAnswer] Error extracting form elements: {e}")
        return []


async def fill_page_field(page: Page, field: Dict[str, Any], answer: str) -> bool:
    """Fills a single field based on its detected type using Playwright."""
    try:
        selector = ""
        if field.get("id"):
            selector = f"#{field['id']}"
        elif field.get("name"):
            selector = f"{field['tag']}[name='{field['name']}']"
        elif field.get("placeholder"):
            selector = f"{field['tag']}[placeholder*='{field['placeholder']}']"
        
        if not selector:
            return False

        loc = page.locator(selector).first
        if await loc.count() == 0:
            return False

        ftype = field.get("type", "text")
        
        if ftype in ("text", "email", "number", "search", "password", "url", "tel", "textarea"):
            await loc.clear()
            await loc.fill(str(answer))
            return True
            
        elif ftype == "select":
            # Match option by label or value
            try:
                await loc.select_option(label=str(answer), timeout=3000)
                return True
            except Exception:
                try:
                    await loc.select_option(value=str(answer), timeout=3000)
                    return True
                except Exception:
                    options = field.get("options", [])
                    if options:
                        await loc.select_option(value=options[0]["value"])
                        return True
                        
        elif ftype == "radio":
            # Check the radio button
            await loc.check()
            return True
            
        elif ftype == "checkbox":
            if str(answer).lower() in ("true", "yes", "1", "check", "on", "sure"):
                await loc.check()
            else:
                await loc.uncheck()
            return True
            
        elif ftype == "file":
            if os.path.exists(str(answer)):
                await loc.set_input_files(str(answer))
                return True

        return False
    except Exception as e:
        print(f"[AutoAnswer] Error filling field {field}: {e}")
        return False


async def capture_verification_screenshot(page: Page, name_prefix: str = "qa_verify") -> str:
    """Takes a full page or viewport screenshot and stores it in screenshots dir."""
    _ensure_dirs()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{name_prefix}_{timestamp}.png"
    filepath = SCREENSHOTS_DIR / filename
    try:
        await page.screenshot(path=str(filepath), full_page=False)
        return str(filepath)
    except Exception as e:
        print(f"[AutoAnswer] Screenshot capture failed: {e}")
        return ""


def run_auto_answer_workflow(
    url: Optional[str] = None,
    provided_answers: Optional[Dict[str, str]] = None,
    allow_submit: bool = False,
    browser_name: str = "chrome",
    player: Any = None
) -> Dict[str, Any]:
    """
    Full Phase 0 - Phase 6 Auto-Answer workflow:
    1. Attach or navigate to target page
    2. Extract questions and form fields
    3. Retrieve answers from scratchpad / web
    4. Fill fields with Playwright
    5. Handle permission-gated submission
    6. Capture screenshot and log to scratchpad
    """
    from actions.browser_control import _registry, _normalize_url
    
    provided_answers = provided_answers or {}
    report = {
        "status": "started",
        "url": url,
        "questions_found": 0,
        "fields_filled": 0,
        "answers": {},
        "screenshot": "",
        "submitted": False,
        "permission_required": False,
        "permission_prompt": "",
        "errors": []
    }

    try:
        sess = _registry.get(browser_name)
    except Exception as e:
        report["status"] = "error"
        report["errors"].append(f"Could not initialize browser session: {e}")
        return report

    async def _async_action() -> Dict[str, Any]:
        page = await sess._get_page()
        if url:
            norm_url = _normalize_url(url)
            await page.goto(norm_url, wait_until="networkidle", timeout=30000)

        current_url = page.url
        report["url"] = current_url
        
        # Phase 2 & 3: Scan form elements & questions
        fields = await extract_form_elements(page)
        report["questions_found"] = len(fields)
        
        if not fields:
            report["status"] = "no_fields_found"
            return report

        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        qa_log_entries = []

        # Phase 4: Fill fields
        for f in fields:
            q_label = f.get("label") or f.get("questionContext") or f.get("name") or f"Field_{f['index']}"
            f_key = f.get("name") or f.get("id") or q_label
            
            # Use provided answer or compose
            if f_key in provided_answers:
                answer = provided_answers[f_key]
                source = "user_override"
            elif q_label in provided_answers:
                answer = provided_answers[q_label]
                source = "user_override"
            else:
                opt_texts = [o["text"] for o in f.get("options", [])] if f.get("options") else None
                answer = compose_best_answer(q_label, options=opt_texts)
                source = "auto_scratchpad_web"

            filled_ok = await fill_page_field(page, f, answer)
            if filled_ok:
                report["fields_filled"] += 1
                report["answers"][q_label] = answer
                
                qa_log_entries.append(
                    f"| {timestamp_str} | Q: {q_label} | A: {answer} | Source: {source} | Status: FILLED |"
                )

        # Verification screenshot
        shot_path = await capture_verification_screenshot(page, "auto_answer_filled")
        report["screenshot"] = shot_path

        # Log Q&A to scratchpad
        if qa_log_entries:
            markdown_block = (
                f"\n### Auto-Answer Session [{timestamp_str}]\n"
                f"- **Target URL**: {current_url}\n"
                f"- **Fields Detected**: {len(fields)}\n"
                f"- **Fields Filled**: {report['fields_filled']}\n"
                f"- **Verification Screenshot**: `{shot_path}`\n\n"
                "| Timestamp | Question / Field | Answer | Source | Status |\n"
                "|-----------|------------------|--------|--------|--------|\n"
                + "\n".join(qa_log_entries) + "\n"
            )
            _append_to_scratchpad(markdown_block)

        # Phase 5: Submission check (Strict Permission Gate)
        if allow_submit:
            # Locate submit button
            submit_btn = page.locator("button[type='submit'], input[type='submit'], button:has-text('Submit'), button:has-text('Send'), button:has-text('Finish')").first
            if await submit_btn.count() > 0:
                await submit_btn.click()
                await page.wait_for_load_state("networkidle", timeout=10000)
                report["submitted"] = True
                post_shot = await capture_verification_screenshot(page, "auto_answer_submitted")
                report["post_submit_screenshot"] = post_shot
        else:
            report["permission_required"] = True
            report["permission_prompt"] = (
                f"⚠️ Permission kekuren: Submit form / quiz on {current_url}.\n"
                f"Reason: All {report['fields_filled']} fields filled. Awaiting approval to submit.\n"
                f"Allow? (yes / no / always)"
            )

        report["status"] = "success"
        return report

    try:
        final_res = sess.run(_async_action())
        return final_res
    except Exception as e:
        report["status"] = "error"
        report["errors"].append(f"Auto-answer execution failed: {e}")
        return report


def auto_answer(parameters: Dict[str, Any], player: Any = None, speak: Any = None) -> str:
    """
    Main dispatcher for JARVIS Auto-Answer capability.
    Accepts:
    - url: target web page / quiz / form URL
    - answers: optional dict of custom answers
    - allow_submit: bool (default False)
    - action: 'auto_answer' | 'scan' | 'submit'
    - browser: browser name (default 'chrome')
    """
    action = parameters.get("action", "auto_answer").lower()
    url = parameters.get("url", "")
    answers = parameters.get("answers", {})
    allow_submit = bool(parameters.get("allow_submit", False))
    browser_name = parameters.get("browser", "chrome")

    if player:
        player.write_log(f"[AutoAnswer] Initiating '{action}' for URL: {url or 'current tab'}")

    res = run_auto_answer_workflow(
        url=url,
        provided_answers=answers,
        allow_submit=allow_submit,
        browser_name=browser_name,
        player=player
    )

    if res.get("status") == "error":
        err_msg = "; ".join(res.get("errors", ["Unknown failure"]))
        out = (
            f"Auto-Answer failed, Sir.\n"
            f"Enna error: {err_msg}. Enga vandhuchu: Playwright form filler. Yen vandhuchu: Page interaction error."
        )
        if player:
            player.write_log(f"[AutoAnswer] ⚠️ {err_msg}")
        return out

    if res.get("permission_required"):
        prompt = res.get("permission_prompt", "")
        summary = (
            f"Form/Quiz filled successfully, Sir ({res['fields_filled']} fields completed).\n"
            f"Screenshot saved: {res.get('screenshot')}\n\n"
            f"{prompt}"
        )
        if player:
            player.write_log(f"[AutoAnswer] 🔒 Gated on permission: {res.get('screenshot')}")
        return summary

    if res.get("submitted"):
        msg = f"Form submitted and completed, Sir! All {res['fields_filled']} fields filled and verified."
        if player:
            player.write_log(f"[AutoAnswer] ✅ {msg}")
        return msg

    return f"Completed auto-answer scan: {res.get('fields_filled', 0)} fields filled, Sir."
