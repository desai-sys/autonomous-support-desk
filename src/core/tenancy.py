"""
What a tenant may reach.

A tenant is an organisation or team using the platform. It can only use the AWS
accounts registered for it, and only the tenants in ``PLATFORM_TENANTS`` may use
the platform account itself. Each tenant other than the default one presents its
own ExternalId when assuming roles, so one tenant cannot register and use an
account whose role was set up for another.
"""

import hashlib
import hmac

import config


def platform_allowed(tenant_id: str) -> bool:
    return (tenant_id or config.DEFAULT_TENANT_ID) in config.PLATFORM_TENANTS


def external_id_for(tenant_id: str) -> str:
    """ExternalId this tenant presents ('' if none is configured)."""
    base = config.secret("CROSS_ACCOUNT_EXTERNAL_ID")
    if not base or (tenant_id or config.DEFAULT_TENANT_ID) == config.DEFAULT_TENANT_ID:
        return base
    return hmac.new(base.encode(), f"tenant:{tenant_id}".encode(), hashlib.sha256).hexdigest()[:40]
