import json
import logging
import time

from django.core.cache import cache
from django.db.utils import DatabaseError
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from core.chatbot_ml.services import answer_message, save_exchange
from core.models import ChatConversation, ChatMessage

logger = logging.getLogger(__name__)
MAX_MESSAGE_LENGTH = 1000
MAX_REQUEST_BYTES = 4096
RATE_LIMIT = 30
RATE_WINDOW_SECONDS = 60


def _rate_limit_key(request):
    if request.user.is_authenticated:
        return f"chatbot:rate:user:{request.user.pk}"
    return f"chatbot:rate:ip:{request.META.get('REMOTE_ADDR', 'unknown')}"


def _rate_limited(request):
    key = _rate_limit_key(request)
    if cache.add(key, 1, timeout=RATE_WINDOW_SECONDS):
        return False
    try:
        return cache.incr(key) > RATE_LIMIT
    except ValueError:
        cache.set(key, 1, timeout=RATE_WINDOW_SECONDS)
        return False


@require_http_methods(["GET"])
def chatbot_history(request):
    if not request.user.is_authenticated:
        return JsonResponse({"messages": []})
    try:
        conversation = ChatConversation.objects.filter(user=request.user).first()
        if conversation is None:
            return JsonResponse({"messages": []})
        messages = list(
            ChatMessage.objects.filter(conversation=conversation)
            .order_by("-created_at", "-pk")[:100]
        )
        messages.reverse()
        return JsonResponse({
            "messages": [{
                "sender": item.sender,
                "content": item.content,
                "created_at": item.created_at.isoformat(),
                "metadata": item.metadata if item.sender == "assistant" else {},
            } for item in messages],
        })
    except DatabaseError:
        logger.exception("Could not load chatbot history for user %s", request.user.pk)
        return JsonResponse({"error": "Chat history is temporarily unavailable."}, status=503)


@require_http_methods(["POST"])
def chatbot_message(request):
    started = time.perf_counter()
    if len(request.body) > MAX_REQUEST_BYTES:
        return JsonResponse({"error": "Message request is too large."}, status=413)
    if _rate_limited(request):
        return JsonResponse(
            {"error": "You're sending messages too quickly. Please try again in a minute."},
            status=429,
        )
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"error": "Send a valid JSON message."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "Send a message object."}, status=400)
    message = payload.get("message")
    if not isinstance(message, str):
        return JsonResponse({"error": "Message must be text."}, status=400)
    message = message.strip()
    if not message:
        return JsonResponse({"error": "Please enter a message."}, status=400)
    if len(message) > MAX_MESSAGE_LENGTH:
        return JsonResponse(
            {"error": f"Messages must be {MAX_MESSAGE_LENGTH} characters or fewer."},
            status=400,
        )
    try:
        result = answer_message(message, request.user)
        result["response_time_ms"] = round(
            (time.perf_counter() - started) * 1000, 1
        )
        if request.user.is_authenticated:
            result["conversation_id"] = save_exchange(
                request.user, message, result
            )
        return JsonResponse(result)
    except DatabaseError:
        logger.exception("Chatbot database retrieval failed for user %s", request.user.pk if request.user.is_authenticated else "anonymous")
        return JsonResponse(
            {"error": "I couldn't access the requested information right now. Please try again shortly."},
            status=503,
        )
    except Exception:
        logger.exception("Chatbot message processing failed")
        return JsonResponse(
            {"error": "The assistant is temporarily unavailable. Please try again shortly."},
            status=503,
        )


@require_http_methods(["POST"])
def chatbot_clear(request):
    if not request.user.is_authenticated:
        return JsonResponse({"cleared": True})
    try:
        ChatConversation.objects.filter(user=request.user).delete()
    except DatabaseError:
        logger.exception("Could not clear chatbot history for user %s", request.user.pk)
        return JsonResponse({"error": "Chat history could not be cleared."}, status=503)
    return JsonResponse({"cleared": True})
