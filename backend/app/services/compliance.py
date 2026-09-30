"""The footer every outgoing email carries.

India has no CAN-SPAM equivalent for email, but the IT Act 2000 (no false
identity) and the DPDP Act 2023 (a clear way to withdraw consent and to reach
the sender about their data) apply. The footer is appended at send time and is
never part of the editable draft, so it can't be deleted by accident.
"""


def footer(sender: dict, from_email: str = "") -> str:
    who = ", ".join(x for x in (sender.get("name"), sender.get("role")) if x) or "the Dr.Tracker team"
    email = from_email or sender.get("email") or ""
    lines = ["---", f"Sent by {who}" + (f" <{email}>" if email else "")]
    if sender.get("address"):
        lines.append(sender["address"])
    lines.append("Don't want emails like this from Dr.Tracker? Reply \"unsubscribe\" and we'll stop.")
    return "\n".join(lines)


def full_body(body: str, sender: dict, from_email: str = "") -> str:
    return body.rstrip() + "\n\n" + footer(sender, from_email)
