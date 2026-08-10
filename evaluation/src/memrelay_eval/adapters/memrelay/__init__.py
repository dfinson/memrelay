"""Memrelay product-stratum adapters and control evidence."""

from .controls import (
    EXPECTED_EMBEDDING_MODEL,
    EXPECTED_FRAMEWORK_MODEL,
    PRODUCT_SHIPPED_TOOL_NAMES,
    FrameworkPreflightEvidence,
    ProductIdentityEnvelope,
    ProductToolContract,
    ProductToolVisibilityEvidence,
    build_framework_process_environments,
    build_product_identity_chain,
    product_tool_contract,
    require_product_tool_visibility,
    verify_framework_preflight,
)
from .product import (
    MCPToolCallEvidence,
    MemrelayProductTreatment,
    ProductCleanupEvidence,
    ProductProvisionRequest,
    ProductStateEvidence,
    ProductTreatmentHandle,
    ProductTreatmentPaths,
    shipped_observation_path,
)

__all__ = [
    "EXPECTED_EMBEDDING_MODEL",
    "EXPECTED_FRAMEWORK_MODEL",
    "FrameworkPreflightEvidence",
    "MCPToolCallEvidence",
    "MemrelayProductTreatment",
    "PRODUCT_SHIPPED_TOOL_NAMES",
    "ProductCleanupEvidence",
    "ProductIdentityEnvelope",
    "ProductProvisionRequest",
    "ProductStateEvidence",
    "ProductToolContract",
    "ProductToolVisibilityEvidence",
    "ProductTreatmentHandle",
    "ProductTreatmentPaths",
    "build_framework_process_environments",
    "build_product_identity_chain",
    "product_tool_contract",
    "require_product_tool_visibility",
    "shipped_observation_path",
    "verify_framework_preflight",
]
