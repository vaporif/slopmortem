from __future__ import annotations

import pytest

from slopmortem.http import SSRFBlockedError, safe_get


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:6333/",
    "http://10.0.0.1/admin",
    "http://metadata.google.internal/",
    "file:///etc/passwd",
])
async def test_safe_get_blocks(url):
    with pytest.raises(SSRFBlockedError):
        await safe_get(url)
