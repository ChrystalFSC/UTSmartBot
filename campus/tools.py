"""Tools the chat model can call for exact answers: staff lookup, programme finder, fee estimator.

Search passages are good for "tell me about..." questions, but contacts and fees
should come from structured data so numbers are exact and calculations are done
in code rather than by the model.
"""
import json
import math
import re
from difflib import SequenceMatcher

from . import PROGRAMMES_PATH, STAFF_PATH

PROGRAMMES = json.loads(PROGRAMMES_PATH.read_text(encoding="utf-8"))
STAFF = json.loads(STAFF_PATH.read_text(encoding="utf-8"))

# The UTS fee schedule has three columns for undergraduates: Sarawak, West Malaysia & Sabah
# (other Malaysians) and international.
STUDENT_TYPES = ["sarawakian", "malaysian", "international"]
STUDENT_TYPE_ALIASES = {
    "local": "malaysian", "west_malaysian": "malaysian", "sabahan": "malaysian", "non_sarawakian": "malaysian",
    "sarawakian_b40": "sarawakian",  # the old 80% B40 bursary is no longer listed
}
LEVELS = ["foundation", "undergraduate", "master", "phd"]
ONE_OFF_REGISTRATION = 350  # includes the RM250 refundable security deposit
RECURRING_REGISTRATION = 150  # per semester, student insurance and resources
SEMESTERS_PER_YEAR = 2  # long semesters
FTES_NOTE = (
    "This is an FTES programme. Under the Sarawak Free Tertiary Education Scheme (FTES), eligible Sarawakians pay "
    "no tuition: it is for a first bachelor's degree, studied full-time, by students who hold no other scholarship. "
    "Students from households with a monthly per capita income of RM1,500 or less can also get an RM15,000/year "
    "stipend. Registration fees are listed separately and may still apply. A Sarawakian who is not eligible "
    "should ask UTS which fee applies."
)
SOURCE_NOTE ="Figures are estimates from the UTS fee schedule and are subject to change; confirm with the UTS Marketing Office (084-367300)."

STOPWORDS = {
    "who", "is", "the", "of", "a", "an", "and", "for", "in", "at", "to", "what", "whats", "email", "phone",
    "number", "contact", "extension", "ext", "uts", "please", "me", "give", "tell", "about", "can", "i", "how",
    "do", "does", "reach", "call", "find", "person", "staff", "my", "s",
}

# Everyday words students use -> words that appear in programme names or schools.
INTEREST_TERMS = {
    "computer": ["computer", "computing", "game", "digital"], "computing": ["computer", "computing"],
    "it": ["computer", "computing"], "programming": ["computer", "computing", "game"],
    "coding": ["computer", "computing", "game"], "software": ["computer", "computing"],
    "ai": ["computer", "computing"], "data": ["computer", "computing"], "cyber": ["computer", "computing"],
    "game": ["game"], "games": ["game"], "gaming": ["game"],
    "design": ["design", "interior", "industrial", "media", "architecture"], "art": ["art", "design", "media"],
    "arts": ["art", "design", "media"], "creative": ["creative", "media", "design", "game"],
    "media": ["media"], "animation": ["media", "game"], "multimedia": ["media"],
    "business": ["business", "marketing", "accountancy", "management"], "money": ["accountancy", "business"],
    "finance": ["accountancy", "business"], "accounting": ["accountancy"], "marketing": ["marketing"],
    "management": ["management"], "engineering": ["engineering"], "engineer": ["engineering"],
    "electrical": ["electrical"], "electronics": ["electrical"], "mechanical": ["mechanical"],
    "civil": ["civil"], "construction": ["construction", "civil", "surveying", "property"],
    "building": ["construction", "architecture", "surveying", "property", "civil"],
    "architecture": ["architecture"], "property": ["property"], "surveying": ["surveying"],
    "food": ["food"], "science": ["science"], "safety": ["safety"], "health": ["health", "safety"],
    "technology": ["technology"],
}

