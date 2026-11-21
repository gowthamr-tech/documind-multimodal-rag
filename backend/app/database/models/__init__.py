"""ORM models. Importing this package registers every table on `Base.metadata`."""

from app.database.models.conversations import (
    Approval,
    ApprovalStatus,
    Citation,
    Conversation,
    Message,
    QueryRun,
)
from app.database.models.documents import (
    Document,
    DocumentVersion,
    JobStatus,
    ProcessingJob,
    Sensitivity,
    VersionStatus,
)
from app.database.models.extraction import (
    Element,
    ExtractedTable,
    Figure,
    FigureAnalysisStatus,
    Page,
    TableCell,
)
from app.database.models.identity import (
    ROLE_RANK,
    AuditLog,
    Membership,
    Organization,
    RefreshToken,
    Role,
    User,
)
from app.database.models.retrieval import (
    DEFAULT_SPACE_ID,
    Chunk,
    ChunkEmbedding,
    EmbeddingSpace,
)

__all__ = [
    "DEFAULT_SPACE_ID",
    "ROLE_RANK",
    "Approval",
    "ApprovalStatus",
    "AuditLog",
    "Chunk",
    "ChunkEmbedding",
    "Citation",
    "Conversation",
    "Document",
    "DocumentVersion",
    "Element",
    "EmbeddingSpace",
    "ExtractedTable",
    "Figure",
    "FigureAnalysisStatus",
    "JobStatus",
    "Membership",
    "Message",
    "Organization",
    "Page",
    "ProcessingJob",
    "QueryRun",
    "RefreshToken",
    "Role",
    "Sensitivity",
    "TableCell",
    "User",
    "VersionStatus",
]
