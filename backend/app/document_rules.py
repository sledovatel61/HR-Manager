"""Phase 11 document lists and constrained personal automation primitives.

This module deliberately stores no document files or free-form automation code.
All candidate mutations are owner-scoped and version checked.
"""
from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.models import Base, UTCDateTime, _new_uuid
from app.utils import utc_now

class DocumentList(Base):
    __tablename__ = "document_lists"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    position: Mapped[str | None] = mapped_column(String(200))
    stage: Mapped[str | None] = mapped_column(String(40))
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False)

class DocumentListVersion(Base):
    __tablename__ = "document_list_versions"
    __table_args__ = (UniqueConstraint("list_id", "version", name="uq_document_list_version"), CheckConstraint("state IN ('draft','published','archived')", name="ck_document_version_state"))
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    list_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_lists.id", ondelete="RESTRICT"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(12), default="draft", nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False)

class DocumentListItem(Base):
    __tablename__ = "document_list_items"
    __table_args__ = (UniqueConstraint("version_id", "item_key", name="uq_document_item_key"), Index("ix_document_item_order", "version_id", "sort_order"))
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_list_versions.id", ondelete="RESTRICT"), nullable=False)
    item_key: Mapped[str] = mapped_column(String(80), nullable=False)
    title_ru: Mapped[str] = mapped_column(String(200), nullable=False)
    explanation: Mapped[str | None] = mapped_column(String(500))
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

class CandidateDocumentSet(Base):
    __tablename__ = "candidate_document_sets"
    __table_args__ = (UniqueConstraint("candidate_id", "version_id", name="uq_candidate_document_set"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.id", ondelete="RESTRICT"), nullable=False)
    list_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_lists.id"), nullable=False)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_list_versions.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)

class CandidateDocumentStatus(Base):
    __tablename__ = "candidate_document_statuses"
    __table_args__ = (UniqueConstraint("set_id", "item_key", name="uq_candidate_document_status"), CheckConstraint("status IN ('missing','received')", name="ck_candidate_document_status"))
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_document_sets.id", ondelete="RESTRICT"), nullable=False)
    item_key: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="missing", nullable=False)
    changed_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

class PersonalRule(Base):
    __tablename__ = "personal_rules"
    __table_args__ = (CheckConstraint("trigger IN ('stage_entered','documents_due')", name="ck_rule_trigger"), CheckConstraint("action IN ('apply_list','document_request','document_reminder')", name="ck_rule_action"))
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    parameters: Mapped[dict] = mapped_column(default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False)
