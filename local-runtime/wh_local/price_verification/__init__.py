"""Public registration surface for the read-only price-verification module."""

from .routes import PriceVerificationRouteDependencies, register_price_verification_routes

__all__ = [
    "PriceVerificationRouteDependencies",
    "register_price_verification_routes",
]
