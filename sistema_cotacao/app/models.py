from datetime import datetime
from decimal import Decimal
from sqlalchemy import String, Integer, DateTime, Numeric, Text, ForeignKey, UniqueConstraint, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="supplier")
    company_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)

class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

class Quote(Base):
    __tablename__ = "quotes"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(180))
    deadline: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    items: Mapped[list["QuoteItem"]] = relationship(back_populates="quote", cascade="all, delete-orphan")

class QuoteSupplier(Base):
    __tablename__ = "quote_suppliers"
    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id", ondelete="CASCADE"))
    supplier_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    __table_args__ = (UniqueConstraint("quote_id", "supplier_id", name="uq_quote_supplier"),)

class QuoteItem(Base):
    __tablename__ = "quote_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(80), default="")
    description: Mapped[str] = mapped_column(String(255))
    requested_qty: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    base_unit: Mapped[str] = mapped_column(String(40))
    quote: Mapped[Quote] = relationship(back_populates="items")
    responses: Mapped[list["QuoteResponse"]] = relationship(back_populates="item", cascade="all, delete-orphan")

class QuoteResponse(Base):
    __tablename__ = "quote_responses"
    id: Mapped[int] = mapped_column(primary_key=True)
    quote_item_id: Mapped[int] = mapped_column(ForeignKey("quote_items.id", ondelete="CASCADE"), index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    offered_unit: Mapped[str] = mapped_column(String(40), default="UN")
    base_units_per_offered_unit: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=1)
    offered_price: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    brand: Mapped[str | None] = mapped_column(String(120), nullable=True)
    delivery_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    item: Mapped[QuoteItem] = relationship(back_populates="responses")
    supplier: Mapped[User] = relationship()
    __table_args__ = (UniqueConstraint("quote_item_id", "supplier_id", name="uq_item_supplier_response"),)

    @property
    def normalized_price(self):
        factor = Decimal(self.base_units_per_offered_unit or 0)
        price = Decimal(self.offered_price or 0)
        if factor <= 0:
            return None
        return price / factor