QUALIFICATION_TERMS = {
    "spm": ["SPM"], "stpm": ["STPM"], "uec": ["UEC"], "a-level": ["A-level", "A-Level", "GCE"],
    "alevel": ["A-level", "A-Level", "GCE"], "o-level": ["O-level"], "diploma": ["Diploma"],
    "foundation": ["Foundation"], "matriculation": ["Matriculation"], "matrikulasi": ["Matriculation"],
    "stam": ["STAM"], "dvm": ["DVM", "Diploma Vokasional"], "dkm": ["DKM"], "degree": ["Bachelor"],
    "bachelor": ["Bachelor"], "pre-u": ["Pre-U"],
}


def tokens(text):
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in STOPWORDS]


def normalize_programme(name):
    name = name.lower().replace("&", "and")
    name = re.sub(r"\((?:hons|honours)\)|\bhonours\b|\bhons\b|\(phd\)", " ", name)
    name = re.sub(r"\barts?\b", "art", name)
    return " ".join(w for w in re.findall(r"[a-z]+", name) if w not in ("of", "in", "the"))


# ------------------------------------------------------------- programmes

def level_hint(words):
    if words & {"phd", "doctor", "doctorate", "philosophy"}:
        return "phd"
    if words & {"master", "masters", "msc", "mba"}:
        return "master"
    if "foundation" in words:
        return "foundation"
    if words & {"bachelor", "degree", "undergraduate"}:
        return "undergraduate"
    return None


def stem(word):
    return word[:-1] if len(word) > 4 and word.endswith("s") else word


def resolve_programme(query):
    """Match 'BCS', 'computer science' or a full name to programmes, best first."""
    q = query.strip()
    exact = [p for p in PROGRAMMES if p["code"] and p["code"].lower() == q.lower()]
    if exact:
        return exact
    nq = normalize_programme(q)
    q_tokens = set(nq.split())
    level = level_hint(q_tokens)
    scored = []
    for p in PROGRAMMES:
        if level and p["level"] != level:
            continue
        name = normalize_programme(p["name"])
        overlap = len(q_tokens & set(name.split())) / max(len(q_tokens), 1)
        score = max(SequenceMatcher(None, nq, name).ratio(), overlap * 0.95)
        # "computer science" should prefer the bachelor over the PhD unless the level is named.
        if not level and p["level"] == "undergraduate":
            score += 0.05
        scored.append((score, p))
    scored.sort(key=lambda s: -s[0])
    return [p for score, p in scored if score >= 0.6][:5]


def normalize_student_type(student_type):
    student_type = (student_type or "").strip().lower().replace(" ", "_")
    student_type = STUDENT_TYPE_ALIASES.get(student_type, student_type)
    return student_type if student_type in STUDENT_TYPES else None


def fee_summary(programme, student_type):
    """Fees for one student type, or for every type when student_type is None."""
    if student_type is None:
        return {t: fee_summary(programme, t) for t in STUDENT_TYPES}
    fees = programme["fees"]
    level = programme["level"]
    if student_type == "international":
        intl = fees.get("international")
        return dict(intl) if intl else None
    if level in ("master", "phd"):
        local = fees.get("local", {})
        summary = {"full_time_total": local.get("full_time"), "part_time_total": local.get("part_time")}
        if student_type == "sarawakian":
            summary["full_time_total_with_uts_scholarship"] = local.get("full_time_scholarship")
            summary["part_time_total_with_uts_scholarship"] = local.get("part_time_scholarship")
        return {k: v for k, v in summary.items() if v is not None}
    if level == "foundation":
        return dict(fees.get("local") or {}) or None  # same fee for every Malaysian
    summary = dict(fees.get(student_type) or {})
    if summary and fees.get("actual"):
        summary["full_fee_before_bursary"] = fees["actual"].get("total")
    return summary or None


def per_year(programme, student_type):
    summary = fee_summary(programme, student_type) or {}
    if "per_year" in summary:
        return summary["per_year"]
    total = summary.get("total") or summary.get("full_time_total")
    duration = programme.get("duration_years")
    return round(total / duration) if total and duration else None


