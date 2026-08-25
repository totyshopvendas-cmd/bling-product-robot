"""Pydantic models."""
from datetime import datetime, timezone
from typing import Optional, List
from pydantic import BaseModel, Field
import uuid


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class BlingTokenDoc(BaseModel):
    account_id: str = "default"
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    scope: Optional[str] = None
    expires_at: datetime
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class PricingRow(BaseModel):
    cost_cents: int  # cost stored as cents to avoid float issues (21.99 -> 2199)
    store_price_brl: str  # display, e.g. "50,50"
    sale_price_int: int  # integer no punctuation: 5050


class TitleCleanRequest(BaseModel):
    raw_title: str
    sku: Optional[str] = None
    use_llm_fallback: bool = False


class TitleCleanResponse(BaseModel):
    raw: str
    cleaned: str
    length: int
    removed_terms: List[str]
    code_used: Optional[str]
    method: str  # "regex" or "llm"


class PriceLookupResponse(BaseModel):
    cost: float
    cost_cents: int
    sale_price_int: int
    store_price_brl: str
    found: bool


class SettingsDoc(BaseModel):
    key: str
    value: dict


class JohnDropCreds(BaseModel):
    username: str
    password: str


class RobotJobConfig(BaseModel):
    max_products: int = 10
    use_llm_fallback: bool = False
    dry_run: bool = True  # if true, just simulate without actually clicking submit


class RobotStatusResponse(BaseModel):
    state: str  # idle, running, paused, error
    current_product: Optional[str] = None
    processed: int = 0
    success: int = 0
    failed: int = 0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    message: Optional[str] = None


class LogEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    level: str  # info, success, warning, error
    message: str
    product_sku: Optional[str] = None
    raw_title: Optional[str] = None
    cleaned_title: Optional[str] = None
    sale_price: Optional[int] = None
    bot: Optional[str] = "johndrop"  # 'johndrop' | 'shopee' | None
    created_at: datetime = Field(default_factory=now_utc)


class DashboardStats(BaseModel):
    pricing_rows: int
    bling_connected: bool
    johndrop_configured: bool
    products_processed_today: int
    success_today: int
    failed_today: int
    robot_state: str
    shopee_configured: bool = False
    shopee_state: str = "idle"
