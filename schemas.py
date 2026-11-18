from typing import Optional
from pydantic import BaseModel


class LoginInput(BaseModel):
    pin: str


class PublicDropoffInput(BaseModel):
    entry_type: str = "DROPOFF"
    account_type: str = "WALK_IN"
    po_station: str = ""
    contact_person: str = ""
    customer_name: str
    customer_phone: str = ""
    customer_email: str = ""
    pickup_address: str = ""
    street_address: str = ""
    city: str = ""
    state: str = "PA"
    zip_code: str = ""
    preferred_date: str = ""
    preferred_time: str = ""
    return_date: str = ""
    return_time: str = ""
    truck_mode: str = "ROAD_ROUNDTRIP"
    bottle_count: int = 1
    tank_summary: str = ""
    service_requested: str = "Hydro + Visual"
    valves_attached: bool = True
    needs_air_fill: bool = False
    rush_requested: bool = False
    picked_up_by: str = ""
    pickup_ticket_ref: str = ""
    notes: str = ""
    website_hp: str = ""


class DropoffStatusInput(BaseModel):
    status: Optional[str] = None
    holding_zone: Optional[str] = None
    rush_requested: Optional[bool] = None
    promised_date: Optional[str] = None


class BinderActionInput(BaseModel):
    action: str = "continue"
    sheet_no: Optional[int] = None


class DayPlanInput(BaseModel):
    work_date: str
    shift_mode: str = "FULL_HYDRO"
    bottle_capacity: Optional[int] = None
    day_notes: str = ""


class ScheduleJobInput(BaseModel):
    scheduled_date: str
    due_date: str = ""
    pickup_time: str = ""
    dropoff_time: str = ""
    return_address: str = ""
    customer_email: str = ""
    job_type: str = "SHOP_BATCH"
    account_type: str = "WALK_IN"
    po_station: str = ""
    contact_person: str = ""
    customer_name: str
    customer_phone: str = ""
    location_notes: str = ""
    street_address: str = ""
    city: str = ""
    state: str = "PA"
    zip_code: str = ""
    bottle_count: int = 1
    tank_summary: str = ""
    valves_attached: bool = True
    needs_air_fill: bool = False
    priority: str = "STANDARD"
    rush_fee: bool = False
    holding_zone: str = "ZONE_A_BACKROOM"
    prep_status: str = "WAITING"
    status: str = "OPEN"
    notes: str = ""


class ScheduleJobPatchInput(BaseModel):
    scheduled_date: Optional[str] = None
    due_date: Optional[str] = None
    pickup_time: Optional[str] = None
    dropoff_time: Optional[str] = None
    return_address: Optional[str] = None
    pickup_leg_done: Optional[bool] = None
    move_to_backroom: Optional[bool] = None
    job_type: Optional[str] = None
    account_type: Optional[str] = None
    po_station: Optional[str] = None
    contact_person: Optional[str] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    location_notes: Optional[str] = None
    bottle_count: Optional[int] = None
    tank_summary: Optional[str] = None
    valves_attached: Optional[bool] = None
    needs_air_fill: Optional[bool] = None
    priority: Optional[str] = None
    rush_fee: Optional[bool] = None
    holding_zone: Optional[str] = None
    prep_status: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class LadderStep(BaseModel):
    pressure_psi: int
    actual_total_cc: float


class VerificationLadderInput(BaseModel):
    test_date: Optional[str] = None
    jacket_type: str
    max_target_psi: int
    steps: list[LadderStep]
    permanent_cc: float = 0.0
    logged_by: str = "RP"
    force_pass: bool = False


class CalibrationDraftInput(BaseModel):
    test_date: Optional[str] = None
    jacket_type: str = "Short Jacket"
    max_target_psi: int = 4000
    values: dict[str, str] = {}
    permanent_cc: str = "0"
    device_id: str = ""


