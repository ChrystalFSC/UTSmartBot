"""Turn UTS information into structured JSON for the tools.

Usage:
    python -m campus.extract

Sources:
- Fees and the management team: the UTS website snapshot in knowledge/uts_website/
  (tuitionfees.html, management.html; refresh with python -m rag.fetch)
- Programme codes, entry requirements and other staff: the older plain-text
  knowledge base, knowledge/UTS_knowledge_base.md

Writes campus/data/programmes.json and campus/data/staff.json, and prints
anything it could not match so it can be checked by hand. Fees and contacts
are copied exactly from the sources; nothing is invented here.
"""
import json
import math
import re
from difflib import SequenceMatcher

from bs4 import BeautifulSoup

from rag import KNOWLEDGE_DIR
from rag.documents import is_heading, load_file

from . import DATA_DIR, PROGRAMMES_PATH, STAFF_PATH

SOURCE = KNOWLEDGE_DIR / "UTS_knowledge_base.md"
WEBSITE_DIR = KNOWLEDGE_DIR / "uts_website"
FEES_PAGE = WEBSITE_DIR / "tuitionfees.html"
MANAGEMENT_PAGE = WEBSITE_DIR / "management.html"
KNOWLEDGE_BASE_SOURCE = "UTS knowledge base (2024-2025 data)"
EMAIL_DOMAIN = "uts.edu.my"
PHONE_PREFIX = "084-367"

ITEM_RE = re.compile(r"^\s*(?:\d{1,2}\.|[ivx]+\)\.?|[a-z]\))\s*(.+?)\s*$", re.IGNORECASE)
PROGRAMME_RE = re.compile(r"\b(Foundation in|Bachelor|Master|Doctor of Philosophy)\b")
MONEY_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")


# ---------------------------------------------------------------- helpers

def find_line(lines, text, start=0):
    for i in range(start, len(lines)):
        if lines[i].strip() == text:
            return i
    raise ValueError(f"Section {text!r} not found in the knowledge base")


def money(text):
    if "NIL" in text.upper():
        return None
    match = MONEY_RE.search(text.split(":", 1)[-1])
    return int(match.group(1).replace(",", "")) if match else None


def number(text):
    match = re.search(r"\d+(?:\.\d+)?", text.split(":", 1)[-1])
    return float(match.group()) if match else None


def normalize_name(name):
    """'Bachelor of Art (Hons) Interior Design' and 'Bachelor of Arts (Hons) in
    Interior Design' should compare equal."""
    name = name.lower().replace("&", "and")
    name = re.sub(r"\((?:hons|honours|hons\.)\)|\bhonours\b|\bhons\b", " ", name)
    name = re.sub(r"\(by (coursework|research)\)", " ", name)
    name = re.sub(r"\[.*?\]|\(phd\)", " ", name)
    name = re.sub(r"\barts?\b", "art", name)
    name = re.sub(r"\b(in|of|the)\b", " ", name)
    return " ".join(re.sub(r"[^a-z ]", " ", name).split())


def similarity(a, b):
    a, b = normalize_name(a), normalize_name(b)
    if a == b:
        return 1.0
    if set(a.split()) == set(b.split()):
        return 0.99
    return SequenceMatcher(None, a, b).ratio()


def best_match(name, candidates, threshold=0.85):
    # Only compare like with like: "Master of Science in Architecture" must never
    # pick up the fees of "Bachelor of Science in Architecture".
    level = level_of(name)
    scored = sorted(((similarity(name, c), c) for c in candidates if level_of(c) == level), reverse=True)
    return scored[0][1] if scored and scored[0][0] >= threshold else None


def level_of(name):
    lowered = name.lower()
    if lowered.startswith("foundation"):
        return "foundation"
    if lowered.startswith("bachelor"):
        return "undergraduate"
    if lowered.startswith("master"):
        return "master"
    return "phd"


# ------------------------------------------------------------- programmes