def requirement_excerpt(text, qualification_words):
    """Only the requirement options that mention the student's qualification."""
    if not text:
        return None
    lines = [l for l in text.split("\n") if l.strip().lower() not in ("or", "and", "or;")]
    if not qualification_words:
        return "\n".join(lines)
    hits = [l for l in lines if any(w.lower() in l.lower() for w in qualification_words)]
    return "\n".join(hits) if hits else None


def find_programmes(level=None, interest=None, school=None, student_type=None, max_fee_per_year=None,
                    qualification=None, name=None, limit=8):
    """Filter and rank UTS programmes. See TOOL_SCHEMAS for the parameters."""
    student_type = normalize_student_type(student_type)
    budget_type = student_type or "malaysian"  # a budget needs one fee column to compare
    candidates = resolve_programme(name) if name else list(PROGRAMMES)
    if level in LEVELS:
        candidates = [p for p in candidates if p["level"] == level]
    if school:
        school_tokens = set(tokens(school)) - {"school"}
        candidates = [p for p in candidates if school_tokens & set(tokens(p["school"] or ""))]

    qualification_words = []
    if qualification:
        for word in re.findall(r"[a-z\-]+", qualification.lower()):
            qualification_words += QUALIFICATION_TERMS.get(word, [])
        qualification_words = qualification_words or [qualification]

    scored = []
    for p in candidates:
        score = 0.0
        if interest:
            haystack = set(tokens(f"{p['name']} {p['school']}"))
            for word in tokens(interest):
                terms = INTEREST_TERMS.get(word) or INTEREST_TERMS.get(stem(word)) or [stem(word)]
                score += sum(1 for t in terms if any(h.startswith(t) or (len(h) >= 4 and t.startswith(h))
                                                     for h in haystack))
            if score == 0:
                continue
        fee = per_year(p, budget_type)
        if max_fee_per_year and (fee is None or fee > max_fee_per_year):
            continue
        scored.append((score, p, fee))
    # "What courses does UTS offer?": no filter that narrows the list, so return the whole
    # catalogue in a compact form, in level order, instead of the first few programmes.
    catalogue = not (name or interest or qualification or max_fee_per_year)
    if catalogue:
        scored.sort(key=lambda s: (LEVELS.index(s[1]["level"]), s[1]["school"] or "", s[1]["name"]))
        return {
            "count": len(scored),
            "programmes": [{"name": p["name"], "code": p["code"], "level": p["level"], "school": p["school"],
                            "duration_years": p["duration_years"]} for _, p, _ in scored],
            "note": "This is the full catalogue. Use estimate_fees or find_programmes with a name for fees and "
                    "entry requirements of a specific programme.",
        }
    scored.sort(key=lambda s: (-s[0], s[2] or math.inf))

    results = []
    detailed = len(scored) <= 2  # full requirements when the question is about one or two programmes
    for _, p, fee in scored[:limit]:
        item = {
            "name": p["name"], "code": p["code"], "level": p["level"], "school": p["school"],
            "duration_years": p["duration_years"], "credits": p["credits"], "ftes_programme": p.get("ftes", False),
            "fees": fee_summary(p, student_type), f"approx_fee_per_year_{budget_type}": fee,
        }
        if qualification_words or detailed:
            item["entry_requirements"] = (
                requirement_excerpt(p["entry_requirements"], qualification_words)
                or ("No entry requirements listed for this programme; check with UTS." if not p["entry_requirements"]
                    else f"No requirement option mentions {qualification}; other routes may apply.")
            )
        results.append(item)
    return {
        "student_type": student_type or "all (sarawakian / malaysian = West Malaysia & Sabah / international)",
        "count": len(results),
        "programmes": results,
        "note": "Fees are from the current UTS fee schedule. ftes_programme = free tuition for eligible Sarawakians. "
                "Entry requirements come from older (2024-2025) UTS information and are subject to change by the "
                "UTS Senate, MQA and the Ministry of Higher Education. " + SOURCE_NOTE,
    }


