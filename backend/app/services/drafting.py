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

from .. import settings_store
from . import greeting, marketplace, prompts, qa, specialties, spend, templates

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["subject", "body"],
    "additionalProperties": False,
}


GUIDED_SCHEMA = {
    "type": "object",
    "properties": {
        "skip_reason": {"type": "string"},
        "subject": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["skip_reason", "subject", "body"],
    "additionalProperties": False,
}


REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": prompts.REPLY_INTENTS},
        "summary": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["intent", "summary", "body"],
    "additionalProperties": False,
}


class DraftError(Exception):
    """A draft could not be produced. `retryable` tells the worker whether to try again later."""

    def __init__(self, message: str, retryable: bool = False, skip: bool = False):
        super().__init__(message)
        self.retryable = retryable
        # The guide says this row shouldn't be emailed (a reseller, a prescription product…): not retried.
        self.skip = skip


@dataclass
class Draft:
    subject: str
    body: str
    drafted_by: str


@dataclass
class ReplyDraft:
    intent: str
    summary: str
    body: str
    drafted_by: str


class ClaudeDrafter:
    name = "claude"

    def __init__(self, api_key: str, model: str = "claude-opus-5-5", effort: str = "medium"):
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=120)
        self.model = model
        self.effort = effort

    def _complete(self, user_text: str, system: str = prompts.SYSTEM, schema: dict = DRAFT_SCHEMA) -> dict:
        kwargs = dict(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": user_text}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
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
            retryable = e.status_code >= 500
            raise DraftError(f"Anthropic API error {e.status_code}" + (" — will retry later." if retryable else "."), retryable=retryable) from e
        except anthropic.APIConnectionError as e:
            raise DraftError("Couldn't reach the Anthropic API — will retry later.", retryable=True) from e

        if response.stop_reason == "refusal":
            raise DraftError("Claude declined to write this email. Edit the contact notes or write it by hand.")
        if response.stop_reason == "max_tokens":
            raise DraftError("The draft was cut off — try again.")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise DraftError("Claude returned an unreadable draft — try again.") from e
        return data

    def initial(self, contact: dict, sender: dict, pitches: dict, market: dict | None = None) -> Draft:
        guide, _ = templates.guide(contact.get("type") or "")
        if guide:
            if contact.get("type") == "brand" and (reason := guide_stop(contact, _market(market))):
                raise DraftError(f"Not written: {reason}", skip=True)
            text = prompts.guided_initial(contact, sender, _market(market))
            if contact.get("_write_anyway"):
                text += ("\n\nWRITE IT ANYWAY: a person on the team has reviewed this row and overridden the guide's stop rules "
                         "for it (inbox type, fit, anything else). Write the email following the template, and leave "
                         "skip_reason empty.")
            data = self._complete(text, prompts.guided_system(guide), GUIDED_SCHEMA)
            reason = (data.get("skip_reason") or "").strip()
            body_missing = not (data.get("subject") or "").strip() or not (data.get("body") or "").strip()
            if reason and (not contact.get("_write_anyway") or body_missing):
                # Asked to write it anyway and Claude still declined: say why, not "empty draft".
                raise DraftError(f"Not written: {reason}", skip=True)
            if body_missing:
                raise DraftError("Claude returned an empty draft — try again.")
            return Draft(data["subject"].strip(), data["body"].strip(), self.name)
        if contact.get("type") in marketplace.TYPES:
            text = prompts.marketplace_initial(contact, sender, _market(market))
        else:
            text = prompts.initial_request(contact, sender, pitches)
        data = self._complete(text, prompts.system_for(contact))
        return Draft(data["subject"].strip(), data["body"].strip(), self.name)

    def followup(self, contact: dict, sender: dict, pitches: dict, previous: dict, number: int, market: dict | None = None) -> Draft:
        guide, _ = templates.guide(contact.get("type") or "")
        if guide:
            check_touch(number)
            if contact.get("type") == "brand" and (reason := guide_stop({**contact, "_write_anyway": True}, _market(market))):
                raise DraftError(f"Not written: {reason}", skip=True)
            text = prompts.guided_followup(contact, sender, _market(market), previous, number)
            data = self._complete(text, prompts.guided_system(guide), GUIDED_SCHEMA)
            reason = (data.get("skip_reason") or "").strip()
            if reason:
                raise DraftError(f"Not written: {reason}", skip=True)
            if not (data.get("body") or "").strip():
                raise DraftError("Claude returned an empty draft — try again.")
            return Draft(previous["subject"], data["body"].strip(), self.name)
        if contact.get("type") in marketplace.TYPES:
            text = prompts.marketplace_followup(contact, sender, _market(market), previous, number)
        else:
            text = prompts.followup_request(contact, sender, pitches, previous, number)
        data = self._complete(text, prompts.system_for(contact))
        return Draft(previous["subject"], data["body"].strip(), self.name)

    def reply(self, contact: dict, sender: dict, pitches: dict, market: dict | None, ours: dict | None, theirs: dict) -> ReplyDraft:
        text = prompts.reply_request(contact, sender, pitches, _market(market), reply_greeting(contact, sender), ours, theirs)
        data = self._complete(text, prompts.reply_system(contact), REPLY_SCHEMA)
        intent = data.get("intent") if data.get("intent") in prompts.REPLY_INTENTS else "other"
        return ReplyDraft(intent, (data.get("summary") or "").strip(), (data.get("body") or "").strip(), self.name)

    def ping(self) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
        )
        return next((b.text for b in response.content if b.type == "text"), "").strip()


