"""The CRM Spec: the schema-validated contract between planner and applier.

Everything the customer can ask for is expressible here or is not built. The
planner (later) produces this; the applier turns it into Frappe documents.
Nothing in this module executes anything.

Two-stage validation. These models are the *structural* stage -- shapes,
types, closed enums. `spec_validator` is the *semantic* stage (cross
references, naming rules, cycles), and it runs after `spec_normalizer` has
filled in everything a planner should not have to get right (fieldnames,
ordering, boilerplate permissions). The JSON Schema handed to the planner is
generated from these models, so the prompt and the validator cannot drift.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.crm import catalog

FieldType = Literal[*catalog.FIELD_TYPES]
PropertyName = Literal[*catalog.PROPERTY_SETTER_TYPES.keys()]
NotificationEvent = Literal[*catalog.NOTIFICATION_EVENTS]


class _Model(BaseModel):
    # An unexpected key is almost always a planner inventing a capability
    # that does not exist; better a validation error it can retry on than a
    # silently ignored setting.
    model_config = ConfigDict(extra="forbid")


class Meta(_Model):
    name: str = Field(min_length=1, description="The business this CRM is for.")
    description: str = ""


class RoleSpec(_Model):
    name: str = Field(min_length=1, max_length=100)


class FieldSpec(_Model):
    label: str = Field(min_length=1, max_length=140)
    # Optional for the planner: derived from the label by the normalizer.
    fieldname: str | None = None
    fieldtype: FieldType
    # Select: newline-separated choices. Link/Table: a doctype name.
    options: str | None = None
    reqd: bool = False
    unique: bool = False
    in_list_view: bool = False
    default: str | None = None
    description: str | None = None
    # Only meaningful for custom fields on standard doctypes; the normalizer
    # chains it. Ignored inside a spec-defined doctype, where list order is
    # already field order.
    insert_after: str | None = None


class PermissionSpec(_Model):
    role: str
    read: bool = True
    write: bool = False
    create: bool = False
    delete: bool = False


class DoctypeSpec(_Model):
    name: str = Field(min_length=1)
    description: str = ""
    # A child table is only ever reached through a Table field on a parent.
    is_child_table: bool = False
    # "series" -> "<PREFIX>-<YYYY>-<####>"; "field" -> named by one field;
    # "prompt" -> the user types the name; "random" -> a hash.
    naming: Literal["series", "field", "prompt", "random"] = "series"
    series_prefix: str | None = None
    naming_field: str | None = None
    fields: list[FieldSpec] = Field(default_factory=list)
    permissions: list[PermissionSpec] = Field(default_factory=list)
    title_field: str | None = None


class CustomFieldSpec(_Model):
    doctype: str
    field: FieldSpec


class PropertySetterSpec(_Model):
    doctype: str
    fieldname: str
    property: PropertyName
    value: str


class StandardDoctypeMods(_Model):
    custom_fields: list[CustomFieldSpec] = Field(default_factory=list)
    property_setters: list[PropertySetterSpec] = Field(default_factory=list)


class WorkflowStateSpec(_Model):
    name: str
    # 0 = draft/editable, 1 = submitted, 2 = cancelled.
    doc_status: Literal[0, 1, 2] = 0
    allow_edit_role: str = "System Manager"


class WorkflowTransitionSpec(_Model):
    from_state: str
    action: str
    to_state: str
    allowed_role: str


class WorkflowSpec(_Model):
    name: str
    doctype: str
    states: list[WorkflowStateSpec] = Field(default_factory=list)
    transitions: list[WorkflowTransitionSpec] = Field(default_factory=list)


class WorkspaceShortcutSpec(_Model):
    label: str
    doctype: str


class NumberCardSpec(_Model):
    label: str
    doctype: str
    # Equality filters only: {"status": "Open"}. Anything richer is a
    # planner reaching past what the applier can verify.
    filters: dict[str, str] = Field(default_factory=dict)


class WorkspaceSpec(_Model):
    title: str
    shortcuts: list[WorkspaceShortcutSpec] = Field(default_factory=list)
    number_cards: list[NumberCardSpec] = Field(default_factory=list)


class NotificationSpec(_Model):
    name: str
    doctype: str
    event: NotificationEvent
    subject: str
    message: str = ""
    recipient_roles: list[str] = Field(default_factory=list)
    # Required when event == "Value Change".
    value_changed: str | None = None


class SeedRow(_Model):
    doctype: str
    values: dict[str, str | int | float | bool]
    # Which value keys identify the row. This is the idempotency key: a
    # re-run finds the row by these and skips it instead of duplicating it.
    match_on: list[str] = Field(min_length=1)


class UnsupportedRequest(_Model):
    request: str
    reason: str
    # "tier2" = needs custom code (a dedicated stack); "out_of_scope" = not
    # something this product builds. Surfaced to the customer, never dropped.
    kind: Literal["tier2", "out_of_scope"] = "out_of_scope"


class CrmSpec(_Model):
    meta: Meta
    roles: list[RoleSpec] = Field(default_factory=list)
    doctypes: list[DoctypeSpec] = Field(default_factory=list)
    standard_doctype_mods: StandardDoctypeMods = Field(default_factory=StandardDoctypeMods)
    workflows: list[WorkflowSpec] = Field(default_factory=list)
    workspaces: list[WorkspaceSpec] = Field(default_factory=list)
    notifications: list[NotificationSpec] = Field(default_factory=list)
    seed_data: list[SeedRow] = Field(default_factory=list)
    unsupported_requests: list[UnsupportedRequest] = Field(default_factory=list)


def spec_json_schema() -> dict:
    """The schema handed to the planner. Generated, never hand-written."""
    return CrmSpec.model_json_schema()
