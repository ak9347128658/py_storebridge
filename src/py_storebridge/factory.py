"""Factory that lazily loads only the selected provider adapter."""

from __future__ import annotations

import importlib
from typing import Any, Dict, Tuple, Type

from py_storebridge.client import StorageClient
from py_storebridge.config import parse_storage_config
from py_storebridge.exceptions import ValidationError
from py_storebridge.providers.base import BaseProvider

_PROVIDERS: Dict[str, Tuple[str, str]] = {
    "s3": ("py_storebridge.providers.s3", "S3Provider"),
    "r2": ("py_storebridge.providers.r2", "R2Provider"),
    "gcs": ("py_storebridge.providers.gcs", "GCSProvider"),
    "azure": ("py_storebridge.providers.azure", "AzureProvider"),
    "spaces": ("py_storebridge.providers.spaces", "SpacesProvider"),
    "b2": ("py_storebridge.providers.b2", "B2Provider"),
    "minio": ("py_storebridge.providers.minio", "MinIOProvider"),
    "supabase": ("py_storebridge.providers.supabase", "SupabaseProvider"),
    "firebase": ("py_storebridge.providers.firebase", "FirebaseProvider"),
    "appwrite": ("py_storebridge.providers.appwrite", "AppwriteProvider"),
}


def _load_class(provider: str) -> Type[BaseProvider]:
    if provider not in _PROVIDERS:
        raise ValidationError(
            f"Unknown provider '{provider}'. Supported: {', '.join(sorted(_PROVIDERS))}",
            field="provider",
        )
    module_name, class_name = _PROVIDERS[provider]
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def create_storage(config: Any = None, **kwargs: Any) -> StorageClient:
    """Create a ``StorageClient`` for the configured provider.

    Only the selected provider SDK is imported. Install that extra, for example
    ``pip install py_storebridge[s3]``.
    """
    payload = config if config is not None else kwargs
    parsed = parse_storage_config(payload)
    cls = _load_class(parsed.provider)
    provider = cls(
        parsed.credentials,
        retry=parsed.retry,
        log_level=parsed.log_level,
        default_visibility=parsed.visibility,
    )
    return StorageClient(provider)


def create_storage_from_provider(provider: BaseProvider) -> StorageClient:
    """Wrap an already constructed provider adapter."""
    return StorageClient(provider)