def parse_catalogue(lines):
    """'Programmes Offer' list: names, codes and schools."""
    start = find_line(lines, "Programmes Offer")
    end = find_line(lines, "Schools", start)
    catalogue, school = {}, None
    for line in lines[start + 1:end]:
        text = line.strip().lstrip("•?\t -").strip()
        if text.startswith("School of"):
            school = text
            continue
        match = re.match(r"(.+?)\s*\(([A-Z]{2,4})\)$", text)
        if match and PROGRAMME_RE.search(match.group(1)):
            name, code = match.groups()
            level = level_of(name)
            catalogue[name] = {
                "code": code,
                "school": school if level in ("undergraduate",) else
                ("School of Foundation Studies" if level == "foundation" else "School of Postgraduate Studies"),
            }
    return catalogue


def cells(row):
    return [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]


def amount(text):
    """'72,395' -> 72395, '0' -> 0, 'NIL' or '' -> None."""
    if not text or "NIL" in text.upper():
        return None
    match = MONEY_RE.search(text)
    return int(match.group(1).replace(",", "")) if match else None


def parse_website_fees(html):
    """Read the fee tables on https://www.uts.edu.my/tuitionfees.

    Returns (local, international): programme name -> fee details. Tables are
    recognised by their headings rather than their order on the page.
    """
    soup = BeautifulSoup(html, "html.parser")
    local, international = {}, {}
    for table in soup.find_all("table"):
        rows = [cells(r) for r in table.find_all("tr")]
        heading = " ".join(" ".join(r) for r in rows[:3]).lower()
        if "actual local fee" in heading:
            parse_undergraduate_table(rows, local)
        elif "under uts scholarship" in heading:
            for row in rows:
                if len(row) == 5 and PROGRAMME_RE.search(row[0]):
                    local[row[0]] = {"fees": {"local": {
                        "full_time": amount(row[1]), "full_time_scholarship": amount(row[2]),
                        "part_time": amount(row[3]), "part_time_scholarship": amount(row[4]),
                    }}}
        elif "duration (years)" in heading:
            for row in rows:
                if len(row) == 3 and PROGRAMME_RE.search(row[0]):
                    key = "total" if "total fees" in heading else "per_year"
                    international[row[0]] = {"duration_years": number(row[1]), key: amount(row[2])}
        elif "total fees per year" in heading:  # local foundation
            for row in rows:
                if len(row) == 2 and PROGRAMME_RE.search(row[0]):
                    local[row[0]] = {"fees": {"local": {"per_year": amount(row[1])}}}
    return local, international


def parse_undergraduate_table(rows, local):
    """Columns: actual fee, Sarawak, West Malaysia & Sabah (each total and per year).

    Group rows say whether the programmes below are FTES programmes (free for
    eligible Sarawakians) and which UTS bursary rate applies to each column.
    """
    ftes, sarawak_scheme, malaysian_scheme = False, None, None
    for row in rows:
        label = row[0].upper() if row else ""
        if label.startswith(("FTES PROGRAMMES", "NON FTES PROGRAMMES")):
            ftes = label.startswith("FTES")
            schemes = [c for c in row[1:] if c]
            if ftes:
                sarawak_scheme, malaysian_scheme = "FTES", (schemes[-1] if schemes else None)
            else:
                sarawak_scheme, malaysian_scheme = (schemes + [None, None])[:2]
            continue
        if len(row) == 9 and PROGRAMME_RE.search(row[0]):
            local[row[0]] = {
                "credits": int(number(row[1])),
                "duration_years": number(row[2]),
                "ftes": ftes,
                "fees": {
                    "actual": {"total": amount(row[3]), "per_year": amount(row[4])},
                    "sarawakian": {"total": amount(row[5]), "per_year": amount(row[6]), "scheme": sarawak_scheme},
                    "malaysian": {"total": amount(row[7]), "per_year": amount(row[8]), "scheme": malaysian_scheme},
                },
            }