def reply_greeting(contact: dict, sender: dict) -> str:
    if contact.get("type") in marketplace.TYPES:
        return greeting.salutation(greeting.for_contact(contact), sender.get("tone"))
    return legacy_greeting(contact, sender.get("tone"))


def _market(market: dict | None) -> dict:
    return market if market is not None else settings_store.DEFAULTS["marketplace"]


# --- Keyless template drafter ------------------------------------------------
# Same shape as the Claude prompt: a scene from the reader's world, the turn,
# and Dr.Tracker only in the final paragraph as an invitation to come on board.

def legacy_greeting(contact: dict, tone: str = "warm") -> str:
    """Greeting for the original contact types: 'Namaste Dr. Sharma,' / 'Dear Neha,' / 'Hello Navjeevan team,'."""
    who = greeting.for_contact(contact)
    hello = "Dear" if tone == "formal" else "Namaste"
    if (contact.get("type") or "doctor") == "doctor":
        if who.is_person:
            return f"{hello} Dr. {who.last},"
        return f"Hello {who.org} team," if who.kind == "org" else f"{hello} Doctor,"
    return greeting.salutation(who)


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
PARTNER_SUBJECTS_NO_BRAND = ["What happens to a home reading at 9 pm?", "The home reading nobody looked at", "Great data, landing nowhere"]
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


def marketplace_template(contact: dict, sender: dict, market: dict) -> Draft:
    """Brand / professional email: what they do, the moment someone on Dr.Tracker looks for it,
    the value proposition from Setup, what listing means for them, and one ask: a 15-minute call."""
    ctype = contact["type"]
    cat = marketplace.category(contact)
    seed = contact.get("email") or contact.get("name") or ""
    who = greeting.for_contact(contact)
    desc = marketplace.clean_description(contact.get("description"))
    intro = ""
    if desc:
        about = who.org or ("your practice" if ctype == "professional" else "what you make")
        intro = f"I was reading about {about}: {desc}. "
    if ctype == "brand":
        opening = f"{intro}{cat.moment} They're exactly the people {who.org or 'a brand like yours'} wants to reach."
        fit = (f"For {who.org or 'your brand'}, that means being in front of people who {cat.seekers}, "
               "right when they're deciding what to try.")
    else:
        opening = f"{intro}{cat.moment} They're looking for someone like you."
        listed = who.org if who.org and not who.is_person else "your practice"
        fit = f"Listing {listed} means people who {cat.seekers} can find you, and contact or book you directly."
    key = "value_prop_brands" if ctype == "brand" else "value_prop_professionals"
    offer = (market.get(key) or settings_store.DEFAULTS["marketplace"][key]).strip() + " " + fit
    if market.get("mention_trial") and (market.get("trial_text") or "").strip():
        offer += " " + market["trial_text"].strip()
    ask = cat.ask.format(who=who.org or "you")
    body = "\n\n".join([greeting.salutation(who, sender.get("tone")), opening, offer, ask,
                         prompts.marketplace_signoff(sender, market)])
    return Draft(marketplace.choose(cat.subjects, seed, "s"), body, "template")


# --- Brands, keyless: the brand guide's templates (brand_outreach_agent.md), filled in as written ----