class SettingsInput(BaseModel):
    calibrated_serial: Optional[str] = None
    visual_rin: Optional[str] = None
    starting_sheet_no: Optional[int] = None
    daily_hydro_capacity: Optional[int] = None
    default_tax_rate: Optional[float] = None
    rin_expires_date: Optional[str] = None
    gauge_cert_expires_date: Optional[str] = None
    gemini_api_key: Optional[str] = None
    sheets_webhook_url: Optional[str] = None
    feedback_webhook_url: Optional[str] = None
    alert_email: Optional[str] = None
    smtp_sender_email: Optional[str] = None
    smtp_app_password: Optional[str] = None
    resend_api_key: Optional[str] = None
    resend_from_email: Optional[str] = None
    master_sheet_url: Optional[str] = None
    gbp_edit_url: Optional[str] = None
    shop_pin: Optional[str] = None
    testing_mode: Optional[bool] = None


class CrewInput(BaseModel):
    full_name: str
    initials: str
    role: str = "Visual Inspection"


class IntakeInput(BaseModel):
    work_date: Optional[str] = None
    customer_name: str
    customer_phone: str = ""
    dropoff_id: Optional[int] = None
    schedule_job_id: Optional[int] = None
    priority: str = "STANDARD"
    rush_fee: bool = False
    promised_date: str = ""
    holding_zone: str = "ZONE_C_READY_RACK"
    serial_number: str
    valve_tag: str = ""
    mfg_id: str = ""
    mfg_date: str = ""
    gas_service: str = "AIR"
    outside_dia: str = ""
    length_in: str = ""
    dot_spec: str
    service_psi: int
    test_psi: Optional[int] = None
    ree_limit: Optional[float] = None
    service_mode: str = "HYDRO_AND_VISUAL"
    star_10yr: bool = False
    intake_by: str = "RP"
    notes: str = ""
    allow_duplicate: bool = False


class RecordPriorityInput(BaseModel):
    priority: Optional[str] = None
    rush_fee: Optional[bool] = None
    promised_date: Optional[str] = None
    holding_zone: Optional[str] = None


class RecordAddonsInput(BaseModel):
    addon_oring: Optional[bool] = None
    addon_burst_disc: Optional[bool] = None
    addon_clean: Optional[bool] = None
    condemn_action: Optional[str] = None


class ValveTagAssignInput(BaseModel):
    record_id: Optional[int] = None
    serial_number: Optional[str] = None
    work_date: Optional[str] = None
    valve_tag: Optional[str] = None
    valve_reinstalled: Optional[bool] = None


class ShopStageInput(BaseModel):
    shop_stage: str
    rin_marked: Optional[bool] = None
    rin_mark_type: Optional[str] = None
    valve_reinstalled: Optional[bool] = None
    holding_zone: Optional[str] = None
    picked_up_by: Optional[str] = None
    force: bool = False


class BatchStageInput(BaseModel):
    work_date: Optional[str] = None
    customer_name: str
    shop_stage: str
    rin_marked: Optional[bool] = None
    rin_mark_type: Optional[str] = None
    release_valves: bool = False
    picked_up_by: Optional[str] = None
    holding_zone: Optional[str] = None


class ScanBottleJsonInput(BaseModel):
    images_b64: list[str]
    work_date: Optional[str] = None
    customer_name: str = ""
    customer_phone: str = ""
    dropoff_id: Optional[int] = None
    schedule_job_id: Optional[int] = None
    priority: str = "STANDARD"
    rush_fee: bool = False
    promised_date: str = ""
    valve_tag: str = ""
    service_mode: str = "HYDRO_AND_VISUAL"
    gas_service: str = "AIR"
    outside_dia: str = ""
    length_in: str = ""
    intake_by: str = "RP"
    auto_import: bool = False


class TestCompleteInput(BaseModel):
    test_date: Optional[str] = None
    jacket_type: str = "Tall Jacket"
    total_cc: Optional[float] = None
    permanent_cc: Optional[float] = None
    max_percent_perm: float = 10.0
    valve_tag: Optional[str] = None
    valve_reinstalled: bool = False
    rin_marked: bool = False
    rin_mark_type: str = ""
    star_10yr: Optional[bool] = None
    shop_stage: str = "DRYING"
    coating_type: str = "Painted"
    coating_condition: str = "Good"
    chk_corrosion: str = "OK"
    chk_dents_dings: str = "OK"
    chk_cuts_gouges: str = "OK"
    chk_leaks: str = "OK"
    chk_fire_damage: str = "OK"
    chk_bulges: str = "OK"
    chk_neck_defects: str = "OK"
    relief_valve_changed: str = "No"
    addon_oring: bool = False
    addon_burst_disc: bool = False
    addon_clean: bool = False
    condemn_action: str = ""
    tested_by: str = "RP"
    notes: str = ""


