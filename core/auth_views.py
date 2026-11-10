import secrets
import time
import logging
from smtplib import SMTPAuthenticationError, SMTPException

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import BadHeaderError, send_mail
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from .forms import RegisterForm

OTP_SESSION_KEY = "email_otp_challenge"
OTP_LIFETIME_SECONDS = 300
OTP_RESEND_DELAY_SECONDS = 30
OTP_MAX_ATTEMPTS = 5

logger = logging.getLogger(__name__)


class OTPDeliveryError(Exception):
    pass


class OTPConfigurationError(ImproperlyConfigured):
    pass


def _send_otp(request, user, purpose, next_url=""):
    if not all(
        (
            settings.EMAIL_HOST,
            settings.EMAIL_HOST_USER,
            settings.EMAIL_HOST_PASSWORD,
            settings.DEFAULT_FROM_EMAIL,
        )
    ):
        raise OTPConfigurationError(
            "Configure EMAIL_HOST, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, and DEFAULT_FROM_EMAIL."
        )
    if not settings.EMAIL_USE_TLS and not settings.EMAIL_USE_SSL:
        raise OTPConfigurationError("SMTP OTP delivery requires TLS or SSL encryption.")
    if settings.EMAIL_HOST.lower() == "smtp.gmail.com" and len(settings.EMAIL_HOST_PASSWORD) != 16:
        raise OTPConfigurationError(
            "Gmail SMTP requires a fresh 16-character Google App Password. "
            "Set it as EMAIL_HOST_PASSWORD in .env; spaces are removed automatically."
        )
    code = f"{secrets.randbelow(1_000_000):06d}"
    delivered = send_mail(
        subject="Your JobPilot verification code",
        message=(
            f"Hello {user.username},\n\n"
            f"Your JobPilot verification code is {code}. "
            f"It expires in 5 minutes. If you did not request this code, "
            "you can ignore this email."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )
    if delivered != 1:
        raise OTPDeliveryError("The email backend did not deliver the verification message.")
    request.session[OTP_SESSION_KEY] = {
        "user_id": user.pk,
        "purpose": purpose,
        "code_hash": make_password(code),
        "expires_at": int(time.time()) + OTP_LIFETIME_SECONDS,
        "sent_at": int(time.time()),
        "attempts": 0,
        "next": next_url,
    }
    request.session.modified = True


def _otp_delivery_error(request, error=None):
    request.session.pop(OTP_SESSION_KEY, None)
    if error is not None:
        logger.warning("Email OTP delivery failed (%s)", type(error).__name__)

    if isinstance(error, OTPConfigurationError):
        message = str(error)
    elif isinstance(error, SMTPAuthenticationError):
        message = (
            "Gmail rejected the SMTP sign-in. Use the sender's current 16-character "
            "Google App Password in .env, not the regular Gmail password."
        )
    else:
        message = (
            "We couldn't send the verification email. Check the SMTP host, port, "
            "TLS settings, and sender credentials in .env, then try again."
        )
    messages.error(
        request,
        message,
    )


@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        from .models import UserProfile

        with transaction.atomic():
            user = form.save(commit=False)
            user.is_active = False
            user.save()
            form.save_m2m()
            UserProfile.objects.create(user=user, role=form.cleaned_data["role"])
        try:
            _send_otp(request, user, "register")
        except (SMTPException, OSError, BadHeaderError, ImproperlyConfigured, OTPDeliveryError) as error:
            user.delete()
            _otp_delivery_error(request, error)
        else:
            messages.success(request, "A six-digit verification code has been sent to your email.")
            return redirect("verify_otp")
    return render(request, "core/register.html", {"form": form})


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        purpose = "login"
        if user is None:
            pending_user = User.objects.filter(username=username, is_active=False).first()
            if pending_user and pending_user.check_password(password):
                user = pending_user
                purpose = "register"
        if user is None:
            messages.error(request, "Invalid username or password.")
        elif not user.email:
            messages.error(request, "Add an email address to your profile before signing in.")
        else:
            next_url = request.POST.get("next", "")
            try:
                _send_otp(request, user, purpose, next_url)
            except (SMTPException, OSError, BadHeaderError, ImproperlyConfigured, OTPDeliveryError) as error:
                _otp_delivery_error(request, error)
            else:
                if purpose == "register":
                    messages.success(
                        request,
                        "A verification code has been sent to finish activating your account.",
                    )
                else:
                    messages.success(
                        request,
                        "A sign-in verification code has been sent to your email.",
                    )
                return redirect("verify_otp")
    return render(request, "core/login.html", {"next": request.GET.get("next", "")})


@require_http_methods(["GET", "POST"])
def verify_otp(request):
    challenge = request.session.get(OTP_SESSION_KEY)
    if not challenge:
        messages.info(request, "Request a new verification code to continue.")
        return redirect("login")

    user = User.objects.filter(pk=challenge.get("user_id")).first()
    if user is None or int(time.time()) >= challenge.get("expires_at", 0):
        request.session.pop(OTP_SESSION_KEY, None)
        messages.error(request, "That verification code expired. Please sign in or register again.")
        return redirect("login")

    if request.method == "POST":
        action = request.POST.get("action", "verify")
        if action == "resend":
            elapsed = int(time.time()) - challenge.get("sent_at", 0)
            if elapsed < OTP_RESEND_DELAY_SECONDS:
                messages.warning(request, f"Please wait {OTP_RESEND_DELAY_SECONDS - elapsed} seconds before requesting another code.")
            else:
                try:
                    _send_otp(request, user, challenge["purpose"], challenge.get("next", ""))
                except (SMTPException, OSError, BadHeaderError, ImproperlyConfigured, OTPDeliveryError) as error:
                    _otp_delivery_error(request, error)
                    return redirect("login")
                messages.success(request, "A new verification code has been sent.")
            return redirect("verify_otp")

        submitted_code = request.POST.get("otp", "").strip()
        if not submitted_code.isdigit() or len(submitted_code) != 6 or not check_password(
            submitted_code, challenge.get("code_hash", "")
        ):
            challenge["attempts"] = challenge.get("attempts", 0) + 1
            if challenge["attempts"] >= OTP_MAX_ATTEMPTS:
                request.session.pop(OTP_SESSION_KEY, None)
                messages.error(request, "Too many incorrect attempts. Please request a new code.")
                return redirect("login")
            request.session[OTP_SESSION_KEY] = challenge
            request.session.modified = True
            messages.error(request, "That code is incorrect. Please try again.")
        else:
            if challenge["purpose"] == "register":
                user.is_active = True
                user.save(update_fields=["is_active"])
            next_url = challenge.get("next", "")
            request.session.pop(OTP_SESSION_KEY, None)
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            messages.success(request, "Your email is verified. Welcome to JobPilot.")
            if next_url and url_has_allowed_host_and_scheme(
                next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
            ):
                return redirect(next_url)
            return redirect("dashboard")

    return render(
        request,
        "core/verify_otp.html",
        {"email": user.email, "purpose": challenge.get("purpose")},
    )