# A Hero Product cell made only of these words describes a category, not a product ("Protein / Sports Nutrition").
GENERIC_HERO_WORDS = {
    "supplement", "supplements", "health", "healthy", "nutrition", "nutraceutical", "nutraceuticals", "protein",
    "sports", "wellness", "essential", "oil", "oils", "plant-based", "plant", "based", "ayurveda", "ayurvedic", "food",
    "foods", "snack", "snacks", "products", "product", "range", "device", "devices", "organic", "natural", "herbal",
    "personal", "care", "fitness", "equipment", "and", "&", "items", "beverages", "drinks", "medical", "home", "diet",
}


# A Hero Product cell that describes the business rather than naming a product.
DESCRIPTION_RE = re.compile(r",|\b(products|solutions?|platform|manufactur\w*|services?|treatments?|ingredients?|equipment|"
                            r"items|medicines|range|wellness partner|contract|b2b|wholesale|distribut\w*)\b", re.I)
OFFER_RE = re.compile(r"\b(free|combo|offer|off|deal|sale|buy \d|\d+ ?%)\b", re.I)


def brand_name(name: str) -> str:
    """The name shoppers know: 'Agatsa (Bengaluru Hub)' -> 'Agatsa', 'Alphacentric Healthcare Pvt Ltd' -> 'Alphacentric Healthcare',
    'SUNLEAF Vitamin Gummies & Supplements' -> 'SUNLEAF'."""
    return greeting._org_name(marketplace.shop_name(name)) or (name or "").strip()


def hero_product(cell: str, brand: str) -> str:
    """The first product a shopper would ask for by name, or '' when the cell describes a category."""
    cell = (cell or "").strip()
    if not cell or all(w.strip(".,()") in GENERIC_HERO_WORDS for w in re.split(r"[\s/;,]+", cell.lower()) if w):
        return ""
    first = re.sub(r"\s*\([^)]*\)\s*$", "", re.split(r"\s*[;/]\s*", cell)[0]).strip()
    if not first or len(first) > 60 or DESCRIPTION_RE.search(first) or OFFER_RE.search(first):
        return ""
    words = first.split()
    if len(words) > 3 and not any(w[:1].isupper() or w[:1].isdigit() for w in words[1:]):
        return ""  # "Ayurvedic botanical skin and hair care": a description in sentence case
    if brand and not first.lower().startswith(brand.split()[0].lower()):
        first = f"{brand} {first}"
    return first


# The brand guide (brand_outreach_agent.md) allows three touches: the first email and two follow-ups.
MAX_TOUCHES = 3


def check_touch(number: int) -> None:
    if number + 1 > MAX_TOUCHES:
        raise DraftError(f"Not written: the guide never sends more than {MAX_TOUCHES} emails to a brand.", skip=True)


# The guide's section 3: don't send to "a generic support/complaints inbox". info@, hello@, contact@ and sales@
# are how most of these brands take business mail, so they're fine; the inboxes below go to a ticket queue.
SUPPORT_INBOX_RE = re.compile(
    r"^(support|customer[._-]?support|customer[._-]?care|customercare|customer[._-]?service|care|help|helpdesk|"
    r"complaints?|grievances?|grievance[._-]?officer|feedback|returns|refunds?|no[._-]?reply|donotreply)([._-]\w{0,6})?\d*@", re.I)
# Services, not products a shopper can buy through the chat (they belong in the professionals list).
SERVICE_RE = re.compile(r"\b(hospitals?|clinics?|outpatient|physiotherapy|physio|rehab(ilitation)?|cent(re|er)|dental|"
                        r"personal training|mental health|nursing (home|service)|counsel+ing|helpline|studio|caf[eé]|"
                        r"health services)\b|(?-i:\bOP\b)", re.I)
# Stores and resellers can't put a brand's product first: the brand behind it is who to write to.
STORE_RE = re.compile(r"\b(store|shop|mart|depot|supermarket)\b", re.I)
# The guide's section 7: not a fit.
NOT_A_FIT_RE = re.compile(
    r"\b(alcohol\w*|beer|wines?|whisk(e)?y|vodka|rum|gin|liquor|brewer(y|ies)|distiller(y|ies)|tobacco|cigar(ette)?s?|"
    r"vapes?|vaping|hookah|e-?cig\w*|gambling|betting|casino|prescription|rx[- ]only|schedule h)\b|"
    r"\b(kids?|children'?s?)\b.{0,30}\bweight[- ]loss\b|\bweight[- ]loss\b.{0,30}\b(kids?|children)\b", re.I)


