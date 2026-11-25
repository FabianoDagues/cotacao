from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import String, Integer, DateTime, Date, Numeric, Text, ForeignKey, UniqueConstraint, Boolean
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
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id", ondelete="CASCADE"), index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id", ondelete="CASCADE"), index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    items: Mapped[list["PurchaseOrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("quote_id", "supplier_id", name="uq_purchase_order_quote_supplier"),)


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True)
    quote_item_id: Mapped[int] = mapped_column(ForeignKey("quote_items.id", ondelete="CASCADE"), index=True)
    response_id: Mapped[int] = mapped_column(ForeignKey("quote_responses.id", ondelete="CASCADE"), index=True)
    quantity_offered: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    order: Mapped[PurchaseOrder] = relationship(back_populates="items")
    __table_args__ = (UniqueConstraint("order_id", "quote_item_id", name="uq_order_quote_item"),)


class CashGroup(Base):
    __tablename__ = "cash_groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    nature: Mapped[str] = mapped_column(String(20), default="expense")  # income | expense | transfer
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("cash_groups.id", ondelete="SET NULL"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FinancialAccount(Base):
    __tablename__ = "financial_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    account_type: Mapped[str] = mapped_column(String(20), default="bank")  # bank | cash
    bank_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    agency: Mapped[str | None] = mapped_column(String(60), nullable=True)
    account_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    last_statement_balance: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    last_statement_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BankStatementImport(Base):
    __tablename__ = "bank_statement_imports"
    id: Mapped[int] = mapped_column(primary_key=True)
    financial_account_id: Mapped[int] = mapped_column(ForeignKey("financial_accounts.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    imported_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    rows_count: Mapped[int] = mapped_column(Integer, default=0)
    detected_bank: Mapped[str | None] = mapped_column(String(120), nullable=True)
    __table_args__ = (UniqueConstraint("financial_account_id", "file_hash", name="uq_statement_account_file"),)


class CashTransaction(Base):
    __tablename__ = "cash_transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_date: Mapped[date] = mapped_column(Date, index=True)
    description: Mapped[str] = mapped_column(String(255))
    transaction_type: Mapped[str] = mapped_column(String(30), index=True)  # income | expense | transfer_in | transfer_out
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    account_id: Mapped[int] = mapped_column(ForeignKey("financial_accounts.id", ondelete="RESTRICT"), index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("cash_groups.id", ondelete="SET NULL"), nullable=True, index=True)
    counterparty: Mapped[str | None] = mapped_column(String(180), nullable=True)
    document_number: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(30), default="manual")  # manual | bank_import | transfer
    bank_import_id: Mapped[int | None] = mapped_column(ForeignKey("bank_statement_imports.id", ondelete="SET NULL"), nullable=True, index=True)
    import_key: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    transfer_ref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    reconciled: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

class CashClassificationRule(Base):
    __tablename__ = "cash_classification_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("financial_accounts.id", ondelete="CASCADE"), index=True)
    description_key: Mapped[str] = mapped_column(String(255), index=True)
    transaction_type: Mapped[str] = mapped_column(String(20), index=True)  # income | expense
    group_id: Mapped[int] = mapped_column(ForeignKey("cash_groups.id", ondelete="CASCADE"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    hits: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("account_id", "description_key", "transaction_type", name="uq_cash_rule_account_desc_type"),)



class ProductWriteOff(Base):
    """Registro independente de baixas; não altera cotações nem saldos financeiros."""
    __tablename__ = "product_write_offs"
    id: Mapped[int] = mapped_column(primary_key=True)
    movement_date: Mapped[date] = mapped_column(Date, index=True)
    code: Mapped[str] = mapped_column(String(80), default="")
    description: Mapped[str] = mapped_column(String(255))
    unit: Mapped[str] = mapped_column(String(40))
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    reason: Mapped[str] = mapped_column(String(80))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
