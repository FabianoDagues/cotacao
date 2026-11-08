"""Uso interno: cadastros e lançamentos independentes do histórico de cotações."""
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import String, DateTime, Numeric, ForeignKey, CheckConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class InternalProduct(Base):
    __tablename__ = 'internal_products'
    id: Mapped[int] = mapped_column(primary_key=True)
    description: Mapped[str] = mapped_column(String(255), index=True)
    barcode: Mapped[str] = mapped_column(String(80))
    barcode_key: Mapped[str] = mapped_column(String(85), unique=True, index=True)
    sector: Mapped[str] = mapped_column(String(100), index=True)
    classification: Mapped[str] = mapped_column(String(20), index=True)
    __table_args__ = (CheckConstraint("classification IN ('Limpeza', 'Insumo')", name='ck_internal_product_class'),)


class InternalWithdrawal(Base):
    __tablename__ = 'internal_withdrawals'
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey('internal_products.id', ondelete='RESTRICT'), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    request_key: Mapped[str] = mapped_column(String(64), unique=True)
    # Retrato do lançamento: editar o cadastro não reescreve o relatório histórico.
    description: Mapped[str] = mapped_column(String(255))
    barcode: Mapped[str] = mapped_column(String(80))
    sector: Mapped[str] = mapped_column(String(100))
    classification: Mapped[str] = mapped_column(String(20))
    employee_name: Mapped[str] = mapped_column(String(120))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_by_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    __table_args__ = (
        CheckConstraint('quantity > 0', name='ck_internal_withdrawal_quantity'),
        Index('ix_internal_withdrawal_date_product', 'created_at', 'product_id'),
    )