def estimate_fees(programme, student_type="malaysian", study_mode="full_time", uts_scholarship=False):
    """Estimate the total cost of a programme, including registration fees."""
    matches = resolve_programme(programme)
    if not matches:
        return {"error": f"No UTS programme matches {programme!r}.",
                "available_programmes": [p["name"] for p in PROGRAMMES]}
    p = matches[0]
    student_type = normalize_student_type(student_type) or "malaysian"
    duration = p["duration_years"]
    notes = []
    result = {"programme": p["name"], "code": p["code"], "level": p["level"], "student_type": student_type,
              "duration_years": duration}

    if p["level"] in ("master", "phd"):
        if student_type == "international":
            tuition = (p["fees"].get("international") or {}).get("total")
            notes.append("International postgraduate fees are listed as estimated totals.")
        else:
            mode = "part_time" if study_mode == "part_time" else "full_time"
            scholarship = uts_scholarship or student_type == "sarawakian"
            key = f"{mode}_scholarship" if scholarship else mode
            tuition = p["fees"]["local"].get(key)
            result["study_mode"] = mode
            result["uts_scholarship"] = scholarship
            if scholarship:
                notes.append("The UTS Scholarship is only for Sarawak citizens and only within the minimum study duration.")
            if tuition is None:
                notes.append(f"No {mode.replace('_', '-')} fee is listed for this programme.")
        notes.append("Postgraduate fees already include the Study Resources Fee (RM100/semester) and the RM250 "
                     "refundable security deposit, but exclude research expenditure and re-examination fees.")
        result["tuition_total"] = tuition
        result["estimated_total"] = tuition
    else:
        summary = fee_summary(p, student_type) or {}
        tuition = summary.get("total")
        if tuition is None and summary.get("per_year") and duration:
            tuition = round(summary["per_year"] * duration)
            notes.append(f"UTS lists this fee per year; the total is RM{summary['per_year']:,} x {duration:g} year(s).")
        if p["level"] == "foundation" and student_type != "international":
            notes.append("Full sponsorship of foundation tuition fees is available for qualified Malaysian students "
                         "(subject to terms and conditions).")
        if p["level"] == "undergraduate" and student_type != "international":
            result["full_fee_before_bursary"] = summary.get("full_fee_before_bursary")
            result["fee_scheme"] = summary.get("scheme")
            if student_type == "sarawakian" and p.get("ftes"):
                result["ftes"] = FTES_NOTE
            elif student_type == "sarawakian":
                notes.append("This is not an FTES programme, so Sarawakians pay the fee after the 50% UTS Bursary Scheme.")
            else:
                notes.append(f"Fee for students from West Malaysia & Sabah, after the {summary.get('scheme')}.")
        semesters = math.ceil((duration or 1) * SEMESTERS_PER_YEAR)
        registration = ONE_OFF_REGISTRATION + RECURRING_REGISTRATION * semesters
        result.update({
            "tuition_total": tuition,
            "tuition_per_year": summary.get("per_year"),
            "registration_fees": {
                "one_off": ONE_OFF_REGISTRATION,
                "per_semester": RECURRING_REGISTRATION,
                "semesters": semesters,
                "total": registration,
            },
            "estimated_total": tuition + registration if tuition is not None else None,
        })
        notes.append("The one-off RM350 registration fee includes a RM250 security deposit that is refundable "
                     "when you leave UTS (terms apply).")
        notes.append("Actual fees per semester depend on the credits taken; tuition can be paid by instalment "
                     "(conditions apply) and PTPTN loans are available.")
    if result.get("tuition_total") is None:
        notes.append("No fee is listed for this combination; ask the UTS Marketing Office (084-367300).")
    if len(matches) > 1 and normalize_programme(matches[0]["name"]) != normalize_programme(programme):
        result["other_matches"] = [m["name"] for m in matches[1:4]]
    result["notes"] = notes + [SOURCE_NOTE]
    return result


# ------------------------------------------------------------------ staff