def parse_requirements(lines):
    """Entry requirements are kept as the official text; eligibility rules are
    too varied to encode safely, so the model explains them with a caveat."""
    start = find_line(lines, "Entry Qualification")
    end = next(i for i in range(start, len(lines)) if lines[i].strip().lower().startswith("tuition fees"))
    requirements, names, block = {}, [], []

    def flush():
        text = "\n".join(l for l in block if l.strip()).strip()
        for name in names:
            requirements[name] = text

    for line in lines[start + 1:end]:
        text = line.strip()
        # A line can introduce several programmes: "1. Bachelor of A 2. Bachelor of B"
        found = re.findall(r"\d{1,2}\.\s+((?:Foundation in|Bachelor|Master|Doctor)[^0-9]*?)(?=\s+\d{1,2}\.\s+|\s*$)", text)
        if found and re.match(r"^\d{1,2}\.\s+", text):
            flush()
            names, block = [f.strip() for f in found], []
        elif text in ("Foundation", "Undergraduate", "Postgraduate"):
            continue
        elif names:
            block.append(text)
    flush()
    return requirements


SCHOOL_KEYWORDS = [
    ("School of Engineering and Technology", ("engineering", "food technology", "occupational safety")),
    ("School of Built Environment", ("architecture", "quantity surveying", "interior design", "property", "construction")),
    ("School of Computing & Creative Media", ("computer", "computing", "game", "digital media", "industrial design")),
    ("School of Business and Management", ("business", "accountancy", "marketing", "technology management")),
]


def guess_school(name, level):
    if level == "foundation":
        return "School of Foundation Studies"
    if level in ("master", "phd"):
        return "School of Postgraduate Studies"
    lowered = name.lower()
    for school, words in SCHOOL_KEYWORDS:
        if any(w in lowered for w in words):
            return school
    return None


def build_programmes(lines):
    """Fees come from the website snapshot; codes and entry requirements from the knowledge base."""
    catalogue = parse_catalogue(lines)
    requirements = parse_requirements(lines)
    local, international = parse_website_fees(FEES_PAGE.read_text(encoding="utf-8"))
    problems = []

    programmes = []
    for name, fee in local.items():
        level = level_of(name)
        info = catalogue.get(best_match(name, catalogue) or "", {})
        intl = international.get(best_match(name, international) or "", {})
        req_name = best_match(name, requirements)
        programme = {
            "name": re.sub(r"\s*\(by (Coursework|Research)\)", "", name).strip(),
            "code": info.get("code"),
            "level": level,
            "school": info.get("school") or guess_school(name, level),
            "mode": "coursework" if "coursework" in name.lower() else None,
            "duration_years": fee.get("duration_years") or intl.get("duration_years"),
            "credits": fee.get("credits"),
            "ftes": fee.get("ftes", False),
            "fees": fee["fees"],
            # Entry requirements on the website load by script, so they still come from the
            # (older) knowledge base and may have changed.
            "entry_requirements": requirements.get(req_name),
        }
        if intl:
            # Only what UTS publishes; the fee estimator does any multiplication and says so.
            programme["fees"]["international"] = {
                k: v for k, v in (("per_year", intl.get("per_year")), ("total", intl.get("total"))) if v is not None
            }
        programmes.append(programme)

        if not info:
            problems.append(f"no code/school in the programme list for: {name}")
        if not req_name:
            problems.append(f"no entry requirements for: {name}")
    for name in international:
        if not best_match(name, local):
            problems.append(f"international fee without local entry: {name}")
    return programmes, problems


# ------------------------------------------------------------------ staff

