"""Build the synthetic transcript used by the tests and the demo.

    python3 tests/fixtures/build.py           # writes examples/auth-refactor.jsonl

The session is invented: a user refactoring auth in a web app, with a few explicit
rules, edits, a failing test, open tasks, and one compaction whose summary keeps
some of that state and silently drops the rest.
"""

from __future__ import annotations

import json
import os
import sys

CWD = "/home/dev/webapp"

SUMMARY = """This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation.

Summary:
1. Primary Request and Intent:
   The user asked to refactor authentication in the web app to OAuth2.
2. Key Technical Concepts:
   - OAuth2 authorization code flow with PKCE. Going with PKCE flow instead of implicit grant.
   - pnpm is the package manager; use pnpm, not npm.
3. Files and Code Sections:
   - src/auth.ts: new OAuth2 client, token exchange and refresh.
   - src/login.tsx: login button now starts the OAuth redirect.
4. Errors and fixes:
   - A lint error in src/auth.ts was fixed.
5. Pending Tasks:
   - Update the README auth section.
6. Current Work:
   Working on the auth tests.
"""


def _u(uuid, text, **extra):
    return {"type": "user", "uuid": uuid, "cwd": CWD, "sessionId": "demo-session", "message": {"role": "user", "content": text}, **extra}


def _a_text(uuid, text):
    return {"type": "assistant", "uuid": uuid, "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _a_tool(uuid, tid, name, inp):
    return {"type": "assistant", "uuid": uuid, "message": {"role": "assistant", "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def _result(uuid, tid, text, is_error=False):
    return {
        "type": "user",
        "uuid": uuid,
        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": is_error}]},
    }


def entries() -> list[dict]:
    e: list[dict] = []
    e.append(_u("u1", "Refactor our auth to OAuth2. Never push to main directly; open a PR instead. Use pnpm, not npm. Don't touch anything in the legacy/ folder, it is frozen for the audit."))
    e.append({"type": "user", "uuid": "m1", "isMeta": True, "message": {"role": "user", "content": "<command-name>/model</command-name>"}})
    e.append(_a_text("a1", "I'll start by reading the current auth module. Going with PKCE flow instead of implicit grant, since this is a public client."))
    e.append(_a_tool("a2", "t1", "TaskCreate", {"subject": "Replace session cookies with OAuth2 tokens", "description": "", "activeForm": ""}))
    e.append(_result("r1", "t1", "Task #1 created successfully: Replace session cookies with OAuth2 tokens"))
    e.append(_a_tool("a3", "t2", "TaskCreate", {"subject": "Update the README auth section", "description": "", "activeForm": ""}))
    e.append(_result("r2", "t2", "Task #2 created successfully: Update the README auth section"))
    e.append(_a_tool("a4", "t3", "TaskCreate", {"subject": "Add refresh-token rotation to the API gateway", "description": "", "activeForm": ""}))
    e.append(_result("r3", "t3", "Task #3 created successfully: Add refresh-token rotation to the API gateway"))
    e.append(_a_tool("a5", "t4", "Write", {"file_path": f"{CWD}/src/auth.ts", "content": "export {}"}))
    e.append(_result("r4", "t4", "File created successfully"))
    e.append(_a_tool("a6", "t5", "Edit", {"file_path": f"{CWD}/src/login.tsx", "old_string": "a", "new_string": "b"}))
    e.append(_result("r5", "t5", "The file has been updated"))
    e.append(_a_tool("a7", "t6", "Edit", {"file_path": f"{CWD}/src/middleware/session.ts", "old_string": "a", "new_string": "b"}))
    e.append(_result("r6", "t6", "The file has been updated"))
    e.append(_a_tool("a8", "t7", "Bash", {"command": "pnpm lint", "description": "lint"}))
    e.append(_result("r7", "t7", "src/auth.ts:4:1 error: 'x' is defined but never used\nExit code 1", is_error=True))
    e.append(_a_tool("a9", "t8", "Edit", {"file_path": f"{CWD}/src/auth.ts", "old_string": "x", "new_string": ""}))
    e.append(_result("r8", "t8", "The file has been updated"))
    e.append(_a_tool("a10", "t9", "Bash", {"command": "pnpm lint", "description": "lint"}))
    e.append(_result("r9", "t9", "No lint errors"))
    e.append(_a_tool("a11", "t10", "TaskUpdate", {"taskId": "1", "status": "completed"}))
    e.append(_result("r10", "t10", "Updated task #1 status"))
    e.append(_u("u2", "Also, keep the old /api/v1/login endpoint working until the mobile app ships 4.2; it must return a deprecation header."))
    e.append(_a_tool("a12", "t11", "Write", {"file_path": f"{CWD}/tests/auth.test.ts", "content": "test()"}))
    e.append(_result("r11", "t11", "File created successfully"))
    e.append(_a_tool("a13", "t12", "Bash", {"command": "pnpm test tests/auth.test.ts", "description": "run tests"}))
    e.append(
        _result(
            "r12",
            "t12",
            " FAIL  tests/auth.test.ts\n  ● refresh › rotates token\n    TypeError: Cannot read properties of undefined (reading 'refreshToken')\n      at refresh (src/auth.ts:42:18)\nExit code 1",
            is_error=True,
        )
    )
    e.append(_a_text("a14", "The refresh test fails because the token store is not initialised in tests; I'll mock it next."))
    e.append(
        {
            "type": "system",
            "subtype": "compact_boundary",
            "uuid": "b1",
            "content": "Conversation compacted",
            "compactMetadata": {"trigger": "manual", "preTokens": 182000, "postTokens": 9100},
        }
    )
    e.append(_u("s1", SUMMARY, isCompactSummary=True))
    e.append(_u("u3", "ok continue"))
    return e


def write(path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for entry in entries():
            fh.write(json.dumps(entry) + "\n")
    return path


def pre_compact_entries() -> list[dict]:
    """The transcript as it looks when PreCompact fires (before the boundary)."""
    out = []
    for entry in entries():
        if entry.get("subtype") == "compact_boundary":
            break
        out.append(entry)
    return out


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(root, "examples", "auth-refactor.jsonl")
    print(write(target))