def guide_stop(contact: dict, market: dict) -> str:
    """Why the brand guide's stop rules (sections 3 and 7) say not to write to this brand, or ''."""
    if not (market.get("meeting_link") or "").strip():
        return "no call link yet. Add the meeting link under Setup."
    if marketplace.spend_range(contact.get("spend") or "") is None:
        return "no spend figure for this brand. Add “Spend last quarter (₹ lakhs)”, e.g. 18-25, to the contact."
    if contact.get("_write_anyway"):
        return ""
    if SUPPORT_INBOX_RE.match(contact.get("email") or ""):
        return f"{contact.get('email')} is a support or complaints inbox (the guide says don't send to those). Find a marketing or founder address."
    if (ref := spend.reference(contact)) and ref.get("skip"):
        return ref["skip"]
    if m := STORE_RE.search(contact.get("name") or ""):
        return f"a store or reseller (“{m.group(0)}”) rather than a brand."
    if m := SERVICE_RE.search(contact.get("name") or ""):
        return f"a service (“{m.group(0)}”), not a product shoppers can buy in the chat. It belongs in the professionals list."
    what = " ".join(contact.get(k) or "" for k in ("name", "product", "description"))
    if m := NOT_A_FIT_RE.search(what):
        return f"not a fit for Dr. Tracker (“{m.group(0)}”, the guide's section 7)."
    if m := qa.CURE_RE.search(contact.get("product") or ""):
        return f"the product makes a disease-cure claim (“{m.group(0)}”, the guide's section 7)."
    return ""


def _guide_fill(contact: dict, sender: dict, market: dict) -> dict:
    """The guide's inputs for the template drafter, or a skip when its stop rules say not to send."""
    if reason := guide_stop(contact, market):
        raise DraftError(f"Not written: {reason}", skip=True)
    return prompts.guided_inputs(contact, sender, market, 1)


def brand_guide_template(contact: dict, sender: dict, market: dict) -> Draft:
    """Touch 1, filled in exactly as the guide's template has it."""
    inp = _guide_fill(contact, sender, market)
    brand = inp["brand_name"]
    hero = hero_product(contact.get("product") or "", "")
    if hero and (qa.claim_in(hero) or qa.PRICING_RE.search(hero) or qa.CURE_RE.search(hero)):
        hero = ""  # a product name that would read as a claim or an offer ("Detox Tea")
    low, high = marketplace.lakhs(inp["spend_low_lakhs"]), marketplace.lakhs(inp["spend_high_lakhs"])
    subject = f"{brand}, your marketing bill for ₹{round(inp['spend_high_lakhs']):,} lakhs is ready"
    body = "\n\n".join([
        f"Hi {inp['contact_name'] or brand + ' team'},",
        f"In the last quarter, your estimated range of spend on marketing was ₹{low}–{high} lakhs.",
        f"We don't know how many customers bought {hero or 'your product'} after watching someone dance with it online.",
        templates.screenshot_marker(1),
        "But we can take your product directly into the inbox of your target consumer — the shopper whose diet, "
        "taste and budget already match it.",
        templates.screenshot_marker(2),
        templates.screenshot_marker(3),
        f"Set up a call with Dr. Tracker Bharat: {inp['call_link']}",
        prompts.guided_signoff(sender, market),
    ])
    return Draft(subject, body, "template")


def brand_guide_followup(contact: dict, sender: dict, market: dict, previous: dict, number: int) -> Draft:
    """Touch 2 or 3 from the guide's follow-up sequence."""
    check_touch(number)
    inp = _guide_fill({**contact, "_write_anyway": True}, sender, market)
    hello = f"Hi {inp['contact_name'] or inp['brand_name'] + ' team'},"
    if number == 1:
        low, high = marketplace.lakhs(inp["spend_low_lakhs"]), marketplace.lakhs(inp["spend_high_lakhs"])
        text = (f"Quick nudge on this. One question to think about: of last quarter's ₹{low}–{high} lakhs, how much "
                f"can you tie to an actual order?\n\nWith us, every sale comes through a code. 15 minutes to show you: {inp['call_link']}")
    else:
        text = ("Last note from me. If shoppers asking \"what should I buy?\" in your category is worth reaching, I'd be "
                f"glad to walk you through it: {inp['call_link']}\n\nIf the timing's wrong, just say so and I won't follow up again.")
    return Draft(previous["subject"], "\n\n".join([hello, text, prompts.guided_signoff(sender, market, number + 1)]), "template")