STAFF_ABBREVIATIONS = {
    "hop": ["head", "programme"], "vc": ["vice", "chancellor"], "dvc": ["deputy", "vice", "chancellor"],
    "hod": ["head", "department"], "program": ["programme"], "lecturers": ["lecturer"],
}


def expand_staff_query(query):
    """'hop of bcs' -> head, programme, bachelor, computer, science."""
    expanded = []
    for t in tokens(query):
        code = next((p for p in PROGRAMMES if (p["code"] or "").lower() == t), None)
        if code:
            expanded += [w for w in tokens(code["name"]) if w not in ("hons", "honours")]
        else:
            expanded += STAFF_ABBREVIATIONS.get(t, [t])
    return list(dict.fromkeys(expanded))


def lookup_staff(query, limit=5):
    """Find staff by name, role, department or research area."""
    q_tokens = expand_staff_query(query)
    if not q_tokens:
        return {"error": "Please give a name, role or department to search for."}
    scored = []
    for person in STAFF:
        name_tokens = tokens(person["name"])
        role_text = " ".join(f"{r.get('position', '')} {r.get('department', '')} {r.get('unit', '')}"
                             for r in person["roles"])
        role_tokens = set(tokens(role_text))
        # Roles from the website's management page are current; others come from older data.
        current_tokens = set(tokens(" ".join(f"{r.get('position', '')} {r.get('department', '')}"
                                             for r in person["roles"] if "current" in r.get("source", ""))))
        research_tokens = set(tokens(person.get("research_interests") or ""))
        score = 0.0
        for t in q_tokens:
            if any(n == t for n in name_tokens):
                score += 3
            elif len(t) >= 4 and any(n.startswith(t) or SequenceMatcher(None, n, t).ratio() > 0.85 for n in name_tokens):
                score += 2  # tolerate typos / partial names
            if t in role_tokens or (len(t) >= 4 and any(r.startswith(t) for r in role_tokens)):
                score += 1.5
                if t in current_tokens:
                    score += 1  # "who is the bursar" should find today's bursar first
            if t in research_tokens:
                score += 0.5
        if score and any(tokens(r.get("position", "")) == q_tokens for r in person["roles"]):
            score += 2  # the exact title ("vice chancellor") beats a longer one ("deputy vice chancellor")
        if score:
            # Prefer people on the current UTS pages, then those with contact details.
            score += 0.5 * bool(person.get("on_current_website")) + 0.3 * bool(person["email"]) \
                + 0.2 * bool(person["phone"])
            scored.append((score, person))
    scored.sort(key=lambda s: -s[0])
    if not scored:
        return {"matches": [], "note": "No staff member matched. The full directory is at https://directory.uts.edu.my/"}
    best = scored[0][0]
    matches = [
        {k: v for k, v in person.items() if v is not None and v != [] and v != ""}
        for score, person in scored[:limit] if score >= best * 0.6
    ]
    result = {}
    # A name search ("elton wong") whose words don't all appear in any name only found partial matches.
    role_words = set(tokens(" ".join(r.get("position", "") for p in STAFF for r in p["roles"])))
    if not set(q_tokens) & role_words and not any(set(q_tokens) <= set(tokens(m["name"])) for m in matches):
        result["name_match"] = ("No staff member has all of these words in their name; the matches below are only "
                                "partial and are probably not the person asked about.")
    return {
        **result,
        "matches": matches,
        "note": "Each role has a source: roles marked current come from the UTS website (management team and "
                "school staff pages, October 2026); other roles come from older 2024-2025 data and may no longer be "
                "true. Prefer current roles. If a person has on_current_website=false, they are not listed on any "
                "current UTS page and may have left or changed role: say so. Emails hidden on the school pages are "
                "not available; point to https://directory.uts.edu.my/ for those. Phone numbers built from an "
                "extension use the UTS prefix 084-367.",
    }


# ---------------------------------------------------------- declarations

TOOLS = {
    "lookup_staff": lookup_staff,
    "find_programmes": find_programmes,
    "estimate_fees": estimate_fees,
}

