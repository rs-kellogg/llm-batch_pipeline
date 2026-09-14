"""Reliable batch pipelines for research coding and extraction."""

from ._version import __version__
from .config import ProjectConfig, load_config
from .core import audit_run, compare_runs, prepare_retry, prepare_run, sync_run
from .models import AuditFinding, BatchHandle, CanonicalRecord, CanonicalRequest, CostEstimate, NormalizedResult
from .providers.base import ProviderAdapter
from .validation import find_duplicate_records, validate_project

__all__ = [
    "AuditFinding",
    "BatchHandle",
    "CanonicalRecord",
    "CanonicalRequest",
    "CostEstimate",
    "NormalizedResult",
    "ProjectConfig",
    "ProviderAdapter",
    "audit_run",
    "compare_runs",
    "find_duplicate_records",
    "load_config",
    "prepare_retry",
    "prepare_run",
    "sync_run",
    "validate_project",
]