class RecordEditInput(BaseModel):
    customer_name: str
    customer_phone: str = ""
    priority: str = "STANDARD"
    rush_fee: bool = False
    promised_date: str = ""
    holding_zone: str = "ZONE_C_READY_RACK"
    serial_number: str
    valve_tag: str = ""
    valve_reinstalled: bool = False
    rin_marked: bool = False
    rin_mark_type: str = ""
    star_10yr: bool = False
    shop_stage: str = "IN_SHOP"
    mfg_id: str = ""
    mfg_date: str = ""
    gas_service: str = "AIR"
    outside_dia: str = ""
    length_in: str = ""
    dot_spec: str
    service_psi: int
    test_psi: int
    ree_limit: Optional[float] = None
    total_cc: Optional[float] = None
    permanent_cc: Optional[float] = None
    max_percent_perm: float = 10.0
    coating_condition: str = "Good"
    relief_valve_changed: str = "No"
    addon_oring: bool = False
    addon_burst_disc: bool = False
    addon_clean: bool = False
    condemn_action: str = ""
    chk_corrosion: str = "OK"
    chk_dents_dings: str = "OK"
    chk_cuts_gouges: str = "OK"
    chk_leaks: str = "OK"
    chk_fire_damage: str = "OK"
    chk_bulges: str = "OK"
    chk_neck_defects: str = "OK"
    hydro_disposition_override: Optional[str] = None
    notes: str = ""


class CustomerRolodexInput(BaseModel):
    id: Optional[int] = None
    customer_name: str
    account_type: str = "WALK_IN"
    contact_person: str = ""
    phone: str = ""
    email: str = ""
    street_address: str = ""
    city: str = ""
    state: str = "PA"
    zip_code: str = ""
    payment_terms: str = "DUE_AT_PICKUP"
    tax_exempt: bool = False
    notes: str = ""


class SupplyUpsertInput(BaseModel):
    id: Optional[int] = None
    category: str = "Shop Supplies"
    item_name: str
    spec_note: str = ""
    vendor_name: str = "Amazon"
    reorder_url: str = ""
    on_hand_qty: int = 5
    reorder_threshold: int = 2
    unit_label: str = "units"
    low_stock: bool = False
    last_ordered_date: str = ""
    sort_order: int = 100


class SupplyLowStockInput(BaseModel):
    low_stock: bool
    mark_ordered_today: bool = False


class PricingRateUpsert(BaseModel):
    code: str
    description: Optional[str] = None
    unit_price: float = 0.0
    notes: Optional[str] = None
    sort_order: Optional[int] = None


class InvoiceLineInput(BaseModel):
    record_id: Optional[int] = None
    description: str
    qty: float = 1.0
    unit_price: float = 0.0


class InvoiceCreateInput(BaseModel):
    work_date: str
    customer_name: str
    customer_phone: str = ""
    tax_rate: float = 0.0
    status: str = "DRAFT"
    payment_method: str = ""
    created_by: str = "RP"
    notes: str = ""
    items: list[InvoiceLineInput]


class InvoiceStatusInput(BaseModel):
    status: str
    payment_method: Optional[str] = None


class CrewFeedbackInput(BaseModel):
    submitted_by: str = "RP"
    category: str = "Improvement"
    page_tab: str = ""
    priority: str = "Normal"
    message: str


class FeedbackStatusInput(BaseModel):
    status: str


class HelpChatTurn(BaseModel):
    role: str
    text: str


class HelpAssistantInput(BaseModel):
    message: str
    current_page: str = "/ops"
    current_tab: str = ""
    submitted_by: str = "RP"
    history: list[HelpChatTurn] = []


class SupplyQtyPatchInput(BaseModel):
    delta: Optional[int] = None
    on_hand_qty: Optional[int] = None
    reorder_threshold: Optional[int] = None
    unit_label: Optional[str] = None


class DayHoursRowInput(BaseModel):
    day_of_week: int
    day_name: str
    is_open: bool = True
    open_time: str = "09:00"
    close_time: str = "16:00"
    dock_24_7: bool = True
    note: str = ""


