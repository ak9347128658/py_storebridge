"""Provider adapters.

Import a specific module (for example ``py_storebridge.providers.s3``) so unused
provider SDKs stay unloaded. ``create_storage()`` does this lazily.
"""

from py_storebridge.providers.base import BaseProvider

__all__ = ["BaseProvider"]