FIELD_RE = re.compile(
    r"^\s*(Position|E-?mail|Email|Extension|Ext|EXT|Phone|Tel|Telephone|Contact Number|Qualifications|"
    r"Research Interest|Research Interests|Specialisations|Research Specialisations)\s*\.?\s*:\s*(.*)$",
    re.IGNORECASE,
)
TITLE_RE = re.compile(r"^(Mr|Ms|Mdm|Mrs|Miss|Dr|Prof|Assoc|Associate|Ts|Sr|Ir|Datuk|Dato|Puan|Encik)\b\.?", re.IGNORECASE)


def field_key(label):
    label = label.lower().replace("-", "")
    if label.startswith("position"):
        return "position"
    if label.startswith("email"):
        return "email"
    if label in ("extension", "ext"):
        return "extension"
    if label in ("phone", "tel", "telephone", "contact number"):
        return "phone"
    if label.startswith("qualification"):
        return "qualifications"
    return "research_interests"


def clean_name(line):
    match = ITEM_RE.match(line)
    name = match.group(1) if match else line.strip()
    unit = re.search(r"\(([^()]*(?:Foundation|Science|Arts)[^()]*)\)\s*$", name)
    name = re.sub(r"\s*\([^()]*\)\s*$", "", name) if unit else name
    # Drop post-nominal credentials: "Mr Jimmy De Rozario, CHRC, CHRA" -> "Mr Jimmy De Rozario"
    name = re.sub(r",\s*[A-Z]{2,}(?:\s*\([^)]*\))?(?:,\s*[A-Z]{2,})*\s*$", "", name)
    return name.strip(" .:"), (unit.group(1) if unit else None)


DEGREE_RE = re.compile(r"\b(Bachelor|Master|PhD|Ph\.D|MSc|BSc|B\.|M\.|Diploma|Dip\.|Degree|MBA|Certificate)\b")
UNIT_RE = re.compile(r"\b(Centre|Center|Department|Office|School of|Unit|Council|Team|Library|Registry)\b")
ROLE_PREFIXES = ("acting", "deputy", "chief", "senior")  # "Acting Dean, School of ..." is a role, not a unit
ROLE_WORDS = ("dean", "director", "head", "chancellor", "bursar", "librarian", "manager", "executive",
              "lecturer", "clerk", "officer", "coordinator", "assistant", "warden", "registrar", "technologist",
              "researcher", "president", "professor")


def looks_like_role(line):
    text = line.strip()
    lowered = text.lower()
    return any(w in lowered for w in ROLE_WORDS) and not TITLE_RE.match(text) and not looks_like_unit(text)


def looks_like_unit(line):
    text = ITEM_RE.match(line.strip()).group(1) if ITEM_RE.match(line.strip()) else line.strip()
    text = text.rstrip(":").strip()
    if ":" in text and not text.lower().startswith("unit:"):  # a "Field: value" line, not a heading
        return False
    first_word = text.split()[0].lower().strip(",.") if text.split() else ""
    return (bool(UNIT_RE.search(text)) and len(text) <= 70 and not text.endswith(".")
            and "floor" not in text.lower() and first_word not in ROLE_WORDS + ROLE_PREFIXES
            and not text.lower().startswith(("the ", "head ", "for ", "please")))


# Team headings in the school pages use the school's abbreviation.
TEAM_NAMES = {
    "SCM": "School of Computing & Creative Media",
    "SBE": "School of Built Environment",
    "SBM": "School of Business and Management",
    "SET": "School of Engineering and Technology",
    "SFS": "School of Foundation Studies",
    "SPS": "School of Postgraduate Studies",
}


def looks_like_name(line):
    text = line.strip()
    if not text or ":" in text or len(text) > 80 or text.endswith(".") or text[0] in "•?-*·":
        return False
    name, _ = clean_name(text)
    words = name.split()
    if not 2 <= len(words) <= 9 or DEGREE_RE.search(name) or looks_like_unit(text):
        return False
    connectors = {"bin", "binti", "bt", "bte", "a/l", "a/p", "ak", "anak", "de", "van", "@"}
    return bool(TITLE_RE.match(name)) or name.isupper() or all(
        w[0].isupper() for w in words if w[0].isalpha() and w.lower() not in connectors)


