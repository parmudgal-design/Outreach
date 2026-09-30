"""Draft generation.

`ClaudeDrafter` calls the Claude API with structured output. `TemplateDrafter`
is the keyless fallback: it fills a fixed story-led template so the whole
pipeline can be exercised before an API key is added. Drafts record which one
wrote them, and the UI labels template drafts clearly.
"""
import json
import re
from dataclasses import dataclass

import anthropic

from . import prompts, specialties

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["subject", "body"],
    "additionalProperties": False,
}


class DraftError(Exception):
    """A draft could not be produced. `retryable` tells the worker whether to try again later."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Draft:
    subject: str
    body: str
    drafted_by: str


class ClaudeDrafter:
    name = "claude"

    def __init__(self, api_key: str, model: str = "claude-opus-5-5", effort: str = "medium"):
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=120)
        self.model = model
        self.effort = effort

    def _complete(self, user_text: str) -> dict:
        kwargs = dict(
            model=self.model,
            max_tokens=16000,
            system=prompts.SYSTEM,
            messages=[{"role": "user", "content": user_text}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": DRAFT_SCHEMA}},
        )
        try:
            # Server-side fallback re-runs a safety-classifier decline on the recommended model.
            response = self.client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
            )
        except anthropic.AuthenticationError as e:
            raise DraftError("Anthropic API key was rejected — check it under Connections.") from e
        except anthropic.PermissionDeniedError as e:
            raise DraftError(f"This API key can't use {self.model}.") from e
        except anthropic.NotFoundError as e:
            raise DraftError(f"Model {self.model} wasn't found — pick another under Connections.") from e
        except anthropic.RateLimitError as e:
            raise DraftError("Rate limited by the Anthropic API — will retry later.", retryable=True) from e
        except anthropic.BadRequestError as e:
            raise DraftError(f"Anthropic API rejected the request: {e.message}") from e
        except anthropic.APIStatusError as e:
            raise DraftError(f"Anthropic API error {e.status_code} — will retry later.", retryable=e.status_code >= 500) from e
        except anthropic.APIConnectionError as e:
            raise DraftError("Couldn't reach the Anthropic API — will retry later.", retryable=True) from e

        if response.stop_reason == "refusal":
            raise DraftError("Claude declined to write this email. Edit the contact notes or write it by hand.")
        if response.stop_reason == "max_tokens":
            raise DraftError("The draft was cut off — try again.", retryable=True)
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise DraftError("Claude returned an unreadable draft — try again.", retryable=True) from e
        return data

    def initial(self, contact: dict, sender: dict, pitches: dict) -> Draft:
        data = self._complete(prompts.initial_request(contact, sender, pitches))
        return Draft(data["subject"].strip(), data["body"].strip(), self.name)

    def followup(self, contact: dict, sender: dict, pitches: dict, previous: dict, number: int) -> Draft:
        data = self._complete(prompts.followup_request(contact, sender, pitches, previous, number))
        return Draft(previous["subject"], data["body"].strip(), self.name)

    def ping(self) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
        )
        return next((b.text for b in response.content if b.type == "text"), "").strip()


# --- Keyless template drafter ------------------------------------------------
# Same shape as the Claude prompt: a scene from the reader's world, the turn,
# and Dr.Tracker only in the final paragraph as an invitation to come on board.

def _first_name(name: str) -> str:
    clean = re.sub(r"^(mr\.?|ms\.?|mrs\.?|prof\.?)\s+", "", (name or "").strip(), flags=re.I)
    parts = clean.split()
    return parts[0] if parts else "there"


def _surname(name: str) -> str:
    clean = re.sub(r"^(dr\.?|mr\.?|ms\.?|mrs\.?|prof\.?)\s+", "", (name or "").strip(), flags=re.I)
    clean = re.split(r",", clean)[0]  # drop trailing degrees: "Rahul Nair, MS (Ortho)"
    parts = clean.split()
    return parts[-1] if parts else "there"


DEFAULT_OFFER = {
    "doctor": "That's the gap Dr.Tracker was built for: your {audience} check in between visits — {watch} — and you get a quiet flag when something needs your eyes. No new dashboard, no extra admin, and every clinical call stays yours.",
    "hospital": "Dr.Tracker gives doctors across your departments a simple way to stay in touch with discharged patients — short check-ins at home and a quiet flag when something needs a look — without adding a task to a nurse's or doctor's day.",
    "company": "Dr.Tracker gives the doctors in your network a simple way to stay connected to their patients between appointments — quick check-ins, readings, and a quiet flag when something needs attention.",
    "partner": "Dr.Tracker is where doctors stay connected to their patients between visits. The doctors on it decide for themselves what their patients use — what a partnership could offer is a clean way for readings from {brand} to reach the doctor who is already following that patient.",
}
ASK = {
    "doctor": "We'd love to have you on board. Could I show you how it would work with your own {audience} — 15 minutes, no slides, a live walkthrough on your phone? Would a day this week suit you?",
    "hospital": "We'd love {org} to help shape a small pilot. Could we have 20 minutes with you and your clinical leadership to walk through what it could look like?",
    "company": "We'd love to explore bringing it to {org}'s doctors together. Could we have 20 minutes with your clinical or partnerships leadership? I'll bring the specifics, you bring the tough questions.",
    "partner": "Could we find 20 minutes this week to see whether {brand} and Dr.Tracker fit together?",
}
PS = {
    "doctor": "P.S. If a call feels like a big ask right now, just reply \"video\" and I'll send a 2-minute look instead.",
    "hospital": "P.S. Happy to start with a one-page pilot outline if that's easier to circulate internally.",
    "company": "P.S. Happy to start with a short async demo video if that's easier to route internally.",
    "partner": "P.S. Happy to share which specialties on Dr.Tracker fit {brand} most naturally, if that's useful before we talk.",
}
ORG_SCENES = {
    "hospital": (
        ["What happens after they leave your wards?", "Discharged on Friday, quiet by Monday", "The follow-up call nobody had time for"],
        "A patient is discharged from {org} on a Friday. By Monday something isn't quite right — but she doesn't call. Not because she doesn't trust your hospital, but because she assumes it's nothing, or doesn't want to bother anyone.",
        "Every hospital carries this quietly — in doctors' evenings spent chasing patients on WhatsApp, in follow-up calls that mostly go unanswered, and in not having a clear answer when someone asks how you follow up after discharge.",
    ),
    "company": (
        ["Who notices when a patient goes quiet?", "The patient in your network who didn't call", "What your network can't see between visits"],
        "Right now, somewhere in {org}'s network, a patient is sitting with a symptom they're not sure is worth mentioning. Most will wait. A few will wait too long — and when they do, it isn't only their health on the line. It's whether they still feel {org} was watching.",
        "That gap between visits is invisible today. Almost every network has it, and the doctors inside it feel it most — they want to follow up, and they simply don't have the hours.",
    ),
    "partner": (
        ["What happens to a {brand} reading at 9 pm?", "The {brand} reading nobody looked at", "Great data, landing nowhere"],
        "It's 9 pm. A patient takes a reading on her {product}, looks at the number, and isn't sure whether it's good or bad. She closes the app. Nobody else ever sees it.",
        "You know this better than anyone: home-health data is only as valuable as the doctor who acts on it. The products patients stick with are the ones their own doctor asks about at the next visit — the ones that sit inside a care plan, not beside it.",
    ),
}


class TemplateDrafter:
    name = "template"

    def initial(self, contact: dict, sender: dict, pitches: dict) -> Draft:
        ctype = contact.get("type") or "doctor"
        seed = (contact.get("email") or contact.get("name") or "")
        is_dr = (contact.get("name") or "").lower().startswith("dr")
        product = (contact.get("product") or contact.get("clinic") or "your product").strip()
        # Full product name once, in the scene; the brand's short name everywhere after.
        brand = (contact.get("clinic") or product.split()[0] if contact.get("product") else product).strip()
        org = contact.get("clinic") or "your organisation"
        pitch = ((pitches.get(ctype) or {}).get("pitch") or "").strip()

        if ctype == "doctor":
            pb = specialties.playbook(contact.get("specialty_key"))
            i = specialties.pick_index(len(pb.moments), seed)
            subject = pb.subjects[i] if i < len(pb.subjects) else pb.subjects[0]
            moment = pb.moments[i]
            opening = "Picture this: " + moment[0].lower() + moment[1:]
            turn = specialties.pick(pb.gaps, seed, "g")
            fmt = {"audience": pb.audience, "watch": pb.watch}
            greeting = f"{'Dear' if sender.get('tone') == 'formal' else 'Namaste'} Dr. {_surname(contact.get('name'))},"
        else:
            subjects, opening, turn = ORG_SCENES[ctype]
            fmt = {"org": org, "product": product, "brand": brand}
            subject = specialties.pick(subjects, seed, "s").format(**fmt)
            opening, turn = opening.format(**fmt), turn.format(**fmt)
            greeting = f"Dear Dr. {_surname(contact.get('name'))}," if is_dr else f"Dear {_first_name(contact.get('name'))},"

        fmt.update({"org": org, "product": product, "brand": brand})
        offer = (pitch or DEFAULT_OFFER[ctype].format(**fmt)) + " " + ASK[ctype].format(**fmt)
        body = "\n\n".join([greeting, opening, turn, offer, prompts.signoff_block(sender), PS[ctype].format(**fmt)])
        return Draft(subject, body, self.name)

    def followup(self, contact: dict, sender: dict, pitches: dict, previous: dict, number: int) -> Draft:
        is_dr = (contact.get("type") or "doctor") == "doctor" or (contact.get("name") or "").lower().startswith("dr")
        salutation = f"{'Dr.' if is_dr else 'Hi'} {_surname(contact.get('name'))},"
        body = (
            f"{salutation}\n\n"
            "I know inboxes are relentless, so I'll keep this short.\n\n"
            "If a call isn't the right format, I'm happy to send a 2-minute video instead — "
            "and if the timing just isn't right, a one-line \"not now\" is completely fine; I won't follow up again.\n\n"
            f"{prompts.signoff_block(sender)}"
        )
        return Draft(previous["subject"], body, self.name)


def get_drafter(ai_settings: dict):
    key = (ai_settings.get("anthropic_api_key") or "").strip()
    if key:
        return ClaudeDrafter(key, ai_settings.get("model") or "claude-opus-5-5", ai_settings.get("effort") or "medium")
    return TemplateDrafter()
