from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if isinstance(exc, DjangoValidationError) and response is None:
        message_dict = getattr(exc, "message_dict", None)
        messages = message_dict if message_dict is not None else exc.messages
        response = Response({"errors": messages}, status=status.HTTP_400_BAD_REQUEST)

    if response is None:
        return response

    if isinstance(response.data, dict):
        if "detail" in response.data and len(response.data) == 1:
            response.data = {
                "detail": response.data["detail"],
                "status_code": response.status_code,
            }
        else:
            response.data = {
                "detail": "Validation error" if response.status_code == 400 else "Request failed",
                "errors": response.data,
                "status_code": response.status_code,
            }
    else:
        response.data = {
            "detail": response.data,
            "status_code": response.status_code,
        }
    return response
