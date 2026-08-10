"""Customer Intelligence & Sales Center Phase 1 preview API.

The persistence foundation deliberately is not imported here, so loading the
synthetic preview cannot acquire a database or encryption dependency.
"""

from .models import CustomerIntelligenceWorkspaceResponse
from .routes import make_customer_intelligence_router
from .service import CustomerIntelligencePreviewService

__all__ = [
    "CustomerIntelligencePreviewService",
    "CustomerIntelligenceWorkspaceResponse",
    "make_customer_intelligence_router",
]