GENERIC_UNITS = {"Management Team", "Administrative Team", "Team Members", "Staff"}


def department_above(lines, i, limit=400):
    """Nearest organisational unit above a record, e.g. 'Information Technology Department'.

    Generic headings ("Management Team" inside a school page) are qualified
    with the unit above them, e.g. "School of Foundation Studies (SFS) › Management Team".
    """
    for j in range(i, max(-1, i - limit), -1):
        if looks_like_unit(lines[j]):
            text = lines[j].strip()
            match = ITEM_RE.match(text)
            text = (match.group(1) if match else text).rstrip(":").strip()
            text = re.sub(r"^Unit:\s*", "", text)
            team = re.match(r"^([A-Z]{3}) Team$", text)
            text = TEAM_NAMES.get(team.group(1), text) if team else text
            if text in GENERIC_UNITS and j > 0:
                parent = department_above(lines, j - 1, limit=600)
                return f"{parent} › {text}" if parent and parent not in GENERIC_UNITS else text
            return text
    return None


def clean_role(text):
    match = ITEM_RE.match(text.strip())
    return (match.group(1) if match else text.strip()).rstrip(":").strip()


def parse_staff(lines):
    """A staff record is a name line followed by 'Field: value' lines.

    The management team lists the role first, then the name, then
    qualifications, so the name is the nearest name-like line above the fields.
    """
    records = []
    i = 0
    while i < len(lines):
        if not FIELD_RE.match(lines[i]):
            i += 1
            continue
        # Collect the block of field lines (allowing one free-text line, e.g. qualifications).
        start = i
        fields, j = {}, i
        while j < len(lines) and (FIELD_RE.match(lines[j]) or (
                j + 1 < len(lines) and FIELD_RE.match(lines[j + 1]) and not looks_like_name(lines[j]))):
            match = FIELD_RE.match(lines[j])
            if match:
                key, value = field_key(match.group(1)), match.group(2).strip()
                if value:
                    fields.setdefault(key, value)
            j += 1
        # Find the person's name above the block (skipping a qualifications line).
        name_at = None
        for k in range(start - 1, max(-1, start - 4), -1):
            if looks_like_name(lines[k]) and not looks_like_role(lines[k]):
                name_at = k
                break
        if name_at is not None and ("email" in fields or "extension" in fields or "phone" in fields or "position" in fields):
            name, unit = clean_name(lines[name_at])
            if "position" not in fields and name_at > 0 and looks_like_role(lines[name_at - 1]):
                fields["position"] = lines[name_at - 1]  # role-first layout: "Bursar" / name / ...
            if "qualifications" not in fields and name_at + 1 < start and DEGREE_RE.search(lines[name_at + 1]):
                fields["qualifications"] = lines[name_at + 1].strip()
            records.append({"name": name, "unit": unit, "line": name_at, **fields})
        i = max(j, i + 1)

    records += parse_management(MANAGEMENT_PAGE)
    records += parse_school_pages(WEBSITE_DIR)

    staff = {}
    for record in records:
        email = record.get("email", "").strip().rstrip(".;").lower()
        if email and "@" not in email:
            email = f"{email}@{EMAIL_DOMAIN}"
        if email and not re.match(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$", email):
            email = ""
        phone = record.get("phone")
        extension = re.sub(r"\D", "", record.get("extension", "")) or None
        if not phone and extension:
            phone = f"{PHONE_PREFIX}{extension}"
        department = record.get("department") or department_above(lines, record["line"])
        # Mark where each role comes from, so current website roles can be preferred.
        if not record.get("current"):
            source = KNOWLEDGE_BASE_SOURCE
        elif record.get("department") == "Management Team":
            source = "UTS website, management team (current)"
        else:
            source = f"{record.get('source')} staff page (current)"
        position = clean_role(record["position"]) if record.get("position") else None
        role = {k: v for k, v in (("position", position), ("department", department),
                                  ("unit", record.get("unit")), ("source", source)) if v}

        key = email or normalize_person(record["name"])
        person = staff.setdefault(key, {"name": record["name"], "email": email or None, "phone": phone,
                                        "extension": extension, "roles": [], "qualifications": None,
                                        "research_interests": None})
        if len(record["name"]) > len(person["name"]):
            person["name"] = record["name"]  # keep the fullest form of the name
        for k, v in (("phone", phone), ("extension", extension), ("qualifications", record.get("qualifications")),
                     ("research_interests", record.get("research_interests"))):
            if v and not person.get(k):
                person[k] = v
        if record.get("current") and (phone or extension):
            person["phone"], person["extension"] = phone, extension  # current pages beat old numbers
            person["phone_is_current"] = True
        if role and role not in person["roles"]:
            person["roles"].append(role)

    # Someone listed without an email in one section (e.g. as a researcher) is
    # usually listed with one elsewhere: fold those entries into the full record.
    by_name = {normalize_person(p["name"]): p for p in staff.values() if p["email"]}
    merged = []
    for person in staff.values():
        target = by_name.get(normalize_person(person["name"])) if not person["email"] else None
        if target is None:
            merged.append(person)
            continue
        for role in person["roles"]:
            if role not in target["roles"]:
                target["roles"].append(role)
        for k in ("phone", "extension", "qualifications", "research_interests"):
            target[k] = target[k] or person[k]
        if person.get("phone_is_current") and not target.get("phone_is_current"):
            target["phone"], target["extension"] = person["phone"], person["extension"]
            target["phone_is_current"] = True
    for person in merged:
        person.pop("phone_is_current", None)
        # People missing from every current UTS page may have left or changed roles.
        person["on_current_website"] = any("current" in r.get("source", "") for r in person["roles"])
    return merged


def parse_management(path):
    """The website's management team page: role / name / qualifications, without field labels.

    These roles are marked current, because the rest of the staff data comes from
    the older knowledge base and some roles there have changed (e.g. the Vice Chancellor).
    """
    if not path.exists():
        return []
    doc = load_file(path)
    lines = [l.strip() for l in doc.text.split("\n") if l.strip()]
    records = []
    for i in range(len(lines) - 1):
        role, name_line = lines[i], lines[i + 1]
        # A heading such as "Vice Chancellor" is followed by the role itself, which is not a name.
        if (looks_like_role(role) or role.lower() in MANAGEMENT_ONLY_ROLES) and looks_like_name(name_line) \
                and not looks_like_role(name_line) and name_line.lower() != "to be appointed":
            name, unit = clean_name(name_line)
            record = {"name": name, "unit": unit, "line": None, "position": role,
                      "department": "Management Team", "current": True, "source": doc.url}
            if i + 2 < len(lines) and DEGREE_RE.search(lines[i + 2]):
                record["qualifications"] = lines[i + 2]
            records.append(record)
    return records


MANAGEMENT_ONLY_ROLES = {"auditor"}

# Staff pages saved from the school and department sites (see knowledge/sources.txt).
SITE_DEPARTMENTS = {
    "scm": "School of Computing & Creative Media",
    "set": "School of Engineering and Technology",
    "sbm": "School of Business and Management",
    "sbe": "School of Built Environment",
    "sfs": "School of Foundation Studies",
    "sps": "School of Postgraduate Studies",
    "sdsc": "Student Development & Services Centre (SDSC)",
    "cir": "Centre for International Relations (CIR)",
    "itd": "Information Technology Department",
    "arc": "Academic Resource Centre (ARC)",
}
PHONE_RE = re.compile(r"(?:\(?\+?6\)?\s*)?0\d{1,2}\s*[-–?]?\s*\d{3}\s*[-–?]?\s*\d{3,4}")
EXT_RE = re.compile(r"\bext\.?\s*:?\s*(\d{3})\b", re.IGNORECASE)
EMAIL_NAME_RE = re.compile(r"\bemail\s*:\s*([a-z0-9][\w.]*)(?:@uts\.edu\.my)?\b", re.IGNORECASE)
FIELD_LABELS = ("position", "extension", "email", "qualification", "qualifications", "phone")


def school_page_lines(doc):
    """Lines of a staff page, with SET's split fields ("Position" / ": Lecturer") joined up."""
    lines = [l.strip() for l in doc.text.split("\n") if l.strip()]
    joined = []
    for line in lines:
        if line.startswith(":") and joined and joined[-1].lower() in FIELD_LABELS:
            joined[-1] = f"{joined[-1]}{line}"
        else:
            joined.append(line)
    return joined


def parse_school_pages(website_dir):
    """Current staff lists from the school sites: a name line, then a role, then contacts.

    Layouts differ (SCM: name/role/phone, SBM: name/role/"EXT 400", SFS: "Ext. 635 |
    email: x", SET: "Position: ..." fields, SBE: name/role only), but all list a name
    followed by its role. Emails shown as "[email protected]" are hidden by the site
    and are not decoded.
    """
    records = []
    for path in sorted(website_dir.glob("*.html")):
        site = path.stem.split("_", 1)[0]
        if site not in SITE_DEPARTMENTS:
            continue
        doc = load_file(path)
        lines = school_page_lines(doc)
        starts = []
        for i in range(len(lines) - 1):
            name, role = lines[i], lines[i + 1]
            if role.lower().startswith("position:"):
                role = role.split(":", 1)[1].strip()
            if looks_like_name(name) and not looks_like_role(name) and name.lower() != "vacant" \
                    and role and (looks_like_role(role) or role.lower().startswith(("head", "dean"))):
                starts.append((i, role))
        for n, (i, role) in enumerate(starts):
            block = lines[i + 2: starts[n + 1][0] if n + 1 < len(starts) else min(len(lines), i + 10)]
            text = " | ".join(block)
            name, unit = clean_name(lines[i])
            record = {"name": name, "unit": unit, "line": None, "position": role,
                      "department": SITE_DEPARTMENTS[site], "current": True, "source": doc.url}
            if (match := EXT_RE.search(text)) or (match := re.search(r"Extension\s*:\s*(\d{3})", text)):
                record["extension"] = match.group(1)
            if match := PHONE_RE.search(text):
                record["phone"] = re.sub(r"\s*[–?]\s*", "-", match.group(0)).strip()
            if (match := EMAIL_NAME_RE.search(text)) and "protected" not in match.group(1).lower():
                record["email"] = match.group(1)
            if match := re.search(r"Qualifications?\s*:\s*([^|]+)", text):
                record["qualifications"] = match.group(1).strip()
            records.append(record)
    return records


def normalize_person(name):
    """'Mr Shukri Abdul Rahman' and 'SHUKRI BIN ABDUL RAHMAN' -> 'shukri abdul rahman'."""
    name = re.sub(r"\(.*?\)", " ", name)
    words = re.sub(r"[^a-z ]", " ", name.lower()).split()
    titles = {"mr", "ms", "mdm", "mrs", "miss", "dr", "prof", "assoc", "associate", "professor", "ts", "sr", "ir",
              "datuk", "dato", "puan", "encik"}
    patronymic = {"bin", "binti", "bt", "bte", "b", "ak", "anak"}
    # "Mohd Shahril" and "Mohammad Shahril" are the same person written two ways.
    same_name = {"mohd": "muhammad", "muhd": "muhammad", "mohamad": "muhammad", "mohammad": "muhammad",
                 "mohammed": "muhammad", "mohamed": "muhammad", "muhamad": "muhammad"}
    return " ".join(same_name.get(w, w) for w in words if w not in titles | patronymic)


# ------------------------------------------------------------------- main

def main():
    lines = SOURCE.read_text(encoding="utf-8").split("\n")
    programmes, problems = build_programmes(lines)
    staff = parse_staff(lines)

    DATA_DIR.mkdir(exist_ok=True)
    PROGRAMMES_PATH.write_text(json.dumps(programmes, ensure_ascii=False, indent=2), encoding="utf-8")
    STAFF_PATH.write_text(json.dumps(staff, ensure_ascii=False, indent=2), encoding="utf-8")

    write_summaries(programmes, parse_management(MANAGEMENT_PAGE))

    print(f"{len(programmes)} programmes -> {PROGRAMMES_PATH.name}")
    print(f"{len(staff)} staff -> {STAFF_PATH.name}")
    print(f"summaries -> {FEES_SUMMARY.relative_to(KNOWLEDGE_DIR)}, {MANAGEMENT_SUMMARY.relative_to(KNOWLEDGE_DIR)}")
    for problem in problems:
        print("check:", problem)


# ------------------------------------------------------------- summaries

FEES_SUMMARY = WEBSITE_DIR / "summary_tuition_fees.md"
MANAGEMENT_SUMMARY = WEBSITE_DIR / "summary_management_team.md"


def rm(value):
    return f"RM{value:,}" if value is not None else "not listed"


def write_summaries(programmes, management):
    """Plain sentences for search: table cells and role/name lists are hard to retrieve,
    while "Students from Sabah pay RM36,708 ..." matches the questions people ask."""
    lines = ["Source: https://www.uts.edu.my/tuitionfees",
             "UTS tuition fees by programme (summary of the UTS fee schedule)", ""]
    for p in programmes:
        f = p["fees"]
        intl = f.get("international", {})
        intl_text = (f"International students: {rm(intl['per_year'])} per year." if "per_year" in intl
                     else f"International students: {rm(intl['total'])} in total." if "total" in intl
                     else "No international fee is listed.")
        if p["level"] == "undergraduate":
            s, m, a = f["sarawakian"], f["malaysian"], f["actual"]
            sarawak = ("Sarawakian students pay RM0 tuition under the Sarawak Free Tertiary Education Scheme (FTES) "
                       "if eligible" if p["ftes"] else
                       f"Sarawakian students pay {rm(s['total'])} in total ({rm(s['per_year'])} per year) after the "
                       f"{s['scheme']}; this is not an FTES programme")
            lines.append(
                f"{p['name']} ({p['school']}, {p['credits']} credits, {p['duration_years']:g} years): "
                f"full fee {rm(a['total'])} ({rm(a['per_year'])} per year). {sarawak}. Students from West Malaysia and "
                f"Sabah pay {rm(m['total'])} in total ({rm(m['per_year'])} per year) after the {m['scheme']}. {intl_text}")
        elif p["level"] == "foundation":
            lines.append(f"{p['name']} (foundation, 1 year): {rm(f['local']['per_year'])} per year for Malaysian "
                         f"students; full sponsorship is available for qualified Malaysian students. {intl_text}")
        else:
            local = f["local"]
            lines.append(
                f"{p['name']} (postgraduate): full time {rm(local.get('full_time'))} "
                f"({rm(local.get('full_time_scholarship'))} under the UTS Scholarship for Sarawak citizens); "
                f"part time {rm(local.get('part_time'))} ({rm(local.get('part_time_scholarship'))} under the "
                f"UTS Scholarship). {intl_text}")
        lines.append("")
    FEES_SUMMARY.write_text("\n".join(lines), encoding="utf-8")

    lines = ["Source: https://www.uts.edu.my/management",
             "UTS management team (current, from the UTS website)", ""]
    for record in management:
        sentence = f"The {record['position']} of UTS is {record['name']}."
        if record.get("qualifications"):
            sentence += f" Qualifications: {record['qualifications']}."
        lines += [sentence, ""]
    MANAGEMENT_SUMMARY.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
