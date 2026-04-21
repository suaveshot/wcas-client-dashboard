"""
Managed Agents smoke test.

Proves the Activation Orchestrator pattern end-to-end against Anthropic's
Managed Agents beta before Day 3 ships the real activation agent:

  1. Create an agent with Opus 4.7 + a custom tool schema
  2. Create a cloud environment
  3. Start a session
  4. Stream events, handle user / tool_use / tool_result / status
  5. Verify the tool call fires and the agent completes
  6. Clean up

Usage:
  python scripts/smoke_managed_agent.py           # full lifecycle, ~$1 of credits
  python scripts/smoke_managed_agent.py --keep    # skip delete, inspect in console

Requires ANTHROPIC_API_KEY in the environment (or loads from an ../.env).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

def _load_api_key() -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    ap_env = Path("C:/Users/bball/OneDrive/Desktop/Claude/Americal Patrol/.env")
    if ap_env.exists():
        for line in ap_env.read_text(encoding="utf-8").splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip()
    print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
    sys.exit(2)


SYSTEM_PROMPT = """You are the WCAS Activation Orchestrator.
You are helping a newly-signed WestCoast Automation Solutions client
configure the pipelines they purchased. For this smoke test, simply:

1. Greet the user briefly.
2. Call the `confirm_company_name` tool with the company name "Americal Patrol".
3. Report that setup is complete and finish.

Keep responses short. No em dashes."""


CONFIRM_COMPANY_NAME_TOOL = {
    "type": "custom",
    "name": "confirm_company_name",
    "description": "Record the client's company name in their tenant config.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The confirmed legal or trade name.",
            },
        },
        "required": ["name"],
    },
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="Don't delete resources after run")
    args = parser.parse_args()

    os.environ["ANTHROPIC_API_KEY"] = _load_api_key()

    import anthropic  # imported here so the script can print a cleaner error first

    client = anthropic.Anthropic()
    print(f"SDK: {anthropic.__version__}")

    print("\n== create agent ==")
    agent = client.beta.agents.create(
        name="wcas-smoke-activation",
        model="claude-opus-4-7",
        system=SYSTEM_PROMPT,
        tools=[
            {"type": "agent_toolset_20260401"},
            CONFIRM_COMPANY_NAME_TOOL,
        ],
    )
    print(f"agent.id={agent.id} version={agent.version}")

    print("\n== create environment ==")
    env = client.beta.environments.create(
        name="wcas-smoke-env",
        config={"type": "cloud", "networking": {"type": "unrestricted"}},
    )
    print(f"environment.id={env.id}")

    print("\n== create session ==")
    session = client.beta.sessions.create(
        agent=agent.id,
        environment_id=env.id,
        title="smoke test",
    )
    print(f"session.id={session.id}")

    print("\n== stream events ==")
    # Fresh sessions emit status_idle immediately because they have no work.
    # After we send a user message the session transitions idle -> active
    # (or similar) and emits agent events, then status_idle again when done.
    # So: skip the FIRST status_idle; break on the SECOND.
    tool_calls_seen = 0
    text_seen = []
    idle_count = 0
    done = False

    with client.beta.sessions.events.stream(session.id) as stream:
        client.beta.sessions.events.send(
            session.id,
            events=[
                {
                    "type": "user.message",
                    "content": [
                        {"type": "text", "text": "Hi, confirm my company and finish."}
                    ],
                }
            ],
        )

        for event in stream:
            t = getattr(event, "type", None)
            if t == "agent.message":
                for block in getattr(event, "content", []) or []:
                    if getattr(block, "type", None) == "text":
                        text_seen.append(block.text)
                        print(block.text, end="", flush=True)
            elif t == "agent.tool_use":
                tool_calls_seen += 1
                name = getattr(event, "name", "?")
                print(f"\n[tool_use: {name}]")
            elif t == "session.status_idle":
                idle_count += 1
                if idle_count >= 2:
                    print("\n[session idle - done]")
                    done = True
                    break
            elif t and t.startswith("session.status_"):
                pass  # progress ticks

    print("\n== results ==")
    print(f"tool_calls_seen: {tool_calls_seen}")
    print(f"text blocks:     {len(text_seen)}")
    print(f"completed:       {done}")

    if not args.keep:
        print("\n== cleanup ==")
        # Note: agents use `archive` (versioned resource), envs/sessions use `delete`.
        try:
            client.beta.sessions.delete(session.id)
            print(f"session {session.id} deleted")
        except Exception as e:
            print(f"session delete: {type(e).__name__}: {e}")
        try:
            client.beta.environments.delete(env.id)
            print(f"environment {env.id} deleted")
        except Exception as e:
            print(f"environment delete: {type(e).__name__}: {e}")
        try:
            client.beta.agents.archive(agent.id)
            print(f"agent {agent.id} archived")
        except Exception as e:
            print(f"agent archive: {type(e).__name__}: {e}")
    else:
        print(f"\n[--keep] resources preserved: agent={agent.id} env={env.id} session={session.id}")

    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