TOOL_LABELS = {
    "lookup_staff": "Staff directory",
    "find_programmes": "Programme finder",
    "estimate_fees": "Fee estimator",
}

STUDENT_TYPE_SCHEMA = {
    "type": "string",
    "enum": STUDENT_TYPES,
    "description": "sarawakian = from Sarawak (FTES programmes are free for eligible Sarawakians, others have a "
                   "50% bursary); malaysian = Malaysian from West Malaysia or Sabah (30% or 25% bursary); "
                   "international = non-Malaysian. Leave it out in find_programmes to get all three.",
}

# Plain JSON Schema, so every LLM provider can use the same definitions
# (Gemini via parameters_json_schema, OpenAI-compatible APIs as "function" tools).
TOOL_SCHEMAS = [
    dict(
        name="lookup_staff",
        description="Look up current UTS staff contact details (email, phone, extension), positions and departments. "
                    "Call it for every question about a person, contact or 'who is the ...', e.g. 'email of Dr X', "
                    "'phone number of the IT manager', 'who is the dean of computing', 'who handles hostel matters'. "
                    "The reference passages can name former staff, so call this even if they seem to answer.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name, role, department or research area, e.g. "
                                                           "'Alan Ting', 'dean computing', 'hostel warden'."},
            },
            "required": ["query"],
        },
    ),
    dict(
        name="find_programmes",
        description="Search the current UTS programme catalogue (foundation, bachelor, master, PhD) with fees and entry "
                    "requirements. Call it whenever you list, recommend or filter programmes, e.g. 'which degrees does "
                    "the School of Business offer' (pass school), 'which programmes suit me', 'what can I study with "
                    "STPM' (pass qualification), 'courses under RM10,000 a year' (pass max_fee_per_year and "
                    "student_type), or details of a specific programme. The reference passages list only some "
                    "programmes, so never build a list from them.",
        parameters={
            "type": "object",
            "properties": {
                "level": {"type": "string", "enum": LEVELS},
                "interest": {"type": "string", "description": "Subject area or hobby, e.g. 'computers', 'design', 'business'."},
                "school": {"type": "string", "description": "UTS school, e.g. 'Business and Management', "
                                                            "'Computing & Creative Media', 'Engineering and Technology', "
                                                            "'Built Environment'."},
                "student_type": STUDENT_TYPE_SCHEMA,
                "max_fee_per_year": {"type": "number", "description": "Budget in RM per year."},
                "qualification": {"type": "string", "description": "Student's qualification, e.g. 'STPM', 'UEC', "
                                                                   "'SPM', 'Diploma', 'Foundation', 'A-level'."},
                "name": {"type": "string", "description": "A specific programme name or code, e.g. 'BCS'."},
            },
        },
    ),
    dict(
        name="estimate_fees",
        description="Calculate the estimated cost of a UTS programme for a student type, including registration "
                    "fees, bursary schemes, part-time study and postgraduate scholarships. Call it for every fee, "
                    "tuition or cost question about a programme, even if the reference passages show a fee: they "
                    "leave out registration fees and may show a different student type's rate.",
        parameters={
            "type": "object",
            "properties": {
                "programme": {"type": "string", "description": "Programme name or code, e.g. 'BCS', 'Master of Engineering'."},
                "student_type": STUDENT_TYPE_SCHEMA,
                "study_mode": {"type": "string", "enum": ["full_time", "part_time"],
                               "description": "Postgraduate only. Default full_time."},
                "uts_scholarship": {"type": "boolean", "description": "Postgraduate only: apply the UTS Scholarship "
                                                                    "(Sarawak citizens)."},
            },
            "required": ["programme"],
        },
    ),
]


def run_tool(name, args):
    """Run a tool by name with model-supplied arguments, never raising."""
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"Unknown tool {name}"}
    try:
        return tool(**{k: v for k, v in (args or {}).items() if v is not None})
    except TypeError as error:
        return {"error": f"Invalid arguments for {name}: {error}"}
