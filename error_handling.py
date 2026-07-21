import traceback


def classify_error(e: Exception) -> dict:
    """
    Error classifier.
    """
    message = str(e)
    status_code = getattr(e, "status_code", None) or getattr(e, "code", None)

    lowered = message.lower()

    if status_code == 429 or "rate limit" in lowered or "resourceexhausted" in lowered or "too many requests" in lowered:
        category = "RATE_LIMITED"
        user_message = "The AI service is currently busy. Please try again in a moment."

    elif "maximum context length" in lowered or "context_length_exceeded" in lowered or ("token" in lowered and "exceed" in lowered):
        category = "CONTEXT_LENGTH_EXCEEDED"
        user_message = "This conversation has gotten too long to process. Please start a new chat."

    elif status_code in (401, 403) or "unauthorized" in lowered or "invalid api key" in lowered or "authentication" in lowered:
        category = "AUTH_ERROR"
        user_message = "There's an authentication issue with the AI service. Please check the server configuration."

    elif status_code in (500, 502, 503, 504) or "upstream error" in lowered or "bad gateway" in lowered or "service unavailable" in lowered:
        category = "PROVIDER_UNAVAILABLE"
        user_message = "The AI service is temporarily unavailable. Please try again shortly."

    elif "timeout" in lowered or "connection" in lowered:
        category = "CONNECTION_ERROR"
        user_message = "Lost connection to the AI service. Please try again."

    elif "psycopg" in type(e).__module__:
        category = "DATABASE_ERROR"
        user_message = "Database error occurred. Please try again."

    else:
        category = "UNKNOWN_ERROR"
        user_message = "Something went wrong. Please try again."

    return {
        "category": category,
        "message": user_message,
        "debug_detail": message,          # full raw error, for you only
        "traceback": traceback.format_exc(),  # full traceback, for you only
    }