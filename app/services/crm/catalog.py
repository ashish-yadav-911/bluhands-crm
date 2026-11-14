"""The closed vocabulary a CRM spec may use.

One file, read by both the spec validator and (later) the planner's prompt, so
what the model is told and what is accepted can never drift apart. Everything
here exists to turn a class of LLM mistakes into a structural impossibility
rather than something discovered when a form fails to render.
"""

from __future__ import annotations

# Field types a planner may choose. Deliberately excluded:
#   Button, Fold, Heading, Image, HTML  -- each needs coupling to another
#       field or to server code to be valid, which is exactly what an LLM
#       gets wrong.
#   Dynamic Link                        -- needs a paired Link-to-DocType field.
#   Section Break / Column Break        -- layout only; Frappe renders fields
#       in order without them, and a wrong break hides fields.
FIELD_TYPES = (
    "Data",
    "Small Text",
    "Text",
    "Long Text",
    "Text Editor",
    "Int",
    "Float",
    "Currency",
    "Percent",
    "Check",
    "Date",
    "Datetime",
    "Time",
    "Select",
    "Link",
    "Table",
    "Phone",
    "Attach",
    "Rating",
)

# Field types that carry a doctype name in `options`.
DOCTYPE_REFERENCE_TYPES = ("Link", "Table")

# Standard doctypes a Link field may point at. Closed on purpose: this makes
# "no broken links" a structural guarantee -- a link target is either a
# doctype defined in the same spec or one of these, all of which exist on
# every ERPNext site once the setup wizard has run.
STANDARD_LINK_TARGETS = (
    "Address",
    "Company",
    "Contact",
    "Country",
    "Currency",
    "Customer",
    "Customer Group",
    "Employee",
    "Item",
    "Item Group",
    "Lead",
    "Opportunity",
    "Project",
    "Supplier",
    "Task",
    "Territory",
    "UOM",
    "User",
)

# Standard doctypes a spec may add custom fields to or change properties of.
STANDARD_MODIFIABLE_DOCTYPES = (
    "Address",
    "Contact",
    "Customer",
    "Item",
    "Lead",
    "Opportunity",
    "Project",
    "Supplier",
    "Task",
)

# Roles that exist on every site; a spec referencing anything else must
# declare it under `roles` (the normalizer adds the declaration for it).
STANDARD_ROLES = (
    "Accounts Manager",
    "Accounts User",
    "All",
    "Employee",
    "HR Manager",
    "HR User",
    "Projects Manager",
    "Projects User",
    "Sales Manager",
    "Sales User",
    "Stock Manager",
    "Stock User",
    "Support Team",
    "System Manager",
)

# Property Setter properties a spec may change on a standard field, with the
# Frappe property_type each needs.
PROPERTY_SETTER_TYPES = {
    "hidden": "Check",
    "reqd": "Check",
    "read_only": "Check",
    "in_list_view": "Check",
    "label": "Data",
    "default": "Text",
    "options": "Text",
}

NOTIFICATION_EVENTS = ("New", "Save", "Submit", "Cancel", "Value Change")

# Frappe's own column and bookkeeping names. A custom field with one of these
# either fails or silently shadows core behaviour.
RESERVED_FIELDNAMES = frozenset(
    {
        "name",
        "owner",
        "creation",
        "modified",
        "modified_by",
        "docstatus",
        "idx",
        "parent",
        "parenttype",
        "parentfield",
        "doctype",
        "amended_from",
        "workflow_state",
        "_assign",
        "_comments",
        "_liked_by",
        "_user_tags",
    }
)

# Custom fields added to *standard* doctypes must carry this prefix so they
# can never collide with a core fieldname, now or after an ERPNext upgrade.
CUSTOM_FIELD_PREFIX = "cf_"

# MariaDB table names are `tab<doctype>` and capped at 64 characters.
MAX_DOCTYPE_NAME_LENGTH = 61
MAX_FIELDNAME_LENGTH = 50

# Budget caps keep a build inside the "minutes" promise and inside what a
# reviewing customer can actually read.
MAX_DOCTYPES = 12
MAX_FIELDS_PER_DOCTYPE = 30
MAX_WORKFLOWS = 4
MAX_SEED_ROWS = 25
