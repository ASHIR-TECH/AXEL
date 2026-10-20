"""
Untrusted data sandboxing and prompt-injection defense for Phase 3 Agent Layer.

Security Rules:
1. All external text (news, social, filings, web) is UNTRUSTED DATA, never instructions.
2. External text is delimited in rigid isolation tags.
3. Common prompt-injection attack vectors (system prompt overrides, instruction injection,
   fake execution commands) are stripped or neutralized.
4. Cryptographic SHA-256 provenance is preserved for every document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256

INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+instructions", re.IGNORECASE),
    re.compile(r"system\s*prompt\s*override", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(a|an)\s+", re.IGNORECASE),
    re.compile(r"execute\s+(trade|order|buy|sell)\s+immediately", re.IGNORECASE),
    re.compile(r"transfer\s+(funds|capital|crypto|usd)", re.IGNORECASE),
    re.compile(r"bypass\s+(risk|guardrails|safety|limits)", re.IGNORECASE),
    re.compile(r"admin\s*mode\s*:\s*enabled", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
]


@dataclass(frozen=True)
class UntrustedDocument:
    """Document wrapper for external text."""

    source: str
    text: str
    published_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    author: str | None = None

    def reference(self) -> str:
        payload = f"{self.source}\0{self.text}"
        return sha256(payload.encode("utf-8")).hexdigest()


class PromptSanitizer:
    """
    Sanitizes untrusted text and wraps it in a rigid structural sandbox.
    """

    @classmethod
    def detect_injection_attempts(cls, text: str) -> list[str]:
        """Scan text for common injection signatures."""
        detected = []
        for pattern in INJECTION_PATTERNS:
            match = pattern.search(text)
            if match:
                detected.append(match.group(0))
        return detected

    @classmethod
    def sanitize(cls, document: UntrustedDocument) -> dict[str, str]:
        """
        Produce a sanitized dictionary payload safe for inclusion in LLM prompts.
        Neutralizes directive injection attempts and isolates content.
        """
        raw_text = document.text
        injections = cls.detect_injection_attempts(raw_text)

        cleaned_text = raw_text
        for pattern in INJECTION_PATTERNS:
            cleaned_text = pattern.sub("[REDACTED_ATTACK_DIRECTIVE]", cleaned_text)

        # Rigid structural isolation XML tags
        sandboxed_text = (
            "<untrusted_document_data>\n"
            f"<source>{document.source}</source>\n"
            f"<published_at>{document.published_at.isoformat()}</published_at>\n"
            f"<content_hash>{document.reference()}</content_hash>\n"
            f"<content>\n{cleaned_text}\n</content>\n"
            "</untrusted_document_data>"
        )

        return {
            "source": document.source,
            "content_hash": document.reference(),
            "untrusted_sandboxed_xml": sandboxed_text,
            "injections_detected_count": str(len(injections)),
            "is_flagged": "true" if injections else "false",
        }
