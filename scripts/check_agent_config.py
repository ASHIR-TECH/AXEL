"""Validate selected Qwen/Groq agent configuration without contacting a provider."""

from axel.agents.runtime import validate_agent_configuration


def main() -> int:
    try:
        print(f"[PASS] {validate_agent_configuration()}")
    except ValueError as error:
        print(f"[FAIL] Agent configuration invalid: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
