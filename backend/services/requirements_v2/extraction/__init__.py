"""
Requirements-v2 extraction, OFFLINE: the pinned criteria_extraction_v2 prompt, the AI-output contract and the
parser/post-processor that turns a model response into a requirements-v2 draft.

NOT WIRED: nothing here is imported by job creation, the criteria worker, the API or scoring; it makes no model call,
reads no database and activates no prompt. Fixture responses are the only input in this stage.
"""
from services.requirements_v2.extraction.parser import ExtractionResult, parse_response, process_ai_output
from services.requirements_v2.extraction.prompt import (
    EXTRACTION_CONFIG, PROMPT_CODE, PROMPT_SHA256, PROMPT_VERSION, PromptIntegrityError, build_messages,
    build_request, build_user_message, load_prompt,
)

__all__ = ["EXTRACTION_CONFIG", "ExtractionResult", "PROMPT_CODE", "PROMPT_SHA256", "PROMPT_VERSION",
           "PromptIntegrityError", "build_messages", "build_request", "build_user_message", "load_prompt",
           "parse_response", "process_ai_output"]