class OperatingScheduleSaveInput(BaseModel):
    weekly_hours: list[DayHoursRowInput]
    gbp_edit_url: Optional[str] = None
    master_sheet_url: Optional[str] = None


class HolidayClosureInput(BaseModel):
    id: Optional[int] = None
    start_date: str
    end_date: str = ""
    title: str
    staffed_open: bool = False
    special_hours: str = ""
    dock_open: bool = True
    public_note: str = ""


class TimePunchActionInput(BaseModel):
    action: str = "TOGGLE"  # IN | OUT | TOGGLE
    notes: str = ""


class ManualPunchEditInput(BaseModel):
    duration_minutes: int
    clock_in_at: Optional[str] = None
    clock_out_at: Optional[str] = None
    notes: Optional[str] = None


class PinUserInput(BaseModel):
    id: Optional[int] = None
    name: str
    initials: str
    pin: str
    role: str = "CREW"


class LockDayLineupInput(BaseModel):
    shift_mode: str = "FULL_HYDRO"
    bottle_capacity: int = 24
    allow_extra_autopace: bool = True
    day_notes: str = ""


class DayJobMoveInput(BaseModel):
    job_id: int
    action: str  # PULL_TODAY | BUMP_TOMORROW | TOGGLE_LOCK | SET_COUNT
    bottle_count: Optional[int] = None


class CrewTaskInput(BaseModel):
    assigned_to: str = "ALL"
    category: str = "SHOP"
    title: str
    priority: str = "NORMAL"
    work_date: str = ""


class CrewTaskUpdateInput(BaseModel):
    status: Optional[str] = None
    crew_note: Optional[str] = None
    title: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[str] = None
    assigned_to: Optional[str] = None


class ShopMessageInput(BaseModel):
    recipient: str = "ALL"
    body: str
    channel: str = "CREW"


class ShopMessageResolveInput(BaseModel):
    status: str = "RESOLVED"
    reply_note: Optional[str] = None


class PublicInquiryInput(BaseModel):
    customer_name: str
    customer_phone: str = ""
    customer_email: str = ""
    topic: str = "General Question"
    message: str
    website_hp: str = ""


class WebsiteContentInput(BaseModel):
    site_banner_announcement: str = ""
    site_about_eyebrow: str = ""
    site_about_heading: str = ""
    site_about_story: str = ""
    site_business_details: str = ""
    site_dropoff_note: str = ""
    site_owner_signoff: str = ""


class EndOfDayInput(BaseModel):
    shift_note: str = ""
    roll_unfinished: bool = True


class ThemeSettingsInput(BaseModel):
    theme_mode: str = "AUTO"
    enable_public: bool = True
    enable_tv: bool = True
    enable_ops: bool = True
    custom_dog_message: str = ""


class MerchStoreSettingsInput(BaseModel):
    popup_store_url: str = ""
    allowance_note: str = ""

__all__ = ['LoginInput', 'PublicDropoffInput', 'DropoffStatusInput', 'BinderActionInput', 'DayPlanInput', 'ScheduleJobInput', 'ScheduleJobPatchInput', 'LadderStep', 'VerificationLadderInput', 'SettingsInput', 'CrewInput', 'IntakeInput', 'RecordPriorityInput', 'RecordAddonsInput', 'ValveTagAssignInput', 'ShopStageInput', 'BatchStageInput', 'ScanBottleJsonInput', 'TestCompleteInput', 'RecordEditInput', 'CustomerRolodexInput', 'SupplyUpsertInput', 'SupplyLowStockInput', 'PricingRateUpsert', 'InvoiceLineInput', 'InvoiceCreateInput', 'InvoiceStatusInput', 'CrewFeedbackInput', 'FeedbackStatusInput', 'HelpChatTurn', 'HelpAssistantInput', 'SupplyQtyPatchInput', 'DayHoursRowInput', 'OperatingScheduleSaveInput', 'HolidayClosureInput', 'TimePunchActionInput', 'ManualPunchEditInput', 'PinUserInput', 'LockDayLineupInput', 'DayJobMoveInput', 'CrewTaskInput', 'CrewTaskUpdateInput', 'ShopMessageInput', 'ShopMessageResolveInput', 'PublicInquiryInput', 'WebsiteContentInput', 'EndOfDayInput', 'ThemeSettingsInput', 'MerchStoreSettingsInput']
