"""
backend.middleware — Custom middleware for the LocalMind server.
"""

from backend.middleware.rate_limit import RateLimitMiddleware

__all__ = ["RateLimitMiddleware"]
