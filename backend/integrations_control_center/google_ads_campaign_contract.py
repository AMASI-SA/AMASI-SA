"""Human editable Google Ads inputs, pinned to the v25 resource contract.

Draft validation is deliberately separate from write readiness. A marketer or
the existing Mezan AI may save incomplete creative content; the deterministic
builder must refuse it before a provider mutation. These models contain neither
credentials, currency claims, arbitrary operations nor client field masks.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, field_validator, model_validator

Digits = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9]{1,20}$")]
NativeAmount = Annotated[str, StringConstraints(strict=True, pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?$")]
ShortName = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=255)]
Text = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=90)]
AssetResource = Annotated[str, StringConstraints(strict=True, pattern=r"^customers/[0-9]{1,20}/assets/[1-9][0-9]{0,19}$")]
CampaignType = Literal["SEARCH", "DISPLAY", "SHOPPING", "PERFORMANCE_MAX", "DEMAND_GEN", "APP", "HOTEL", "VIDEO", "SMART", "LOCAL_SERVICES"]
AdFormat = Literal["AUTO", "RSA", "DSA", "RESPONSIVE_DISPLAY", "UPLOADED_IMAGE", "UPLOADED_HTML5", "MULTI_ASSET", "VIDEO", "CAROUSEL", "PRODUCT", "APP", "HOTEL"]
BiddingStrategy = Literal["MANUAL_CPC", "MAXIMIZE_CLICKS", "MAXIMIZE_CONVERSIONS", "MAXIMIZE_CONVERSION_VALUE", "TARGET_CPA", "TARGET_ROAS", "PERCENT_CPC"]

SUPPORTED_STRATEGIES: dict[str, tuple[str, ...]] = {
    "SEARCH": ("MANUAL_CPC", "MAXIMIZE_CLICKS", "MAXIMIZE_CONVERSIONS", "MAXIMIZE_CONVERSION_VALUE", "TARGET_CPA", "TARGET_ROAS"),
    "DISPLAY": ("MANUAL_CPC", "MAXIMIZE_CLICKS", "MAXIMIZE_CONVERSIONS", "TARGET_CPA", "TARGET_ROAS"),
    "SHOPPING": ("MANUAL_CPC", "MAXIMIZE_CLICKS", "TARGET_ROAS"),
    "PERFORMANCE_MAX": ("MAXIMIZE_CONVERSIONS", "MAXIMIZE_CONVERSION_VALUE", "TARGET_CPA", "TARGET_ROAS"),
    "DEMAND_GEN": ("MAXIMIZE_CLICKS", "MAXIMIZE_CONVERSIONS", "TARGET_CPA", "TARGET_ROAS"),
    "APP": ("TARGET_CPA", "TARGET_ROAS"),
    "HOTEL": ("MANUAL_CPC", "PERCENT_CPC"),
}
SUPPORTED_FORMATS: dict[str, tuple[str, ...]] = {
    "SEARCH": ("RSA", "DSA"),
    "DISPLAY": ("RESPONSIVE_DISPLAY", "UPLOADED_IMAGE", "UPLOADED_HTML5"),
    "SHOPPING": ("PRODUCT",),
    "PERFORMANCE_MAX": ("MULTI_ASSET",),
    "DEMAND_GEN": ("MULTI_ASSET", "VIDEO", "CAROUSEL", "PRODUCT"),
    "APP": ("APP",),
    "HOTEL": ("HOTEL",),
}
TOTAL_BUDGET_TYPES = frozenset({"SEARCH", "SHOPPING", "PERFORMANCE_MAX", "DEMAND_GEN"})
SMART_MANAGEMENT_ACTIONS = ("rename", "pause", "enable", "set_budget", "remove")
UNSUPPORTED_CREATE = {
    "VIDEO": "Google Ads API does not support creation of classic VIDEO campaigns; Demand Gen video is a different campaign type.",
    "SMART": "New Smart campaign creation was discontinued by Google on 2026-09-23.",
    "LOCAL_SERVICES": "Google Ads API does not support new Local Services or Local Services Performance Max campaigns.",
}
DAILY_BUDGET_EXPLANATION = (
    "An average daily budget is not a hard daily spending cap. For most campaigns "
    "Google may charge up to twice the average on an individual day, with a monthly "
    "charging limit of 30.4 times an unchanged average daily budget. Budget changes "
    "and campaign-specific exceptions affect the limits; Mezan approval limits "
    "must account for Google's charging rules."
)


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def positive_amount(value: str) -> str:
    number = Decimal(value)
    if not number.is_finite() or number <= 0 or number * 1_000_000 > 9_223_372_036_854_775_807:
        raise ValueError("amount must be positive and fit Google's int64 micros")
    return value


def validate_http_url(value: str) -> str:
    if any(ord(character) < 32 for character in value) or any(character.isspace() for character in value):
        raise ValueError("URL cannot contain whitespace or control characters")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("an absolute http(s) URL without embedded credentials is required")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("invalid URL port") from exc
    return value


def owned_resource(value: str, customer_id: str, collection: str, *, allow_id: bool = False, composite: bool = False) -> str:
    """Validate, never strip, the customer prefix of a resource reference."""
    if allow_id and re.fullmatch(r"[1-9][0-9]{0,19}", value):
        return f"customers/{customer_id}/{collection}/{value}"
    suffix = r"[1-9][0-9]{0,19}(?:~[1-9][0-9]{0,19})" if composite else r"[1-9][0-9]{0,19}"
    if not re.fullmatch(rf"customers/{re.escape(customer_id)}/{re.escape(collection)}/{suffix}", value):
        raise ValueError(f"{collection} resource must belong to the selected Google customer")
    return value


class GoogleBiddingInput(StrictInput):
    strategy: BiddingStrategy
    target_cpa_native: NativeAmount | None = None
    target_roas: NativeAmount | None = None
    max_cpc_native: NativeAmount | None = None

    @field_validator("target_cpa_native", "target_roas", "max_cpc_native")
    @classmethod
    def amounts(cls, value: str | None) -> str | None:
        return positive_amount(value) if value is not None else None

    @model_validator(mode="after")
    def compatible_parameters(self) -> "GoogleBiddingInput":
        if self.target_cpa_native is not None and self.strategy not in {"TARGET_CPA", "MAXIMIZE_CONVERSIONS"}:
            raise ValueError("target_cpa_native requires target CPA or maximize conversions")
        if self.target_roas is not None and self.strategy not in {"TARGET_ROAS", "MAXIMIZE_CONVERSION_VALUE"}:
            raise ValueError("target_roas is a ratio (3 means 300%) and requires value-based bidding")
        if self.max_cpc_native is not None and self.strategy not in {"MANUAL_CPC", "MAXIMIZE_CLICKS", "PERCENT_CPC"}:
            raise ValueError("max_cpc_native is not supported by this standard bidding strategy")
        return self


class GoogleKeywordInput(StrictInput):
    text: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=80)]
    match_type: Literal["EXACT", "PHRASE", "BROAD"] = "PHRASE"
    negative: StrictBool = False
    status: Literal["ENABLED", "PAUSED"] | None = None

    @field_validator("text")
    @classmethod
    def keyword_words(cls, value: str) -> str:
        if not value.strip() or len(value.split()) > 10 or any(ord(c) < 32 for c in value):
            raise ValueError("keyword requires 1 to 10 words without control characters")
        return value

    @model_validator(mode="after")
    def negative_status(self) -> "GoogleKeywordInput":
        if self.negative and self.status is not None:
            raise ValueError("negative keyword criteria do not have a serving status")
        return self


class GoogleCarouselCardInput(StrictInput):
    headline: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=40)]
    final_url: Annotated[str, StringConstraints(strict=True, max_length=2048)]
    image_asset: AssetResource | None = None
    square_image_asset: AssetResource | None = None
    portrait_image_asset: AssetResource | None = None
    _url = field_validator("final_url")(validate_http_url)


class GoogleCampaignAdInput(StrictInput):
    """Additional ad creative; targeting stays on its containing ad group."""
    name: ShortName | None = None
    status: Literal["ENABLED", "PAUSED"] = "ENABLED"
    ad_format: AdFormat = "AUTO"
    headlines: list[Text] = Field(default_factory=list, max_length=15)
    descriptions: list[Text] = Field(default_factory=list, max_length=5)
    long_headlines: list[Text] = Field(default_factory=list, max_length=5)
    final_urls: list[Annotated[str, StringConstraints(strict=True, max_length=2048)]] = Field(default_factory=list, max_length=10)
    business_name: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=25)] | None = None
    image_assets: list[AssetResource] = Field(default_factory=list, max_length=20)
    square_image_assets: list[AssetResource] = Field(default_factory=list, max_length=20)
    portrait_image_assets: list[AssetResource] = Field(default_factory=list, max_length=20)
    tall_portrait_image_assets: list[AssetResource] = Field(default_factory=list, max_length=20)
    logo_assets: list[AssetResource] = Field(default_factory=list, max_length=5)
    landscape_logo_assets: list[AssetResource] = Field(default_factory=list, max_length=5)
    video_assets: list[AssetResource] = Field(default_factory=list, max_length=20)
    media_bundle_asset: AssetResource | None = None
    carousel_cards: list[GoogleCarouselCardInput] = Field(default_factory=list, max_length=10)

    @field_validator("final_urls")
    @classmethod
    def urls(cls, values: list[str]) -> list[str]:
        return [validate_http_url(value) for value in values]


class GoogleCampaignGroupInput(StrictInput):
    name: ShortName
    status: Literal["ENABLED", "PAUSED"] = "ENABLED"
    ad_name: ShortName | None = None
    ad_status: Literal["ENABLED", "PAUSED"] = "ENABLED"
    ad_format: AdFormat = "AUTO"
    ads: list[GoogleCampaignAdInput] = Field(default_factory=list, max_length=99)
    headlines: list[Text] = Field(default_factory=list, max_length=15)
    descriptions: list[Text] = Field(default_factory=list, max_length=5)
    long_headlines: list[Text] = Field(default_factory=list, max_length=5)
    final_urls: list[Annotated[str, StringConstraints(strict=True, max_length=2048)]] = Field(default_factory=list, max_length=10)
    keywords: list[GoogleKeywordInput] = Field(default_factory=list, max_length=500)
    business_name: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=25)] | None = None
    image_assets: list[AssetResource] = Field(default_factory=list, max_length=20)
    square_image_assets: list[AssetResource] = Field(default_factory=list, max_length=20)
    portrait_image_assets: list[AssetResource] = Field(default_factory=list, max_length=20)
    tall_portrait_image_assets: list[AssetResource] = Field(default_factory=list, max_length=20)
    logo_assets: list[AssetResource] = Field(default_factory=list, max_length=5)
    landscape_logo_assets: list[AssetResource] = Field(default_factory=list, max_length=5)
    video_assets: list[AssetResource] = Field(default_factory=list, max_length=20)
    media_bundle_asset: AssetResource | None = None
    audience_ids: list[Annotated[str, StringConstraints(strict=True, max_length=100)]] = Field(default_factory=list, max_length=20)
    search_themes: list[Annotated[str, StringConstraints(strict=True, min_length=1, max_length=80)]] = Field(default_factory=list, max_length=50)
    user_list_ids: list[Annotated[str, StringConstraints(strict=True, max_length=100)]] = Field(default_factory=list, max_length=20)
    product_offer_ids: list[Annotated[str, StringConstraints(strict=True, min_length=1, max_length=150)]] = Field(default_factory=list, max_length=500)
    hotel_ids: list[Annotated[str, StringConstraints(strict=True, min_length=1, max_length=150)]] = Field(default_factory=list, max_length=500)
    carousel_cards: list[GoogleCarouselCardInput] = Field(default_factory=list, max_length=10)
    channels: list[Literal["YOUTUBE_IN_STREAM", "YOUTUBE_IN_FEED", "YOUTUBE_SHORTS", "DISCOVER", "GMAIL", "DISPLAY"]] = Field(default_factory=list, max_length=6)
    geo_target_ids: list[Digits] | None = Field(default=None, max_length=100)
    language_ids: list[Digits] | None = Field(default=None, max_length=50)
    cpc_bid_native: NativeAmount | None = None
    percent_cpc_bid: NativeAmount | None = None
    disable_ai_max_search_term_matching: StrictBool = False

    @field_validator("final_urls")
    @classmethod
    def urls(cls, values: list[str]) -> list[str]:
        return [validate_http_url(value) for value in values]

    @field_validator("name", "ad_name", "business_name", "headlines", "descriptions", "long_headlines")
    @classmethod
    def clean_text(cls, value: Any) -> Any:
        values = value if isinstance(value, list) else [value]
        if any(item is not None and (not item.strip() or any(ord(c) < 32 for c in item)) for item in values):
            raise ValueError("text must be nonblank and contain no control characters")
        if isinstance(value, list) and len(set(value)) != len(value):
            raise ValueError("creative text assets must be distinct")
        return value

    @field_validator("cpc_bid_native", "percent_cpc_bid")
    @classmethod
    def bid_amounts(cls, value: str | None) -> str | None:
        return positive_amount(value) if value is not None else None

    @model_validator(mode="after")
    def percent_fraction(self) -> "GoogleCampaignGroupInput":
        if self.percent_cpc_bid is not None and Decimal(self.percent_cpc_bid) >= 1:
            raise ValueError("percent_cpc_bid is a fraction below 1 (0.1 means 10%)")
        for field in ("image_assets", "square_image_assets", "portrait_image_assets", "tall_portrait_image_assets", "logo_assets", "landscape_logo_assets", "video_assets", "audience_ids", "search_themes", "user_list_ids", "product_offer_ids", "hotel_ids", "channels"):
            values = getattr(self, field)
            if len(values) != len(set(values)):
                raise ValueError(f"{field} cannot contain duplicates")
        if len({(keyword.text, keyword.match_type, keyword.negative) for keyword in self.keywords}) != len(self.keywords):
            raise ValueError("keyword text/match type/negative combinations must be distinct")
        for field in ("geo_target_ids", "language_ids"):
            values = getattr(self, field)
            if values is not None and len(values) != len(set(values)):
                raise ValueError(f"{field} cannot contain duplicates")
        return self


class GoogleShoppingInput(StrictInput):
    merchant_id: Digits
    feed_label: Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Z0-9_-]{1,20}$")] | None = None
    campaign_priority: Annotated[int, Field(strict=True, ge=0, le=2)] = 0
    enable_local: StrictBool = False
    target_search_network: StrictBool = False


AppGoalType = Literal["OPTIMIZE_INSTALLS_TARGET_INSTALL_COST", "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_INSTALL_COST", "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST", "OPTIMIZE_RETURN_ON_ADVERTISING_SPEND", "OPTIMIZE_PRE_REGISTRATION_CONVERSION_VOLUME"]


class GoogleAppInput(StrictInput):
    subtype: Literal["INSTALLS", "ENGAGEMENT", "PRE_REGISTRATION"] = "INSTALLS"
    app_id: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=255)]
    app_store: Literal["GOOGLE_APP_STORE", "APPLE_APP_STORE"] = "GOOGLE_APP_STORE"
    goal_type: AppGoalType | None = None
    conversion_action_ids: list[Annotated[str, StringConstraints(strict=True, max_length=100)]] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def store_id(self) -> "GoogleAppInput":
        if self.app_store == "APPLE_APP_STORE" and not re.fullmatch(r"[1-9][0-9]+", self.app_id):
            raise ValueError("Apple app_id must be the numeric App Store identifier")
        if self.app_store == "GOOGLE_APP_STORE" and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", self.app_id):
            raise ValueError("Google app_id must be an Android package name")
        if self.subtype == "PRE_REGISTRATION" and self.app_store != "GOOGLE_APP_STORE":
            raise ValueError("App pre-registration is supported for Google Play only")
        return self


class GoogleHotelInput(StrictInput):
    hotel_center_id: Digits


class GoogleSearchInput(StrictInput):
    target_search_network: StrictBool = True
    target_content_network: StrictBool = False
    target_partner_search_network: StrictBool = False
    dynamic_search_domain: Annotated[str, StringConstraints(strict=True, max_length=253)] | None = None
    dynamic_search_language_code: Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z]{2,3}(?:-[A-Z]{2})?$")] | None = None
    use_supplied_urls_only: StrictBool = False

    @field_validator("dynamic_search_domain")
    @classmethod
    def domain_only(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value):
            raise ValueError("DSA domain must be a hostname, without a scheme or path")
        return value


class GoogleAiMaxInput(StrictInput):
    enabled: StrictBool = False
    text_customization: StrictBool = False
    final_url_expansion: StrictBool = False

    @model_validator(mode="after")
    def dependencies(self) -> "GoogleAiMaxInput":
        if (self.text_customization or self.final_url_expansion) and not self.enabled:
            raise ValueError("AI Max must be enabled for text customization or URL expansion")
        if self.final_url_expansion and not self.text_customization:
            raise ValueError("final URL expansion requires text customization")
        return self


class GoogleConversionGoalInput(StrictInput):
    category: Literal["ADD_TO_CART", "BEGIN_CHECKOUT", "BOOK_APPOINTMENT", "CONTACT", "CONVERTED_LEAD", "DEFAULT", "DOWNLOAD", "ENGAGEMENT", "GET_DIRECTIONS", "IMPORTED_LEAD", "IN_APP_AD_REVENUE", "OUTBOUND_CLICK", "PAGE_VIEW", "PHONE_CALL_LEAD", "PURCHASE", "QUALIFIED_LEAD", "REQUEST_QUOTE", "SIGNUP", "STORE_SALE", "STORE_VISIT", "SUBMIT_LEAD_FORM", "SUBSCRIBE_PAID", "YOUTUBE_FOLLOW_ON_VIEWS"]
    origin: Literal["APP", "CALL_FROM_ADS", "GOOGLE_HOSTED", "LOCAL_SERVICES_ADS", "STORE", "WEBSITE", "YOUTUBE_HOSTED"]
    biddable: StrictBool


class GoogleProductOfferSelection(StrictInput):
    product_id: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")]
    merchant_id: Digits
    offer_id: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=150)]


class GoogleCampaignDraft(StrictInput):
    account_id: Digits
    product_ids: list[Annotated[str, StringConstraints(strict=True, min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")]] = Field(default_factory=list, max_length=8)
    product_offer_selections: list[GoogleProductOfferSelection] = Field(default_factory=list, max_length=50)
    campaign_type: CampaignType
    name: ShortName
    budget_native: NativeAmount
    budget_mode: Literal["DAILY", "TOTAL"] = "DAILY"
    bidding: GoogleBiddingInput
    geo_target_ids: list[Digits] = Field(default_factory=list, max_length=100)
    language_ids: list[Digits] = Field(default_factory=list, max_length=50)
    start_date: Annotated[str, StringConstraints(strict=True, pattern=r"^\d{4}-\d{2}-\d{2}$")] | None = None
    end_date: Annotated[str, StringConstraints(strict=True, pattern=r"^\d{4}-\d{2}-\d{2}$")] | None = None
    groups: list[GoogleCampaignGroupInput] = Field(default_factory=list, max_length=100)
    shopping: GoogleShoppingInput | None = None
    app: GoogleAppInput | None = None
    hotel: GoogleHotelInput | None = None
    search: GoogleSearchInput | None = None
    ai_max: GoogleAiMaxInput | None = None
    conversion_goals: list[GoogleConversionGoalInput] = Field(default_factory=list, max_length=100)
    brand_guidelines_enabled: StrictBool = True
    eu_political_advertising: StrictBool = False

    _budget = field_validator("budget_native")(positive_amount)

    @field_validator("start_date", "end_date")
    @classmethod
    def dates(cls, value: str | None) -> str | None:
        if value is not None:
            date.fromisoformat(value)
        return value

    @field_validator("name")
    @classmethod
    def campaign_name(cls, value: str) -> str:
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("campaign name must be nonblank without control characters")
        return value

    @model_validator(mode="after")
    def references(self) -> "GoogleCampaignDraft":
        if len(set(self.product_ids)) != len(self.product_ids):
            raise ValueError("product identifiers must be distinct")
        if any(selection.product_id not in self.product_ids for selection in self.product_offer_selections):
            raise ValueError("feed selections must reference selected Mezan products")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        if len(set(self.geo_target_ids)) != len(self.geo_target_ids) or len(set(self.language_ids)) != len(self.language_ids):
            raise ValueError("targeting identifiers must be distinct")
        if len({group.name for group in self.groups}) != len(self.groups):
            raise ValueError("group names must be distinct within a draft")
        if len({(goal.category, goal.origin) for goal in self.conversion_goals}) != len(self.conversion_goals):
            raise ValueError("conversion goal category/origin pairs must be distinct")
        for group in self.groups:
            validate_group_references(group, self.account_id)
        if self.app:
            for ref in self.app.conversion_action_ids:
                owned_resource(ref, self.account_id, "conversionActions", allow_id=True)
        return self


def validate_group_references(group: GoogleCampaignGroupInput, customer_id: str) -> None:
    for field in ("image_assets", "square_image_assets", "portrait_image_assets", "tall_portrait_image_assets", "logo_assets", "landscape_logo_assets", "video_assets"):
        for ref in getattr(group, field):
            owned_resource(ref, customer_id, "assets")
    if group.media_bundle_asset:
        owned_resource(group.media_bundle_asset, customer_id, "assets")
    for card in group.carousel_cards:
        for ref in (card.image_asset, card.square_image_asset, card.portrait_image_asset):
            if ref:
                owned_resource(ref, customer_id, "assets")
    for ref in group.audience_ids:
        owned_resource(ref, customer_id, "audiences", allow_id=True)
    for ref in group.user_list_ids:
        owned_resource(ref, customer_id, "userLists", allow_id=True)
    for ad in group.ads:
        validate_group_references(ad_as_group(group, ad), customer_id)


def ad_as_group(group: GoogleCampaignGroupInput, ad: GoogleCampaignAdInput) -> GoogleCampaignGroupInput:
    data = group.model_dump(mode="json", exclude={"ads"})
    for field, value in ad.model_dump(mode="json", exclude={"name", "status", "ad_format"}).items():
        data[field] = value
    data["ad_format"] = group.ad_format if ad.ad_format == "AUTO" else ad.ad_format
    data["ad_name"] = ad.name
    data["ad_status"] = ad.status
    data["ads"] = []
    return GoogleCampaignGroupInput.model_validate(data)


class GoogleCampaignActionInput(StrictInput):
    action: Literal["create_campaign", "rename", "pause", "enable", "set_budget", "set_bidding", "set_targeting", "set_conversion_goals", "set_keywords", "set_channels", "update_ad", "remove", "duplicate", "add_group", "add_ad", "rename_group", "pause_group", "enable_group", "set_group_bidding", "remove_group", "remove_ad", "pause_ad", "enable_ad", "update_group_assets"]
    account_id: Digits
    campaign_id: Digits | None = None
    draft_id: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")] | None = None
    draft_revision: Annotated[int, Field(strict=True, ge=1)] | None = None
    changes: dict[str, Any] = Field(default_factory=dict, max_length=10)
    reason: Annotated[str, StringConstraints(strict=True, max_length=500)] = ""
    idempotency_key: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")] | None = None

    @field_validator("action", mode="before")
    @classmethod
    def create_alias(cls, value: Any) -> Any:
        return "create_campaign" if value == "create" else value

    @model_validator(mode="after")
    def semantic_changes(self) -> "GoogleCampaignActionInput":
        allowed = {
            "create_campaign": set(), "rename": {"name"}, "pause": set(), "enable": set(),
            "set_budget": {"budget_native"}, "set_bidding": {"bidding"},
            "set_targeting": {"geo_target_ids", "language_ids", "group_id"},
            "set_conversion_goals": {"conversion_goals"},
            "set_keywords": {"group_id", "keywords"}, "set_channels": {"group_id", "channels"},
            "update_ad": {"ad_group_ad_resource", "group"}, "remove": {"ad_group_ad_resource"},
            "duplicate": {"name"},
            "add_group": {"group"}, "add_ad": {"group_id", "group"},
            "rename_group": {"group_id", "name"}, "pause_group": {"group_id"},
            "enable_group": {"group_id"}, "remove_group": {"group_id"},
            "set_group_bidding": {"group_id", "cpc_bid_native", "percent_cpc_bid", "target_cpa_native", "target_roas"},
            "remove_ad": {"ad_group_ad_resource"}, "update_group_assets": {"group_id", "group"},
            "pause_ad": {"ad_group_ad_resource"}, "enable_ad": {"ad_group_ad_resource"},
        }[self.action]
        if set(self.changes) - allowed:
            raise ValueError("changes contain fields not allowed for this action")
        if self.action == "create_campaign" and (not self.draft_id or not self.draft_revision):
            raise ValueError("campaign creation requires a saved draft_id and draft_revision")
        if self.action != "create_campaign" and not self.campaign_id:
            raise ValueError("campaign_id is required for this action")
        return self


def campaign_capabilities() -> list[dict[str, Any]]:
    """Builder support, not account eligibility, provider success or live proof."""
    labels = {"SEARCH": "Search", "DISPLAY": "Display", "SHOPPING": "Shopping", "PERFORMANCE_MAX": "Performance Max", "DEMAND_GEN": "Demand Gen", "APP": "App", "HOTEL": "Hotel", "VIDEO": "Classic Video", "SMART": "Smart", "LOCAL_SERVICES": "Local Services", "PERFORMANCE_MAX_TRAVEL": "Performance Max for travel goals"}
    prerequisites = {
        "SEARCH": ["google_ads_link", "eligible_conversion_actions_for_smart_bidding", "dsa_website_language_if_selected"],
        "DISPLAY": ["google_ads_link", "approved_image_assets", "html5_account_eligibility_if_selected"],
        "SHOPPING": ["linked_merchant_center", "approved_merchant_products"],
        "PERFORMANCE_MAX": ["complete_asset_group", "brand_name_and_logo", "linked_merchant_center_if_retail"],
        "DEMAND_GEN": ["format_specific_image_video_assets", "linked_merchant_center_if_feed"],
        "APP": ["valid_store_app_id", "conversion_tracking", "engagement_user_list_if_selected"],
        "HOTEL": ["allowlisted_account", "linked_hotel_center", "hotel_feed_and_pricing"],
    }
    rows = []
    for campaign_type, label in labels.items():
        supported = campaign_type in SUPPORTED_FORMATS
        limitation = UNSUPPORTED_CREATE.get(campaign_type)
        if campaign_type == "HOTEL":
            limitation = "Hotel feed and pricing are managed in Hotel Center; an allowlisted, linked account is required."
        elif campaign_type == "PERFORMANCE_MAX_TRAVEL":
            limitation = "Specialized travel-goal hotel-property asset sets and generation workflow are not implemented; ordinary Performance Max must not be substituted."
        actions = ["rename", "pause", "enable", "set_budget", "set_bidding", "set_targeting", "remove", "duplicate", "add_group", "rename_group", "pause_group", "enable_group", "remove_group"] if supported else []
        if campaign_type == "SMART":
            actions = list(SMART_MANAGEMENT_ACTIONS)
        if supported and campaign_type != "PERFORMANCE_MAX":
            actions.extend(["update_ad", "add_ad", "remove_ad", "pause_ad", "enable_ad", "set_group_bidding"])
        elif campaign_type == "PERFORMANCE_MAX":
            actions.append("update_group_assets")
        if supported and campaign_type != "APP":
            actions.append("set_conversion_goals")
        if campaign_type == "SEARCH":
            actions.append("set_keywords")
        elif campaign_type == "DEMAND_GEN":
            actions.append("set_channels")
        rows.append({
            "type": campaign_type, "label": label, "create": supported, "read": True,
            "manage": bool(actions), "actions": actions, "provider_limitation": limitation,
            "prerequisites": prerequisites.get(campaign_type, []),
            "implemented": "builder_ready" if supported else "existing_campaign_management" if campaign_type == "SMART" else "creation_unavailable" if campaign_type in UNSUPPORTED_CREATE else "not_implemented",
            "provider_api_version": "v25", "formats": list(SUPPORTED_FORMATS.get(campaign_type, ())),
            "bidding_strategies": list(SUPPORTED_STRATEGIES.get(campaign_type, ())),
            "budget_modes": ["DAILY", "TOTAL"] if campaign_type in TOTAL_BUDGET_TYPES else ["DAILY"] if supported else [],
            "budget_explanation": DAILY_BUDGET_EXPLANATION,
            "live_verified": False,
            "duplicate_source_policy": "complete_lossless_snapshot_required" if supported else None,
        })
    return rows