def marketplace_followup_template(contact: dict, sender: dict, market: dict, previous: dict, number: int = 1) -> Draft:
    if templates.guide(contact.get("type") or "")[0] and contact.get("type") == "brand":
        return brand_guide_followup(contact, sender, market, previous, number)
    body = "\n\n".join([
        greeting.salutation(greeting.for_contact(contact), sender.get("tone")),
        "I know inboxes are busy, so I'll keep this short.",
        "If 15 minutes to see how a listing on Dr.Tracker works would be useful, just reply with a time that suits you. "
        "And if the timing isn't right, a one-line \"not now\" is completely fine; I won't follow up again.",
        prompts.marketplace_signoff(sender, market),
    ])
    return Draft(previous["subject"], body, "template")


class TemplateDrafter:
    name = "template"

    def initial(self, contact: dict, sender: dict, pitches: dict, market: dict | None = None) -> Draft:
        ctype = contact.get("type") or "doctor"
        if ctype == "brand" and templates.guide("brand")[0]:
            return brand_guide_template(contact, sender, _market(market))
        if ctype in marketplace.TYPES:
            return marketplace_template(contact, sender, _market(market))
        seed = (contact.get("email") or contact.get("name") or "")
        who = greeting.for_contact(contact)
        # Never fall back to "your product" / "your organisation": rephrase with what we do know.
        org = contact.get("clinic") or (who.org if who.kind == "org" else "") or {"hospital": "your hospital", "company": "your company"}.get(ctype, "")
        product = (contact.get("product") or "").strip()
        brand = (contact.get("clinic") or (product.split()[0] if product else "") or (who.org if who.kind == "org" else "")).strip()
        if not product:
            product = f"{brand} monitor" if brand else "home monitor"
        pitch = ((pitches.get(ctype) or {}).get("pitch") or "").strip()
        desc = marketplace.clean_description(contact.get("description"))
        intro = f"I was reading about {org if org and not org.startswith('your ') else 'your work'}: {desc}.\n\n" if desc else ""

        if ctype == "doctor":
            pb = specialties.playbook(contact.get("specialty_key"))
            i = specialties.pick_index(len(pb.moments), seed)
            subject = pb.subjects[i] if i < len(pb.subjects) else pb.subjects[0]
            moment = pb.moments[i]
            opening = "Picture this: " + moment[0].lower() + moment[1:]
            turn = specialties.pick(pb.gaps, seed, "g")
            fmt = {"audience": pb.audience, "watch": pb.watch}
        else:
            subjects, opening, turn = ORG_SCENES[ctype]
            if ctype == "partner" and not brand:
                subjects, brand = PARTNER_SUBJECTS_NO_BRAND, "your device"
            fmt = {"org": org, "product": product, "brand": brand}
            subject = specialties.pick(subjects, seed, "s").format(**fmt)
            opening, turn = opening.format(**fmt), turn.format(**fmt)
        opening = intro + opening
        greeting_line = legacy_greeting(contact, sender.get("tone"))

        fmt.update({"org": org, "product": product, "brand": brand})
        offer = (pitch or DEFAULT_OFFER[ctype].format(**fmt)) + " " + ASK[ctype].format(**fmt)
        body = "\n\n".join([greeting_line, opening, turn, offer, prompts.signoff_block(sender), PS[ctype].format(**fmt)])
        return Draft(subject, body, self.name)

    def followup(self, contact: dict, sender: dict, pitches: dict, previous: dict, number: int, market: dict | None = None) -> Draft:
        if contact.get("type") in marketplace.TYPES:
            return marketplace_followup_template(contact, sender, _market(market), previous, number)
        body = (
            f"{legacy_greeting(contact, sender.get('tone'))}\n\n"
            "I know inboxes are relentless, so I'll keep this short.\n\n"
            "If a call isn't the right format, I'm happy to send a 2-minute video instead — "
            "and if the timing just isn't right, a one-line \"not now\" is completely fine; I won't follow up again.\n\n"
            f"{prompts.signoff_block(sender)}"
        )
        return Draft(previous["subject"], body, self.name)

    def reply(self, *args, **kwargs) -> None:
        """No suggested responses without Claude: the alert still goes out and a person answers."""
        return None


def get_drafter(ai_settings: dict):
    key = (ai_settings.get("anthropic_api_key") or "").strip()
    if key:
        return ClaudeDrafter(key, ai_settings.get("model") or "claude-opus-5-5", ai_settings.get("effort") or "medium")
    return TemplateDrafter()
