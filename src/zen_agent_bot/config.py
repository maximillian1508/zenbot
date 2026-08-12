"""Legacy env-based settings — prefer config.load.load_config()."""

from .config.load import GatewayConfig, load_config

__all__ = ["GatewayConfig", "load_config"]
