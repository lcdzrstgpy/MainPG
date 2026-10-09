from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Protocol


class VerificationEmailSender(Protocol):
    def send_verification_code(self, recipient: str, code: str) -> None: ...


class EmailDeliveryError(RuntimeError):
    """Raised when the configured provider cannot accept an email."""


@dataclass(frozen=True)
class TencentCloudSESConfig:
    secret_id: str
    secret_key: str
    region: str = "ap-hongkong"
    from_email: str = "verify@notice.haocoming.top"
    template_id: int = 212484
    display_name: str = "界野"
    subject: str = "界野邮箱验证码"

    @classmethod
    def from_env(cls) -> TencentCloudSESConfig | None:
        secret_id = os.environ.get("TENCENTCLOUD_SECRET_ID", "").strip()
        secret_key = os.environ.get("TENCENTCLOUD_SECRET_KEY", "").strip()
        if not secret_id or not secret_key:
            return None
        raw_template_id = os.environ.get("TENCENTCLOUD_SES_TEMPLATE_ID", "212484").strip()
        try:
            template_id = int(raw_template_id)
        except ValueError as exc:
            raise RuntimeError("TENCENTCLOUD_SES_TEMPLATE_ID must be an integer") from exc
        return cls(
            secret_id=secret_id,
            secret_key=secret_key,
            region=os.environ.get("TENCENTCLOUD_SES_REGION", "ap-hongkong").strip() or "ap-hongkong",
            from_email=(
                os.environ.get("TENCENTCLOUD_SES_FROM_EMAIL", "verify@notice.haocoming.top").strip()
                or "verify@notice.haocoming.top"
            ),
            template_id=template_id,
            display_name=os.environ.get("TENCENTCLOUD_SES_DISPLAY_NAME", "界野").strip() or "界野",
            subject=os.environ.get("TENCENTCLOUD_SES_SUBJECT", "界野邮箱验证码").strip() or "界野邮箱验证码",
        )


class TencentCloudSESEmailSender:
    """Send verification emails through Tencent Cloud SES API.

    Tencent's SDK imports are intentionally lazy so the runtime can still boot
    and report a clear configuration error before SES credentials are added.
    """

    def __init__(self, config: TencentCloudSESConfig):
        self.config = config

    @classmethod
    def from_env(cls) -> TencentCloudSESEmailSender | None:
        config = TencentCloudSESConfig.from_env()
        return cls(config) if config is not None else None

    def send_verification_code(self, recipient: str, code: str) -> None:
        try:
            from tencentcloud.common import credential
            from tencentcloud.common.profile.client_profile import ClientProfile
            from tencentcloud.common.profile.http_profile import HttpProfile
            from tencentcloud.ses.v20201002 import models, ses_client
        except ImportError as exc:
            raise EmailDeliveryError(
                "Tencent Cloud SDK is not installed; install tencentcloud-sdk-python"
            ) from exc

        try:
            credentials = credential.Credential(self.config.secret_id, self.config.secret_key)
            http_profile = HttpProfile()
            http_profile.endpoint = "ses.tencentcloudapi.com"
            client_profile = ClientProfile()
            client_profile.httpProfile = http_profile
            client = ses_client.SesClient(credentials, self.config.region, client_profile)

            template = models.Template()
            template.TemplateID = self.config.template_id
            template.TemplateData = json.dumps({"code": code}, ensure_ascii=False)

            request = models.SendEmailRequest()
            request.FromEmailAddress = f"{self.config.display_name} <{self.config.from_email}>"
            request.Destination = [recipient]
            request.Subject = self.config.subject
            request.Template = template
            client.SendEmail(request)
        except EmailDeliveryError:
            raise
        except Exception as exc:
            # Provider details can include request metadata. Keep them in the
            # chained exception/logs instead of returning them to an end user.
            raise EmailDeliveryError("Tencent Cloud SES rejected the email") from exc
